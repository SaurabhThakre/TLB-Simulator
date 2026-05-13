"""Module 3 — Inventory Allocation Logic (Streamlit page)."""

from __future__ import annotations

import copy
import json
import os
from typing import Dict, List

import pandas as pd
import streamlit as st

EPS = 1e-9
MAX_CYCLES = 10
MIN_THRESHOLD_DEFAULT = 2.0
DYNAMIC_DEMAND_DEFAULT = 1.0
DYNAMIC_DAYS_DEFAULT = 1
MU_STOCK_DEFAULT = 50.0

STATE_FILE = "m3_state.json"
SCHEMA_VERSION = 1
PERSIST_PREFIX = "m3_"
PERSIST_SKIP = {
    "m3_cycle_results",
    "m3_defaults_applied",
    "m3_current_cycle",
    "m3_new_queue",
    "m3_old_order",
    "m3_new_remaining",
    "m3_old_remaining",
    "m3_sim_complete",
    # Internal Streamlit data_editor diff-state; persisting it double-applies edits.
    "m3_branch_editor",
}

DEFAULT_BRANCHES = [
    {"id": "B01", "name": "Branch 01", "qk": 60.0},
    {"id": "B02", "name": "Branch 02", "qk": 55.0},
    {"id": "B03", "name": "Branch 03", "qk": 50.0},
    {"id": "B04", "name": "Branch 04", "qk": 45.0},
    {"id": "B05", "name": "Branch 05", "qk": 40.0},
    {"id": "B06", "name": "Branch 06", "qk": 35.0},
    {"id": "B07", "name": "Branch 07", "qk": 30.0},
    {"id": "B08", "name": "Branch 08", "qk": 25.0},
    {"id": "B09", "name": "Branch 09", "qk": 20.0},
    {"id": "B10", "name": "Branch 10", "qk": 18.0},
    {"id": "B11", "name": "Branch 11", "qk": 15.0},
    {"id": "B12", "name": "Branch 12", "qk": 12.0},
    {"id": "B13", "name": "Branch 13", "qk": 10.0},
    {"id": "B14", "name": "Branch 14", "qk": 8.0},
    {"id": "B15", "name": "Branch 15", "qk": 5.0},
    {"id": "B16", "name": "Branch 16", "qk": 0.8},
    {"id": "B17", "name": "Branch 17", "qk": 0.6},
    {"id": "B18", "name": "Branch 18", "qk": 0.4},
    {"id": "B19", "name": "Branch 19", "qk": 0.2},
    {"id": "B20", "name": "Branch 20", "qk": 0.0},
]


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def save_state() -> None:
    data: dict = {"_schema_version": SCHEMA_VERSION}
    for k, v in st.session_state.items():
        if not isinstance(k, str) or not k.startswith(PERSIST_PREFIX):
            continue
        if k in PERSIST_SKIP:
            continue
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        data[k] = v
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def load_state() -> None:
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if data.get("_schema_version") != SCHEMA_VERSION:
        return
    for k, v in data.items():
        if k.startswith("_"):
            continue
        st.session_state[k] = v


def _init_state() -> None:
    # 1. Persistent inputs — hardcoded defaults via setdefault.
    st.session_state.setdefault("m3_mu_stock", MU_STOCK_DEFAULT)
    st.session_state.setdefault("m3_threshold_mode", "Fixed MT")
    st.session_state.setdefault("m3_threshold_value", MIN_THRESHOLD_DEFAULT)
    st.session_state.setdefault("m3_dynamic_demand", DYNAMIC_DEMAND_DEFAULT)
    st.session_state.setdefault("m3_dynamic_days", DYNAMIC_DAYS_DEFAULT)
    st.session_state.setdefault("m3_branch_data", copy.deepcopy(DEFAULT_BRANCHES))

    # 2. Simulation runtime — never persisted.
    st.session_state.setdefault("m3_current_cycle", 0)
    st.session_state.setdefault("m3_cycle_results", [])
    st.session_state.setdefault("m3_new_queue", [])
    st.session_state.setdefault("m3_old_order", [])
    st.session_state.setdefault("m3_new_remaining", {})
    st.session_state.setdefault("m3_old_remaining", {})
    st.session_state.setdefault("m3_sim_complete", False)

    # 3. Override persistent inputs with saved values.
    load_state()


def get_effective_threshold() -> float:
    if st.session_state["m3_threshold_mode"] == "Fixed MT":
        return float(st.session_state["m3_threshold_value"])
    return max(
        float(st.session_state["m3_threshold_value"]),
        float(st.session_state["m3_dynamic_demand"])
        * int(st.session_state["m3_dynamic_days"]),
    )


# --------------------------------------------------------------------------
# Top-of-page Clear / Reset button
# --------------------------------------------------------------------------

def _render_clear_button() -> None:
    if st.button("↺ Clear / Reset to Defaults"):
        try:
            os.remove(STATE_FILE)
        except FileNotFoundError:
            pass
        for k in list(st.session_state.keys()):
            if isinstance(k, str) and k.startswith("m3_"):
                del st.session_state[k]
        st.rerun()


# --------------------------------------------------------------------------
# Global inputs
# --------------------------------------------------------------------------

def _render_global_inputs() -> None:
    st.subheader("Global Inputs")

    st.number_input(
        "Available MU Stock — P (MT)",
        min_value=0.5, step=0.5, format="%.1f",
        key="m3_mu_stock",
        on_change=save_state,
    )

    st.radio(
        "Minimum Threshold Mode",
        options=["Fixed MT", "Dynamic (max(Fixed MT, D × Days))"],
        key="m3_threshold_mode",
        on_change=save_state,
        horizontal=True,
    )

    st.number_input(
        "Minimum Threshold (MT)",
        min_value=0.5, step=0.5, format="%.1f",
        key="m3_threshold_value",
        on_change=save_state,
    )

    if st.session_state["m3_threshold_mode"] == "Dynamic (max(Fixed MT, D × Days))":
        c1, c2 = st.columns(2)
        with c1:
            st.number_input(
                "Daily Demand — D (MT/day)",
                min_value=0.1, step=0.1, format="%.1f",
                key="m3_dynamic_demand",
                on_change=save_state,
            )
        with c2:
            st.number_input(
                "Days for Dynamic Threshold",
                min_value=1, step=1,
                key="m3_dynamic_days",
                on_change=save_state,
            )
        eff = get_effective_threshold()
        st.info(f"Effective threshold (Dynamic): {eff:.2f} MT")


# --------------------------------------------------------------------------
# Branch table
# --------------------------------------------------------------------------

def _render_branch_table() -> None:
    st.subheader("Branch Configuration")
    branches = st.session_state["m3_branch_data"]

    df = pd.DataFrame([
        {"Branch ID": b["id"], "Branch Name": b["name"],
         "Dispatch Qty Qk (MT)": float(b["qk"])}
        for b in branches
    ])

    edited = st.data_editor(
        df,
        key="m3_branch_editor",
        num_rows="fixed",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Branch ID": st.column_config.TextColumn("Branch ID", disabled=True),
            "Branch Name": st.column_config.TextColumn("Branch Name"),
            "Dispatch Qty Qk (MT)": st.column_config.NumberColumn(
                "Dispatch Qty Qk (MT)",
                min_value=0.0, step=0.5, format="%.1f",
            ),
        },
    )

    # Sync the edited frame back into the canonical list-of-dicts state.
    new_data: List[dict] = []
    for _, row in edited.iterrows():
        try:
            qk = float(row["Dispatch Qty Qk (MT)"])
        except (TypeError, ValueError):
            qk = 0.0
        new_data.append({
            "id": str(row["Branch ID"]),
            "name": str(row["Branch Name"]),
            "qk": qk,
        })
    if new_data != branches:
        st.session_state["m3_branch_data"] = new_data
        save_state()

    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        if st.button("+ Add Branch", key="m3_btn_add", use_container_width=True):
            n = len(st.session_state["m3_branch_data"]) + 1
            st.session_state["m3_branch_data"].append({
                "id": f"B{n:02d}",
                "name": f"Branch {n:02d}",
                "qk": 0.0,
            })
            save_state()
            st.rerun()
    with c2:
        disable_remove = len(st.session_state["m3_branch_data"]) <= 1
        if st.button(
            "× Remove Last Branch",
            key="m3_btn_remove",
            disabled=disable_remove,
            use_container_width=True,
        ):
            st.session_state["m3_branch_data"].pop()
            save_state()
            st.rerun()


# --------------------------------------------------------------------------
# Simulation algorithm
# --------------------------------------------------------------------------

def _initialise_simulation() -> None:
    branches = st.session_state["m3_branch_data"]
    new_rem: Dict[str, float] = {}
    old_rem: Dict[str, float] = {}
    for b in branches:
        qk = float(b["qk"])
        new_rem[b["id"]] = qk
        old_rem[b["id"]] = qk

    eligible = [b for b in branches if float(b["qk"]) > EPS]
    eligible_sorted = sorted(eligible, key=lambda b: float(b["qk"]), reverse=True)
    ordered_ids = [b["id"] for b in eligible_sorted]

    st.session_state["m3_new_remaining"] = new_rem
    st.session_state["m3_old_remaining"] = old_rem
    st.session_state["m3_new_queue"] = list(ordered_ids)
    st.session_state["m3_old_order"] = list(ordered_ids)


def _build_row(
    branch: dict,
    remaining_before: float,
    allocated: float,
) -> dict:
    remaining_after = max(0.0, remaining_before - allocated)
    if allocated > EPS:
        status = "✅ Served"
    elif remaining_before <= EPS:
        status = "✔️ Fulfilled"
    else:
        status = "⏳ Queued"
    return {
        "branch_id": branch["id"],
        "branch_name": branch["name"],
        "original_qk": float(branch["qk"]),
        "remaining_before": remaining_before,
        "allocated": allocated,
        "remaining_after": remaining_after,
        "status": status,
    }


def _run_new_logic(P: float, threshold: float) -> dict:
    new_remaining: Dict[str, float] = st.session_state["m3_new_remaining"]
    queue: List[str] = st.session_state["m3_new_queue"]

    active = [bid for bid in queue if new_remaining.get(bid, 0.0) > EPS]
    active_sorted = sorted(active, key=lambda b: new_remaining[b], reverse=True)

    k_star = 0
    no_branch_can_be_served = False

    for k in range(1, len(active_sorted) + 1):
        top_k = active_sorted[:k]
        total_q = sum(new_remaining[b] for b in top_k)
        if total_q <= EPS:
            break
        trial = {b: new_remaining[b] * P / total_q for b in top_k}
        min_alloc = min(trial.values())
        if min_alloc < threshold:
            k_star = k - 1
            if k_star == 0:
                no_branch_can_be_served = True
            break
        if k == len(active_sorted):
            k_star = k

    warning = None
    allocations: Dict[str, float] = {}
    served_ids: List[str] = []

    if not active_sorted:
        warning = "All branches already fulfilled. No allocation needed this cycle."
        residual = P
    elif no_branch_can_be_served:
        warning = (
            "Available stock too low to meet minimum threshold for any branch."
        )
        residual = P
    else:
        served_ids = list(active_sorted[:k_star])
        total_q_star = sum(new_remaining[b] for b in served_ids)
        if total_q_star <= EPS:
            allocations = {}
            residual = P
        else:
            for bid in served_ids:
                raw = new_remaining[bid] * P / total_q_star
                allocations[bid] = min(raw, new_remaining[bid])
            residual = P - sum(allocations.values())

    unserved_ids = [bid for bid in active_sorted if bid not in set(served_ids)]

    # Per-branch rows for display, in canonical branch_data order.
    rows = []
    for b in st.session_state["m3_branch_data"]:
        bid = b["id"]
        if bid not in new_remaining:
            continue
        rows.append(_build_row(
            b,
            remaining_before=new_remaining[bid],
            allocated=allocations.get(bid, 0.0),
        ))

    # Commit allocations.
    for bid, alloc in allocations.items():
        new_remaining[bid] = max(0.0, new_remaining[bid] - alloc)
    st.session_state["m3_new_remaining"] = new_remaining

    # New queue: unserved (re-sorted by remaining desc) + served (in their served order).
    unserved_sorted = sorted(
        unserved_ids, key=lambda b: new_remaining.get(b, 0.0), reverse=True
    )
    st.session_state["m3_new_queue"] = unserved_sorted + served_ids

    return {
        "rows": rows,
        "served_ids": served_ids,
        "unserved_ids": unserved_sorted,
        "served_ids_moved_to_bottom": served_ids,
        "residual": max(0.0, residual),
        "total_allocated": sum(allocations.values()),
        "branches_served": len(served_ids),
        "branches_active": len(active_sorted),
        "warning": warning,
    }


def _run_old_logic(P: float) -> dict:
    old_remaining: Dict[str, float] = st.session_state["m3_old_remaining"]
    order: List[str] = st.session_state["m3_old_order"]

    active_count = sum(1 for bid in order if old_remaining.get(bid, 0.0) > EPS)

    remaining_stock = P
    allocations: Dict[str, float] = {}
    served_ids: List[str] = []

    for bid in order:
        if old_remaining.get(bid, 0.0) <= EPS:
            continue
        if remaining_stock <= EPS:
            break
        alloc = min(remaining_stock, old_remaining[bid])
        if alloc > EPS:
            allocations[bid] = alloc
            served_ids.append(bid)
            remaining_stock -= alloc

    residual = max(0.0, remaining_stock)

    rows = []
    for b in st.session_state["m3_branch_data"]:
        bid = b["id"]
        if bid not in old_remaining:
            continue
        rows.append(_build_row(
            b,
            remaining_before=old_remaining[bid],
            allocated=allocations.get(bid, 0.0),
        ))

    for bid, alloc in allocations.items():
        old_remaining[bid] = max(0.0, old_remaining[bid] - alloc)
    st.session_state["m3_old_remaining"] = old_remaining

    served_set = set(served_ids)
    unserved_ids = [
        bid for bid in order
        if bid not in served_set and old_remaining.get(bid, 0.0) > EPS
    ]

    return {
        "rows": rows,
        "served_ids": served_ids,
        "unserved_ids": unserved_ids,
        "served_ids_moved_to_bottom": [],  # old logic never reorders
        "residual": residual,
        "total_allocated": sum(allocations.values()),
        "branches_served": len(served_ids),
        "branches_active": active_count,
        "warning": None,
    }


def _run_cycle() -> None:
    if st.session_state["m3_current_cycle"] == 0:
        _initialise_simulation()
    st.session_state["m3_current_cycle"] += 1
    P = float(st.session_state["m3_mu_stock"])
    threshold = get_effective_threshold()
    new_result = _run_new_logic(P, threshold)
    old_result = _run_old_logic(P)
    st.session_state["m3_cycle_results"].append({
        "cycle": st.session_state["m3_current_cycle"],
        "P": P,
        "threshold": threshold,
        "new": new_result,
        "old": old_result,
    })
    if st.session_state["m3_current_cycle"] >= MAX_CYCLES:
        st.session_state["m3_sim_complete"] = True
    save_state()
    st.rerun()


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------

def _render_controls() -> None:
    st.subheader("Simulation Controls")
    cycle = st.session_state["m3_current_cycle"]
    complete = st.session_state["m3_sim_complete"]

    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
    with c1:
        if st.button(
            "▶ Start / Run Cycle 1",
            key="m3_btn_start",
            type="primary",
            disabled=cycle > 0,
            use_container_width=True,
        ):
            _run_cycle()
    with c2:
        if st.button(
            "⏭ Next Cycle",
            key="m3_btn_next",
            disabled=(cycle == 0) or complete,
            use_container_width=True,
        ):
            _run_cycle()
    with c3:
        if st.button(
            "↺ Reset Simulation",
            key="m3_btn_reset",
            use_container_width=True,
        ):
            for k in (
                "m3_cycle_results", "m3_current_cycle",
                "m3_new_queue", "m3_old_order",
                "m3_new_remaining", "m3_old_remaining",
                "m3_sim_complete",
            ):
                st.session_state.pop(k, None)
            st.rerun()
    with c4:
        if cycle == 0:
            st.caption("No cycles run yet.")
        else:
            st.markdown(f"**Cycle {cycle} of {MAX_CYCLES}**")

    if complete:
        st.success("✅ Simulation Complete — All 10 cycles have been run.")


# --------------------------------------------------------------------------
# Output rendering
# --------------------------------------------------------------------------

_STATUS_BG = {
    "✅ Served": "#e8f5e9",
    "✔️ Fulfilled": "#f5f5f5",
    "⏳ Queued": "",
}


def _style_status_row(row: pd.Series) -> List[str]:
    color = _STATUS_BG.get(row.get("Status", ""), "")
    css = f"background-color: {color}" if color else ""
    return [css for _ in row]


def _render_result_table(result: dict) -> None:
    rows = []
    for r in result["rows"]:
        rows.append({
            "Branch": f"{r['branch_id']} — {r['branch_name']}",
            "Original Qk (MT)": r["original_qk"],
            "Before (MT)": r["remaining_before"],
            "Allocated (MT)": r["allocated"],
            "After (MT)": r["remaining_after"],
            "Status": r["status"],
        })
    if not rows:
        st.caption("No branches to display.")
        return
    df = pd.DataFrame(rows)
    styled = df.style.apply(_style_status_row, axis=1).format({
        "Original Qk (MT)": "{:.2f}",
        "Before (MT)": "{:.2f}",
        "Allocated (MT)": "{:.2f}",
        "After (MT)": "{:.2f}",
    })
    st.dataframe(styled, hide_index=True, use_container_width=True)


def _render_cycle_results() -> None:
    results = st.session_state["m3_cycle_results"]
    if not results:
        return
    current_cycle = st.session_state["m3_current_cycle"]
    for entry in reversed(results):
        with st.expander(
            f"\U0001F4E6 Cycle {entry['cycle']} Results",
            expanded=(entry["cycle"] == current_cycle),
        ):
            st.caption(
                f"MU Stock Used: {entry['P']:.2f} MT | "
                f"Threshold: {entry['threshold']:.2f} MT"
            )
            new = entry["new"]
            old = entry["old"]

            if new.get("warning"):
                st.warning(new["warning"])

            col_l, col_r = st.columns(2)
            with col_l:
                st.markdown("**\U0001F7E2 New Logic — Eligible Set Expansion**")
                _render_result_table(new)
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Allocated", f"{new['total_allocated']:.2f} MT")
                m2.metric("Residual Stock", f"{new['residual']:.2f} MT")
                m3.metric(
                    "Branches Served",
                    f"{new['branches_served']} of {new['branches_active']} active",
                )
            with col_r:
                st.markdown("**\U0001F534 Old Logic — First-Come-First-Served**")
                _render_result_table(old)
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Allocated", f"{old['total_allocated']:.2f} MT")
                m2.metric("Residual Stock", f"{old['residual']:.2f} MT")
                m3.metric(
                    "Branches Served",
                    f"{old['branches_served']} of {old['branches_active']} active",
                )

            st.info(
                f"New Logic served {new['branches_served']} branches · "
                f"Old Logic served {old['branches_served']} branches · "
                f"P = {entry['P']:.2f} MT each"
            )


def _render_cumulative() -> None:
    new_rem = st.session_state["m3_new_remaining"]
    old_rem = st.session_state["m3_old_remaining"]
    if not new_rem and not old_rem:
        return
    st.subheader("\U0001F4CA Cumulative Allocation Progress")

    rows = []
    for b in st.session_state["m3_branch_data"]:
        bid = b["id"]
        if bid not in new_rem and bid not in old_rem:
            continue
        original = float(b["qk"])
        new_left = float(new_rem.get(bid, 0.0))
        old_left = float(old_rem.get(bid, 0.0))
        rows.append({
            "Branch": f"{bid} — {b['name']}",
            "Original Qk (MT)": original,
            "Received — New (MT)": max(0.0, original - new_left),
            "Received — Old (MT)": max(0.0, original - old_left),
            "Remaining — New (MT)": new_left,
            "Remaining — Old (MT)": old_left,
            "Fulfilled — New": "✅" if new_left <= EPS else "⏳",
            "Fulfilled — Old": "✅" if old_left <= EPS else "⏳",
        })
    if not rows:
        st.caption("Run a cycle to populate cumulative progress.")
        return

    df = pd.DataFrame(rows)

    def highlight_new_fulfilled(col: pd.Series) -> List[str]:
        if col.name != "Fulfilled — New":
            return ["" for _ in col]
        return [
            "background-color: #e8f5e9" if v == "✅" else ""
            for v in col
        ]

    styled = df.style.apply(highlight_new_fulfilled, axis=0).format({
        "Original Qk (MT)": "{:.2f}",
        "Received — New (MT)": "{:.2f}",
        "Received — Old (MT)": "{:.2f}",
        "Remaining — New (MT)": "{:.2f}",
        "Remaining — Old (MT)": "{:.2f}",
    })
    st.dataframe(styled, hide_index=True, use_container_width=True)


def _render_queue_panel() -> None:
    results = st.session_state["m3_cycle_results"]
    if not results:
        return

    st.subheader("\U0001F500 New Logic — Next Cycle Queue State")

    if st.session_state["m3_sim_complete"]:
        st.success("Simulation complete. Reset to run again.")
        return

    new_rem = st.session_state["m3_new_remaining"]
    last_new = results[-1]["new"]
    name_by_id = {b["id"]: b["name"] for b in st.session_state["m3_branch_data"]}

    unserved = last_new["unserved_ids"]
    served = last_new["served_ids_moved_to_bottom"]

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**⬆️ Priority Queue (Top — Unserved)**")
        rows = [
            {
                "Branch ID": bid,
                "Branch Name": name_by_id.get(bid, ""),
                "Remaining Qk (MT)": round(new_rem.get(bid, 0.0), 2),
            }
            for bid in unserved
        ]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.caption("Empty — no branches waiting.")
    with col_r:
        st.markdown("**⬇️ Moved to Bottom (Served this cycle)**")
        rows = [
            {
                "Branch ID": bid,
                "Branch Name": name_by_id.get(bid, ""),
                "Remaining Qk (MT)": round(new_rem.get(bid, 0.0), 2),
            }
            for bid in served
        ]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.caption("Empty — no branches served this cycle.")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def render() -> None:
    _init_state()

    st.title("\U0001F3ED Module 3 — Inventory Allocation Logic")
    st.caption(
        "Simulate proportional stock distribution across branch warehouses "
        "under scarcity. Compares New Logic (Eligible Set Expansion) vs "
        "Old Logic (First-Come-First-Served) over up to 10 cycles."
    )

    _render_clear_button()
    st.divider()
    _render_global_inputs()
    st.divider()
    _render_branch_table()
    st.divider()
    _render_controls()

    if st.session_state["m3_cycle_results"]:
        st.divider()
        _render_cycle_results()
        st.divider()
        _render_cumulative()
        st.divider()
        _render_queue_panel()

    save_state()
