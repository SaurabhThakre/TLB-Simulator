"""TLB Inventory Dispatch Simulation engine - new dispatch algorithm.

Implements a 20-day inventory cycle following the new TLB algorithm:

  Floor (75% of TC) replaces the legacy ROP.
  Trigger when projected I (after LT+LW days, factoring in arriving SIT)
      <= 0.75 * TC.
  Dispatch quantity:
      term1 = TC - max(0, I + SIT_Total - (LT+LW)*D)
      term2 = TC - max(0, I - (LT+LW)*D) - sd(SIT, DD, I, LT+LW)
      Q = max(0, min(term1, term2))
  If Q == 0 the dispatch is skipped entirely; no empty SIT/DD entry is
  created.

Stock In Transit tracking:
  SIT (dict): order_index -> quantity dispatched
  DD  (dict): order_index -> days already in transit
  An order arrives when DD[k] == LT+LW. Days remaining = (LT+LW) - DD[k].

Daily flow per the reference algorithm:
  1. Daily consumption.
  2. Process SIT arrivals (check arrival, then increment DD).
  3. Trigger check via projected I.
  4. Compute Q and place new order if triggered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TOTAL_DAYS = 20


@dataclass
class PendingOrder:
    order_index: int
    qty: int
    days_in_transit: int
    days_remaining: int


@dataclass
class DaySnapshot:
    day: int
    I_before: int
    I_after_consumption: int
    arrivals_today: int
    arrival_orders: List[int]
    I: int
    SIT_Total: int
    projected_I: float
    floor_75: float
    floor_50: float
    floor_breached: bool
    triggered: bool
    Q: Optional[int]
    dispatch_status: str  # "DISPATCHED" | "NOT_NEEDED" | "NO_TRIGGER" | "NOT_STARTED"
    pending_orders: List[PendingOrder] = field(default_factory=list)
    status_color: str = "GREEN"
    status_label: str = "Safe"


def sd(SIT: Dict[int, int], DD: Dict[int, int], I: int, TLT: int, D: int) -> float:
    """Estimate how much SIT will remain after consumption upon arrival.

    Mirrors the reference algorithm exactly: iterate orders in index order,
    find the first whose post-consumption remainder is positive, then add
    the full quantities of all subsequent in-transit orders.
    """
    if D <= 0:
        return 0.0
    keys = sorted(SIT.keys())
    for i, l in enumerate(keys):
        if TLT >= I / D + DD[l]:
            value = SIT[l] / D - DD[l]
        else:
            value = SIT[l] / D - max(
                min(
                    SIT[l] / D,
                    TLT - max(TLT - DD[l], 0) - max(I / D - max(TLT - DD[l], 0), 0),
                ),
                0,
            )
        if value > 0:
            total = value
            for next_key in keys[i + 1:]:
                total += SIT[next_key] / D
            return total * D
    return 0.0


class Simulation:
    def __init__(
        self,
        TC: int,
        I_init: int,
        LT: int,
        LW: int,
        D: int,
        SIT_init: int = 5,
        DD_init: int = 6,
    ):
        self.TC = TC
        self.I = I_init
        self.LT = LT
        self.LW = LW
        self.D = D
        self.SIT_init = SIT_init
        self.DD_init = DD_init
        self.SIT: Dict[int, int] = {0: SIT_init}
        self.DD: Dict[int, int] = {0: DD_init}
        self.j = 0
        self.current_day = 0
        self.history: List[DaySnapshot] = []
        self.completed = False
        self._record_initial()

    @property
    def TLT(self) -> int:
        return self.LT + self.LW

    @property
    def floor_75(self) -> float:
        return 0.75 * self.TC

    @property
    def floor_50(self) -> float:
        return 0.50 * self.TC

    @property
    def SIT_Total(self) -> int:
        return sum(self.SIT.values())

    def projected_I(self) -> float:
        bare = max(0, self.I - self.TLT * self.D)
        with_sit = sd(self.SIT, self.DD, self.I, self.TLT, self.D)
        return bare + with_sit

    def _classify(self) -> Tuple[str, str]:
        # Status color is driven by I alone.
        if self.I == 0:
            return "RED", "Critical (Stockout)"
        if self.I > self.floor_75:
            return "GREEN", "Safe (above 75% floor)"
        if self.I > self.floor_50:
            return "YELLOW", "Warning (50%–75%)"
        return "RED", "Critical (≤ 50%)"

    def _pending_list(self) -> List[PendingOrder]:
        result: List[PendingOrder] = []
        for k in sorted(self.SIT.keys()):
            qty = self.SIT[k]
            if qty <= 0:
                continue
            dd = self.DD[k]
            result.append(
                PendingOrder(
                    order_index=k,
                    qty=qty,
                    days_in_transit=dd,
                    days_remaining=self.TLT - dd,
                )
            )
        return result

    def _record_initial(self) -> None:
        color, label = self._classify()
        proj = self.projected_I()
        self.history.append(
            DaySnapshot(
                day=0,
                I_before=self.I,
                I_after_consumption=self.I,
                arrivals_today=0,
                arrival_orders=[],
                I=self.I,
                SIT_Total=self.SIT_Total,
                projected_I=proj,
                floor_75=self.floor_75,
                floor_50=self.floor_50,
                floor_breached=proj <= self.floor_75,
                triggered=False,
                Q=None,
                dispatch_status="NOT_STARTED",
                pending_orders=self._pending_list(),
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

        # Step 1: Daily consumption
        if self.I > 0:
            self.I -= self.D
        self.I = max(0, self.I)
        I_after_consumption = self.I

        # Step 2: Process SIT arrivals (check, then increment)
        arrivals_today = 0
        arrival_orders: List[int] = []
        for k in list(self.DD.keys()):
            if self.SIT[k] == 0:
                continue
            if self.DD[k] == self.TLT:
                self.I = min(self.I + self.SIT[k], self.TC)
                arrivals_today += self.SIT[k]
                arrival_orders.append(k)
                self.DD[k] = 0
                self.SIT[k] = 0
            if self.SIT[k] != 0:
                self.DD[k] += 1

        # Step 3: Trigger condition - projected I after LT+LW days
        proj_bare = max(0, self.I - self.TLT * self.D)
        proj_sit = sd(self.SIT, self.DD, self.I, self.TLT, self.D)
        projected_total = proj_bare + proj_sit
        floor = self.floor_75
        triggered = projected_total <= floor

        # Step 4: Compute Q and place dispatch
        Q_value: Optional[int] = None
        dispatch_status = "NO_TRIGGER"
        if triggered:
            term1 = self.TC - max(0, self.I + self.SIT_Total - self.TLT * self.D)
            term2 = self.TC - max(0, self.I - self.TLT * self.D) - proj_sit
            Q_raw = max(0, min(term1, term2))
            Q_int = int(round(Q_raw))
            if Q_int > 0:
                self.j += 1
                self.SIT[self.j] = Q_int
                self.DD[self.j] = 0
                Q_value = Q_int
                dispatch_status = "DISPATCHED"
            else:
                Q_value = 0
                dispatch_status = "NOT_NEEDED"

        color, label = self._classify()
        snapshot = DaySnapshot(
            day=day,
            I_before=I_before,
            I_after_consumption=I_after_consumption,
            arrivals_today=arrivals_today,
            arrival_orders=arrival_orders,
            I=self.I,
            SIT_Total=self.SIT_Total,
            projected_I=projected_total,
            floor_75=floor,
            floor_50=self.floor_50,
            floor_breached=triggered,
            triggered=triggered,
            Q=Q_value,
            dispatch_status=dispatch_status,
            pending_orders=self._pending_list(),
            status_color=color,
            status_label=label,
        )
        self.history.append(snapshot)

        if self.current_day >= TOTAL_DAYS:
            self.completed = True

        return snapshot

    def latest(self) -> DaySnapshot:
        return self.history[-1]
