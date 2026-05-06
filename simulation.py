"""TLB Inventory Dispatch Simulation engine.

Implements a 20-day inventory cycle following the TLB dispatch formula:
    ROP = D * (0.75 * TC + LT + LW)
    Trigger when (I + SIT + PO) <= ROP
    Q = min(max(0, AC + D*(LT+LW) - SIT_arrivals_in_window), TC),
        where AC = TC - I and SIT_arrivals_in_window is the sum of
        pending SIT quantities arriving during (day, day+LT+LW]
    Block dispatch when (I + SIT + PO) >= TC
    Orders dispatched on day X spend LW days in loading at source plus
    LT days in transit, and arrive (SIT -> I) on day X + LT + LW.

Initial-state extensions:
    - SIT (Day 0): user-defined qty already in transit; arrives in I on
      day = SIT_transit_time.
    - Open PO (Day 0): user-defined qty awaiting dispatch; on Day 2 it
      moves PO -> SIT and then arrives in I on Day 2 + LT (the Day 2
      dispatch already represents the loading-window step).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

TOTAL_DAYS = 20
INITIAL_PO_DISPATCH_DAY = 2  # Day on which Day-0 Open PO moves PO -> SIT


@dataclass
class PendingOrder:
    dispatch_day: int
    qty: int
    arrival_day: int
    kind: str = "system"  # "system" | "initial_sit" | "initial_po"


@dataclass
class DaySnapshot:
    day: int
    I_before: int
    I_after_consumption: int
    arrivals_today: int
    initial_po_dispatched_today: int
    I: int
    SIT: int
    PO: int
    total: int
    AC: int
    ROP: float
    triggered: bool
    Q: Optional[int]
    dispatch_status: str  # "DISPATCHED", "BLOCKED", "NOT_NEEDED", "NO_ORDER", "NOT_STARTED"
    arrival_day: Optional[int]
    pending_orders: List[PendingOrder] = field(default_factory=list)
    status_color: str = "GREEN"  # GREEN / YELLOW / RED
    status_label: str = "Safe"


class Simulation:
    def __init__(
        self,
        TC: int,
        I_init: int,
        LT: int,
        LW: int,
        D: int,
        SIT_init: int = 0,
        SIT_transit_time: int = 3,
        PO_init: int = 0,
    ):
        self.TC = TC
        self.I = I_init
        self.LT = LT
        self.LW = LW
        self.D = D
        self.SIT = SIT_init
        self.PO = PO_init
        self.SIT_init = SIT_init
        self.SIT_transit_time = SIT_transit_time
        self.PO_init = PO_init
        self.initial_po_dispatched = False
        self.current_day = 0
        self.pending_orders: List[PendingOrder] = []
        self.history: List[DaySnapshot] = []
        self.completed = False

        # Track initial SIT as a pending arrival that lands on day = transit_time
        if SIT_init > 0:
            self.pending_orders.append(PendingOrder(
                dispatch_day=0,
                qty=SIT_init,
                arrival_day=SIT_transit_time,
                kind="initial_sit",
            ))

        self._record_initial()

    @property
    def rop(self) -> float:
        # Dynamic safety buffer: 75% of total capacity (instead of a hardcoded
        # 10-day buffer) so the reorder point scales with warehouse size.
        return self.D * (0.75 * self.TC + self.LT + self.LW)

    @property
    def total_available(self) -> int:
        return self.I + self.SIT + self.PO

    @property
    def AC(self) -> int:
        return self.TC - self.I

    def _classify(self, I: int, total: int, rop: float) -> tuple[str, str]:
        """Section D color rules: GREEN > ROP+2, YELLOW within ±2, RED < ROP-2 or I=0."""
        if I == 0:
            return "RED", "Critical (Stockout)"
        if total > rop + 2:
            return "GREEN", "Safe"
        if total < rop - 2:
            return "RED", "Critical"
        return "YELLOW", "Warning"

    def _record_initial(self) -> None:
        rop = self.rop
        total = self.total_available
        color, label = self._classify(self.I, total, rop)
        self.history.append(
            DaySnapshot(
                day=0,
                I_before=self.I,
                I_after_consumption=self.I,
                arrivals_today=0,
                initial_po_dispatched_today=0,
                I=self.I,
                SIT=self.SIT,
                PO=self.PO,
                total=total,
                AC=self.AC,
                ROP=rop,
                triggered=False,
                Q=None,
                dispatch_status="NOT_STARTED",
                arrival_day=None,
                pending_orders=list(self.pending_orders),
                status_color=color,
                status_label=label,
            )
        )

    def can_advance(self) -> bool:
        return not self.completed and self.current_day < TOTAL_DAYS

    def advance_day(self) -> Optional[DaySnapshot]:
        if not self.can_advance():
            return None

        self.current_day += 1
        day = self.current_day

        I_before = self.I

        # Step 1: Daily consumption (cap at 0 for stockout)
        self.I = max(0, self.I - self.D)
        I_after_consumption = self.I

        # Step 1b: Process arrivals - SIT -> I for orders arriving today
        arrivals_today = 0
        remaining: List[PendingOrder] = []
        for order in self.pending_orders:
            if order.arrival_day == day:
                self.SIT = max(0, self.SIT - order.qty)
                self.I += order.qty
                arrivals_today += order.qty
            else:
                remaining.append(order)
        self.pending_orders = remaining

        # Step 1c: Initial Open PO dispatch (PO -> SIT) on the configured day
        initial_po_dispatched_today = 0
        if (
            not self.initial_po_dispatched
            and self.PO_init > 0
            and day == INITIAL_PO_DISPATCH_DAY
        ):
            self.PO = max(0, self.PO - self.PO_init)
            self.SIT += self.PO_init
            # Day 2 dispatch already absorbs the loading-window step,
            # so arrival is +LT additional days (no extra LW).
            arrival_day_initial_po = INITIAL_PO_DISPATCH_DAY + self.LT
            self.pending_orders.append(PendingOrder(
                dispatch_day=INITIAL_PO_DISPATCH_DAY,
                qty=self.PO_init,
                arrival_day=arrival_day_initial_po,
                kind="initial_po",
            ))
            self.initial_po_dispatched = True
            initial_po_dispatched_today = self.PO_init

        # Step 2: Trigger check
        rop = self.rop
        total = self.total_available
        triggered = total <= rop

        # Step 3: Dispatch if triggered
        Q: Optional[int] = None
        dispatch_status = "NO_ORDER"
        arrival_day: Optional[int] = None

        if triggered:
            if total < self.TC:
                AC = self.TC - self.I
                # Subtract any in-flight inventory that will arrive during the
                # new order's transit window (day, day+LT+LW] so concurrent
                # inflows don't stack and push I above TC. This includes
                # pending SIT plus the not-yet-dispatched initial Open PO
                # (which will arrive on Day 2 + LT once dispatched).
                window_end = day + self.LT + self.LW
                in_flight_in_window = sum(
                    o.qty for o in self.pending_orders
                    if day < o.arrival_day <= window_end
                )
                if (
                    not self.initial_po_dispatched
                    and self.PO_init > 0
                    and day < INITIAL_PO_DISPATCH_DAY + self.LT <= window_end
                ):
                    in_flight_in_window += self.PO_init
                raw_Q = AC + self.D * (self.LT + self.LW) - in_flight_in_window
                Q = min(max(0, raw_Q), self.TC)
                if Q > 0:
                    arrival_day = window_end
                    self.SIT += Q
                    self.pending_orders.append(
                        PendingOrder(dispatch_day=day, qty=Q, arrival_day=arrival_day)
                    )
                    dispatch_status = "DISPATCHED"
                else:
                    dispatch_status = "NOT_NEEDED"
            else:
                dispatch_status = "BLOCKED"

        # Snapshot
        post_total = self.total_available
        color, label = self._classify(self.I, post_total, rop)
        snapshot = DaySnapshot(
            day=day,
            I_before=I_before,
            I_after_consumption=I_after_consumption,
            arrivals_today=arrivals_today,
            initial_po_dispatched_today=initial_po_dispatched_today,
            I=self.I,
            SIT=self.SIT,
            PO=self.PO,
            total=post_total,
            AC=self.TC - self.I,
            ROP=rop,
            triggered=triggered,
            Q=Q,
            dispatch_status=dispatch_status,
            arrival_day=arrival_day,
            pending_orders=list(self.pending_orders),
            status_color=color,
            status_label=label,
        )
        self.history.append(snapshot)

        if self.current_day >= TOTAL_DAYS:
            self.completed = True

        return snapshot

    def latest(self) -> DaySnapshot:
        return self.history[-1]
