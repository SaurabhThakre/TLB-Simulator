"""TLB Inventory Dispatch Simulation engine.

Implements a 20-day inventory cycle following the TLB dispatch formula:
    ROP = D * (10 + LT + LW)
    Trigger when (I + SIT + PO) <= ROP
    Q = min(AC + D*(LT+LW), TC), where AC = TC - I
    Block dispatch when (I + SIT + PO) >= TC
    Orders dispatched on day X arrive (move SIT -> I) on day X + LT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

TOTAL_DAYS = 20


@dataclass
class PendingOrder:
    dispatch_day: int
    qty: int
    arrival_day: int


@dataclass
class DaySnapshot:
    day: int
    I_before: int
    I_after_consumption: int
    arrivals_today: int
    I: int
    SIT: int
    PO: int
    total: int
    AC: int
    ROP: int
    triggered: bool
    Q: Optional[int]
    dispatch_status: str  # "DISPATCHED", "BLOCKED", "NO_ORDER", "NOT_STARTED"
    arrival_day: Optional[int]
    pending_orders: List[PendingOrder] = field(default_factory=list)
    status_color: str = "GREEN"  # GREEN / YELLOW / RED
    status_label: str = "Safe"


class Simulation:
    def __init__(self, TC: int, I_init: int, LT: int, LW: int, D: int, PO_init: int = 0):
        self.TC = TC
        self.I = I_init
        self.LT = LT
        self.LW = LW
        self.D = D
        self.SIT = 0
        self.PO = PO_init  # External purchase orders, static during simulation
        self.current_day = 0
        self.pending_orders: List[PendingOrder] = []
        self.history: List[DaySnapshot] = []
        self.completed = False
        self._record_initial()

    @property
    def rop(self) -> int:
        return self.D * (10 + self.LT + self.LW)

    @property
    def total_available(self) -> int:
        return self.I + self.SIT + self.PO

    @property
    def AC(self) -> int:
        return self.TC - self.I

    def _classify(self, I: int, total: int, rop: int) -> tuple[str, str]:
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
                Q = min(AC + self.D * (self.LT + self.LW), self.TC)
                arrival_day = day + self.LT
                # System-generated orders go directly to SIT (bypass PO)
                self.SIT += Q
                self.pending_orders.append(
                    PendingOrder(dispatch_day=day, qty=Q, arrival_day=arrival_day)
                )
                dispatch_status = "DISPATCHED"
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
