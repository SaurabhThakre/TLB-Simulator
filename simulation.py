"""TLB Inventory Dispatch Simulation engine - forward-simulation algorithm.

100-day inventory cycle with a configurable trigger threshold and a
forward-simulation projection of on-hand inventory.

  Threshold (default 75% of TC) replaces the legacy ROP.
  Trigger when projected I (after forward-simulating LT+LW days) <=
      threshold_pct * TC.
  Dispatch quantity:
      Q = max(0, TC - projected_I)

Stock In Transit tracking:
  SIT (dict): order_index -> quantity dispatched (float)
  DD  (dict): order_index -> days already in transit (int)
  An order arrives when DD[k] == LT+LW. New dispatches reuse slot j if
  SIT[j] is empty; otherwise j is incremented.

Daily flow per the reference algorithm:
  1. Trigger check via forward-simulated projected I.
  2. Compute Q and place new order if triggered.
  3. Process SIT arrivals (check arrival, then increment DD).
  4. Daily consumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TOTAL_DAYS = 100


@dataclass
class PendingOrder:
    order_index: int
    qty: float
    days_in_transit: int
    days_remaining: int


@dataclass
class DaySnapshot:
    day: int
    I_before: float
    I_after_arrivals: float
    I_after_consumption: float
    arrivals_today: float
    arrival_orders: List[int]
    I: float
    SIT_Total: float
    projected_I: float
    threshold_floor: float
    floor_50: float
    floor_breached: bool
    triggered: bool
    Q: Optional[float]
    dispatch_status: str  # "DISPATCHED" | "NO_TRIGGER" | "NOT_STARTED"
    pending_orders: List[PendingOrder] = field(default_factory=list)
    status_color: str = "GREEN"
    status_label: str = "Safe"


class Simulation:
    def __init__(
        self,
        TC: float,
        I_init: float,
        LT: int,
        LW: int,
        D: float,
        SIT_init: float = 0.0,
        DD_init: int = 0,
        threshold_pct: float = 0.75,
    ):
        self.TC = float(TC)
        self.I = float(I_init)
        self.LT = int(LT)
        self.LW = int(LW)
        self.D = float(D)
        self.SIT_init = float(SIT_init)
        self.DD_init = int(DD_init)
        self.threshold_pct = float(threshold_pct)
        self.SIT: Dict[int, float] = {0: float(SIT_init)}
        self.DD: Dict[int, int] = {0: int(DD_init)}
        self.j = 0
        self.current_day = 0
        self.history: List[DaySnapshot] = []
        self.completed = False
        self._record_initial()

    @property
    def TLT(self) -> int:
        return self.LT + self.LW

    @property
    def threshold_floor(self) -> float:
        return self.threshold_pct * self.TC

    @property
    def floor_50(self) -> float:
        return 0.50 * self.TC

    @property
    def SIT_Total(self) -> float:
        return sum(self.SIT.values())

    def _projected_inventory(self) -> float:
        """Forward-simulate I over TLT days assuming no new dispatch.

        Mirrors the daily flow: Consume -> Arrivals each iteration.
        """
        sim_I = self.I
        sim_SIT = dict(self.SIT)
        sim_DD = dict(self.DD)

        for _ in range(self.TLT):
            if sim_I > 0:
                sim_I = max(0.0, sim_I - self.D)
            for k in list(sim_DD.keys()):
                if sim_SIT.get(k, 0) == 0:
                    continue
                if sim_DD[k] == self.TLT:
                    sim_I += sim_SIT[k]
                    sim_DD[k] = 0
                    sim_SIT[k] = 0
                elif sim_SIT.get(k, 0) != 0:
                    sim_DD[k] += 1

        return sim_I

    def _classify(self) -> Tuple[str, str]:
        pct = int(round(self.threshold_pct * 100))
        if self.I == 0:
            return "RED", "Critical (Stockout)"
        if self.I > self.threshold_floor:
            return "GREEN", f"Safe (above {pct}% floor)"
        if self.I > self.floor_50:
            return "YELLOW", f"Warning (50%–{pct}%)"
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
        proj = self._projected_inventory()
        self.history.append(
            DaySnapshot(
                day=0,
                I_before=self.I,
                I_after_arrivals=self.I,
                I_after_consumption=self.I,
                arrivals_today=0.0,
                arrival_orders=[],
                I=self.I,
                SIT_Total=self.SIT_Total,
                projected_I=proj,
                threshold_floor=self.threshold_floor,
                floor_50=self.floor_50,
                floor_breached=proj <= self.threshold_floor,
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
            self.I = max(0.0, self.I - self.D)
        I_after_consumption = self.I

        # Step 2: Process SIT arrivals (check, then increment)
        arrivals_today = 0.0
        arrival_orders: List[int] = []
        for k in list(self.DD.keys()):
            if self.SIT[k] == 0:
                continue
            if self.DD[k] == self.TLT:
                self.I += self.SIT[k]
                arrivals_today += self.SIT[k]
                arrival_orders.append(k)
                self.DD[k] = 0
                self.SIT[k] = 0
            if self.SIT[k] != 0:
                self.DD[k] += 1
        I_after_arrivals = self.I

        # Step 3: Trigger check + Dispatch
        # New dispatch DD initialized to 1 so arrival lands on day X+TLT
        # (with C->A->T flow, dispatch happens after the day's arrival step,
        # so DD=1 puts it one increment ahead).
        proj_I = self._projected_inventory()
        triggered = proj_I <= self.threshold_floor

        Q_value: Optional[float] = None
        dispatch_status = "NO_TRIGGER"
        if triggered:
            Q_raw = max(0.0, self.TC - proj_I)
            if self.SIT.get(self.j, 0) != 0:
                self.j += 1
            self.SIT[self.j] = Q_raw
            self.DD[self.j] = 1
            Q_value = Q_raw
            dispatch_status = "DISPATCHED"

        color, label = self._classify()
        snapshot = DaySnapshot(
            day=day,
            I_before=I_before,
            I_after_arrivals=I_after_arrivals,
            I_after_consumption=I_after_consumption,
            arrivals_today=arrivals_today,
            arrival_orders=arrival_orders,
            I=self.I,
            SIT_Total=self.SIT_Total,
            projected_I=proj_I,
            threshold_floor=self.threshold_floor,
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
