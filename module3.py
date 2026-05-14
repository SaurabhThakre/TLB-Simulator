"""Module 3 — Inventory Allocation Logic (Streamlit page)."""

from __future__ import annotations

import copy
import json
import os
from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

EPS = 1e-9
MAX_CYCLES = 50
MIN_THRESHOLD_DEFAULT = 2.0
DYNAMIC_DEMAND_DEFAULT = 1.0
DYNAMIC_DAYS_DEFAULT = 1
MU_STOCK_DEFAULT = 50.0

STATE_FILE = "m3_state.json"
SCHEMA_VERSION = 2
PERSIST_PREFIX = "m3_"
PERSIST_SKIP = {
    "m3_cycle_results",
    "m3_defaults_applied",
    "m3_current_cycle",
    "m3_new_queue",
    "m3_new_remaining",
    "m3_sim_complete",
    # Internal Streamlit data_editor diff-state; persisting it double-applies edits.
    "m3_branch_editor",
    # Session-only init flag — must never be written to disk.
    "m3_initialized",
}

DEFAULT_BRANCHES = [
    {"id": "B01", "name": "Branch 01", "qk": 60.0, "ui": -4.5},
    {"id": "B02", "name": "Branch 02", "qk": 55.0, "ui": -3.8},
    {"id": "B03", "name": "Branch 03", "qk": 50.0, "ui": -3.1},
    {"id": "B04", "name": "Branch 04", "qk": 45.0, "ui": -2.6},
    {"id": "B05", "name": "Branch 05", "qk": 40.0, "ui": -1.9},
    {"id": "B06", "name": "Branch 06", "qk": 35.0, "ui": -1.2},
    {"id": "B07", "name": "Branch 07", "qk": 30.0, "ui": -0.5},
    {"id": "B08", "name": "Branch 08", "qk": 25.0, "ui": 0.2},
    {"id": "B09", "name": "Branch 09", "qk": 20.0, "ui": 0.8},
    {"id": "B10", "name": "Branch 10", "qk": 18.0, "ui": 1.5},
    {"id": "B11", "name": "Branch 11", "qk": 15.0, "ui": 2.1},
    {"id": "B12", "name": "Branch 12", "qk": 12.0, "ui": 2.8},
    {"id": "B13", "name": "Branch 13", "qk": 10.0, "ui": 3.4},
    {"id": "B14", "name": "Branch 14", "qk": 8.0, "ui": 4.0},
    {"id": "B15", "name": "Branch 15", "qk": 5.0, "ui": 4.7},
    {"id": "B16", "name": "Branch 16", "qk": 0.8, "ui": 5.3},
    {"id": "B17", "name": "Branch 17", "qk": 0.6, "ui": 6.0},
    {"id": "B18", "name": "Branch 18", "qk": 0.4, "ui": 6.8},
    {"id": "B19", "name": "Branch 19", "qk": 0.2, "ui": 7.5},
    {"id": "B20", "name": "Branch 20", "qk": 0.0, "ui": 8.2},
]


def urgency_index(I: float, SIT: float, PO: float,
                  D: float, LT: float, LW: float) -> float:
    if D <= EPS:
        return float('inf')
    return (I + SIT + PO) / D - (LT + LW)


def sort_branches_by_ui(branches: List[dict]) -> List[dict]:
    """Two-group sort for the Sort by UI button.

    Group 1 (UI ≤ 0): sorted by Dispatch Qty Qk descending — higher-quantity
    branches with negative urgency are prioritised first within the group.
    Group 2 (UI > 0): sorted by UI ascending — least urgent last.
    Group 1 always precedes Group 2 in the final list.
    """
    neg = sorted(
        [b for b in branches if float(b.get("ui", 0.0)) <= 0],
        key=lambda b: float(b["qk"]),
        reverse=True,
    )
    pos = sorted(
        [b for b in branches if float(b.get("ui", 0.0)) > 0],
        key=lambda b: float(b.get("ui", 0.0)),
    )
    return neg + pos


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
    # Only run once per browser session. Subsequent reruns use session state
    # directly — re-reading from disk on every rerun would overwrite in-flight
    # edits with stale persisted values (the alternating-edit-lost bug).
    if st.session_state.get("m3_initialized"):
        return

    # 1. Load persisted values from disk first so they win over hard-coded
    #    defaults for any key that was previously saved.
    load_state()

    # 2. Apply hard-coded defaults for any key not present on disk.
    st.session_state.setdefault("m3_mu_stock", MU_STOCK_DEFAULT)
    st.session_state.setdefault("m3_threshold_mode", "Fixed MT")
    st.session_state.setdefault("m3_threshold_value", MIN_THRESHOLD_DEFAULT)
    st.session_state.setdefault("m3_dynamic_demand", DYNAMIC_DEMAND_DEFAULT)
    st.session_state.setdefault("m3_dynamic_days", DYNAMIC_DAYS_DEFAULT)
    st.session_state.setdefault("m3_branch_data", copy.deepcopy(DEFAULT_BRANCHES))

    # 3. Simulation runtime — never persisted.
    st.session_state.setdefault("m3_current_cycle", 0)
    st.session_state.setdefault("m3_cycle_results", [])
    st.session_state.setdefault("m3_new_queue", [])
    st.session_state.setdefault("m3_new_remaining", {})
    st.session_state.setdefault("m3_sim_complete", False)

    st.session_state["m3_initialized"] = True


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
# Branch table helpers
# --------------------------------------------------------------------------

def _commit_from_editor(edited: "pd.DataFrame") -> None:
    """Apply all pending data_editor edits to m3_branch_data by Branch ID match.

    Called before any action that mutates m3_branch_data (sort, add, remove).
    Uses ID-keyed lookup so row order is always taken from the canonical
    m3_branch_data list, never from the editor's current display order.
    """
    branches = st.session_state["m3_branch_data"]
    edited_by_id: dict = {}
    for _, row in edited.iterrows():
        bid = str(row["Branch ID"])
        try:
            qk = float(row["Dispatch Qty Qk (MT)"])
        except (TypeError, ValueError):
            qk = 0.0
        try:
            ui = float(row["UI Score"])
        except (TypeError, ValueError):
            ui = 0.0
        edited_by_id[bid] = {"name": str(row["Branch Name"]), "qk": qk, "ui": ui}

    new_data: List[dict] = []
    for b in branches:
        bid = b["id"]
        if bid in edited_by_id:
            e = edited_by_id[bid]
            new_data.append({"id": bid, "name": e["name"], "qk": e["qk"], "ui": e["ui"]})
        else:
            new_data.append(dict(b))
    st.session_state["m3_branch_data"] = new_data


# --------------------------------------------------------------------------
# Branch table
# --------------------------------------------------------------------------

def _render_branch_table() -> None:
    st.subheader("Branch Configuration")
    branches = st.session_state["m3_branch_data"]

    st.markdown(
        "**Branch Priority Formula — Urgency Index (UI):**  "
        "`UI = (I + SIT + PO) / D − (LT + LW)`  \n"
        "Sort: **UI ≤ 0** → descending Dispatch Qty · "
        "**UI > 0** → ascending UI Score"
    )

    df = pd.DataFrame([
        {"Branch ID": b["id"], "Branch Name": b["name"],
         "Dispatch Qty Qk (MT)": float(b["qk"]),
         "UI Score": float(b.get("ui", 0.0))}
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
            "UI Score": st.column_config.NumberColumn(
                "UI Score",
                step=0.1, format="%.2f",
                help="Urgency Index — lower = higher priority.",
            ),
        },
    )

    # No auto-sync back to m3_branch_data here.
    # Streamlit's data_editor resets its entire diff state whenever the input
    # df changes between renders. Auto-syncing would update m3_branch_data →
    # rebuild df → trigger a diff reset → drop the very next edit (alternating
    # edit-lost bug). Instead, df stays stable across all user edits and edits
    # are committed to m3_branch_data only on explicit button clicks below.

    # Show live total from the editor's current output (not yet committed).
    try:
        total_qk = sum(
            float(row["Dispatch Qty Qk (MT)"])
            for _, row in edited.iterrows()
        )
    except (TypeError, ValueError):
        total_qk = sum(float(b["qk"]) for b in branches)
    st.markdown(
        f"**Total Dispatch Quantity:** {total_qk:.2f} MT "
        f"across {len(branches)} branches"
    )

    c1, c2, c3, _ = st.columns([1, 1, 1, 3])
    with c1:
        if st.button("+ Add Branch", use_container_width=True):
            _commit_from_editor(edited)
            n = len(st.session_state["m3_branch_data"]) + 1
            st.session_state["m3_branch_data"].append({
                "id": f"B{n:02d}",
                "name": f"Branch {n:02d}",
                "qk": 0.0,
                "ui": 0.0,
            })
            st.session_state.pop("m3_branch_editor", None)
            save_state()
            st.rerun()
    with c2:
        disable_remove = len(branches) <= 1
        if st.button(
            "× Remove Last Branch",
            disabled=disable_remove,
            use_container_width=True,
        ):
            _commit_from_editor(edited)
            st.session_state["m3_branch_data"].pop()
            st.session_state.pop("m3_branch_editor", None)
            save_state()
            st.rerun()
    with c3:
        if st.button(
            "🔄 Sort by UI",
            use_container_width=True,
            help=(
                "Sort: UI ≤ 0 → descending Dispatch Qty · "
                "UI > 0 → ascending UI Score"
            ),
        ):
            _commit_from_editor(edited)
            st.session_state["m3_branch_data"] = sort_branches_by_ui(
                st.session_state["m3_branch_data"]
            )
            st.session_state.pop("m3_branch_editor", None)
            save_state()
            st.rerun()


# --------------------------------------------------------------------------
# Simulation algorithm
# --------------------------------------------------------------------------

def _initialise_simulation() -> None:
    branches = st.session_state["m3_branch_data"]
    new_rem: Dict[str, float] = {}
    for b in branches:
        new_rem[b["id"]] = float(b["qk"])

    eligible = [b for b in branches if float(b["qk"]) > EPS]
    # Sort by Urgency Index ascending — lower UI = higher priority.
    eligible_sorted = sorted(eligible, key=lambda b: float(b.get("ui", 0.0)))
    ordered_ids = [b["id"] for b in eligible_sorted]

    st.session_state["m3_new_remaining"] = new_rem
    st.session_state["m3_new_queue"] = list(ordered_ids)


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
    ui_lookup: Dict[str, float] = {
        b["id"]: float(b.get("ui", 0.0))
        for b in st.session_state["m3_branch_data"]
    }

    # Active at start of cycle: in-queue branches with remaining > EPS.
    # Preserve queue order — unserved at top (from previous rotation), served
    # branches at bottom. Within a cycle's cascade passes we keep queue order;
    # only the between-cycle queue rebuild re-sorts unserved by UI ascending.
    active_start = [bid for bid in queue if new_remaining.get(bid, 0.0) > EPS]

    remaining_P = float(P)
    allocations: Dict[str, float] = {}
    all_served: List[str] = []

    # Cascade: keep running eligible-set expansion on the still-unserved-this-cycle
    # branches with whatever stock is left, until P is exhausted or no branch
    # can meet the minimum threshold in any remaining pass.
    while True:
        if remaining_P <= EPS:
            break
        served_set = set(all_served)
        active = [bid for bid in active_start if bid not in served_set]
        if not active:
            break

        k_star = 0
        for k in range(1, len(active) + 1):
            top_k = active[:k]
            total_q = sum(new_remaining[b] for b in top_k)
            if total_q <= EPS:
                break
            trial = {b: new_remaining[b] * remaining_P / total_q for b in top_k}
            min_alloc = min(trial.values())
            if min_alloc < threshold:
                k_star = k - 1
                break
            if k == len(active):
                k_star = k

        if k_star == 0:
            break

        pass_served = list(active[:k_star])
        total_q_star = sum(new_remaining[b] for b in pass_served)
        if total_q_star <= EPS:
            break
        pass_alloc_sum = 0.0
        for bid in pass_served:
            raw = new_remaining[bid] * remaining_P / total_q_star
            alloc = min(raw, new_remaining[bid])
            allocations[bid] = alloc
            pass_alloc_sum += alloc

        all_served.extend(pass_served)
        remaining_P -= pass_alloc_sum

    residual = max(0.0, remaining_P)

    warning = None
    if not active_start:
        warning = "All branches already fulfilled. No allocation needed this cycle."
    elif not all_served:
        warning = (
            "Available stock too low to meet minimum threshold for any branch."
        )

    served_set = set(all_served)
    unserved_ids = [bid for bid in active_start if bid not in served_set]

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

    # New queue: unserved sorted by UI ascending at top, all served
    # this cycle (across cascades, in served order) at bottom.
    unserved_by_ui = sorted(unserved_ids, key=lambda b: ui_lookup.get(b, 0.0))
    st.session_state["m3_new_queue"] = list(unserved_by_ui) + list(all_served)

    return {
        "rows": rows,
        "served_ids": list(all_served),
        "unserved_ids": list(unserved_by_ui),
        "served_ids_moved_to_bottom": list(all_served),
        "residual": residual,
        "total_allocated": sum(allocations.values()),
        "branches_served": len(all_served),
        "branches_active": len(active_start),
        "warning": warning,
    }


def _run_cycle() -> None:
    if st.session_state["m3_current_cycle"] == 0:
        _initialise_simulation()
    st.session_state["m3_current_cycle"] += 1
    P = float(st.session_state["m3_mu_stock"])
    threshold = get_effective_threshold()
    result = _run_new_logic(P, threshold)
    st.session_state["m3_cycle_results"].append({
        "cycle": st.session_state["m3_current_cycle"],
        "P": P,
        "threshold": threshold,
        "result": result,
    })
    if st.session_state["m3_current_cycle"] >= MAX_CYCLES:
        st.session_state["m3_sim_complete"] = True
    save_state()
    st.rerun()


# --------------------------------------------------------------------------
# Sidebar controls (called from app.py when Module 3 is active)
# --------------------------------------------------------------------------

def render_sidebar() -> None:
    cycle = st.session_state.get("m3_current_cycle", 0)
    complete = st.session_state.get("m3_sim_complete", False)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🏭 Simulation Controls")

    if st.sidebar.button(
        "▶ Start / Run Cycle 1",
        type="primary",
        disabled=cycle > 0,
        use_container_width=True,
    ):
        _run_cycle()

    if st.sidebar.button(
        "⏭ Next Cycle",
        disabled=(cycle == 0) or complete,
        use_container_width=True,
    ):
        _run_cycle()

    if st.sidebar.button(
        "↺ Reset Simulation",
        use_container_width=True,
    ):
        for k in (
            "m3_cycle_results", "m3_current_cycle",
            "m3_new_queue", "m3_new_remaining",
            "m3_sim_complete",
        ):
            st.session_state.pop(k, None)
        st.rerun()

    st.sidebar.markdown("---")
    if cycle == 0:
        st.sidebar.caption("No cycles run yet.")
    else:
        st.sidebar.markdown(f"**Cycle {cycle} of {MAX_CYCLES}**")
        st.sidebar.progress(cycle / MAX_CYCLES)

    if complete:
        st.sidebar.success(f"✅ Simulation Complete — all {MAX_CYCLES} cycles run.")


# --------------------------------------------------------------------------
# Bar chart — live snapshot of remaining dispatch requirements
# --------------------------------------------------------------------------

def _render_bar_chart() -> None:
    st.subheader("📊 Dispatch Requirements — Live View")

    branches = st.session_state["m3_branch_data"]
    new_rem = st.session_state.get("m3_new_remaining", {})
    results = st.session_state.get("m3_cycle_results", [])
    cycle = st.session_state.get("m3_current_cycle", 0)

    last_served: set = set()
    if results:
        last_served = set(results[-1]["result"]["served_ids"])

    x_labels: List[str] = []
    y_values: List[float] = []
    bar_colors: List[str] = []
    hover_texts: List[str] = []

    for b in branches:
        bid = b["id"]
        original_qk = float(b["qk"])
        remaining = float(new_rem.get(bid, original_qk)) if new_rem else original_qk

        x_labels.append(bid)
        y_values.append(remaining)
        hover_texts.append(
            f"<b>{bid} — {b['name']}</b><br>"
            f"Original: {original_qk:.2f} MT<br>"
            f"Remaining: {remaining:.2f} MT"
        )

        if remaining <= EPS:
            bar_colors.append("#9e9e9e")    # grey — fulfilled
        elif bid in last_served:
            bar_colors.append("#4caf50")    # green — served last cycle
        else:
            bar_colors.append("#2196f3")    # blue — queued / unserved

    fig = go.Figure(go.Bar(
        x=x_labels,
        y=y_values,
        marker_color=bar_colors,
        text=[f"{v:.1f}" for v in y_values],
        textposition="outside",
        hovertext=hover_texts,
        hoverinfo="text",
        cliponaxis=False,
    ))

    if cycle == 0:
        title = "Initial Dispatch Requirements (all branches)"
    else:
        title = f"Remaining Dispatch Requirements — after Cycle {cycle} of {MAX_CYCLES}"

    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left"),
        xaxis_title="Branch",
        yaxis_title="Remaining (MT)",
        height=440,
        margin=dict(l=40, r=40, t=70, b=60),
        showlegend=False,
        plot_bgcolor="white",
        yaxis=dict(gridcolor="#e0e0e0"),
    )

    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "🔵 Blue = Queued / Unserved  |  "
        "🟢 Green = Served in last cycle  |  "
        "⬜ Grey = Fulfilled (remaining ≈ 0)"
    )


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
    ui_lookup = {
        b["id"]: float(b.get("ui", 0.0))
        for b in st.session_state["m3_branch_data"]
    }
    rows = []
    for r in result["rows"]:
        rows.append({
            "Branch": f"{r['branch_id']} — {r['branch_name']}",
            "UI Score": ui_lookup.get(r["branch_id"], 0.0),
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
        "UI Score": "{:.2f}",
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
            result = entry["result"]
            st.caption(
                f"MU Stock Used: {entry['P']:.2f} MT | "
                f"Threshold: {entry['threshold']:.2f} MT"
            )
            if result.get("warning"):
                st.warning(result["warning"])

            _render_result_table(result)

            m1, m2, m3 = st.columns(3)
            m1.metric("Total Allocated", f"{result['total_allocated']:.2f} MT")
            m2.metric("Residual Stock", f"{result['residual']:.2f} MT")
            m3.metric(
                "Branches Served",
                f"{result['branches_served']} of {result['branches_active']} active",
            )


def _render_cumulative() -> None:
    new_rem = st.session_state["m3_new_remaining"]
    if not new_rem:
        return
    st.subheader("\U0001F4CA Cumulative Allocation Progress")

    rows = []
    for b in st.session_state["m3_branch_data"]:
        bid = b["id"]
        if bid not in new_rem:
            continue
        original = float(b["qk"])
        remaining = float(new_rem[bid])
        rows.append({
            "Branch": f"{bid} — {b['name']}",
            "UI Score": float(b.get("ui", 0.0)),
            "Original Qk (MT)": original,
            "Received (MT)": max(0.0, original - remaining),
            "Remaining (MT)": remaining,
            "Fulfilled": "✅" if remaining <= EPS else "⏳",
        })
    if not rows:
        st.caption("Run a cycle to populate cumulative progress.")
        return

    df = pd.DataFrame(rows)

    def highlight_fulfilled(col: pd.Series) -> List[str]:
        if col.name != "Fulfilled":
            return ["" for _ in col]
        return [
            "background-color: #e8f5e9" if v == "✅" else ""
            for v in col
        ]

    styled = df.style.apply(highlight_fulfilled, axis=0).format({
        "UI Score": "{:.2f}",
        "Original Qk (MT)": "{:.2f}",
        "Received (MT)": "{:.2f}",
        "Remaining (MT)": "{:.2f}",
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
    last_result = results[-1]["result"]
    name_by_id = {b["id"]: b["name"] for b in st.session_state["m3_branch_data"]}
    ui_by_id = {
        b["id"]: float(b.get("ui", 0.0))
        for b in st.session_state["m3_branch_data"]
    }

    unserved = last_result["unserved_ids"]
    served = last_result["served_ids_moved_to_bottom"]

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**⬆️ Priority Queue (Top — Unserved)**")
        rows = [
            {
                "Branch ID": bid,
                "Branch Name": name_by_id.get(bid, ""),
                "UI Score": round(ui_by_id.get(bid, 0.0), 2),
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
                "UI Score": round(ui_by_id.get(bid, 0.0), 2),
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
        "under scarcity using Eligible Set Expansion over up to "
        f"{MAX_CYCLES} cycles. Use the sidebar controls to run cycles."
    )

    _render_clear_button()
    st.divider()
    _render_global_inputs()
    st.divider()
    _render_branch_table()
    st.divider()
    _render_bar_chart()

    if st.session_state["m3_sim_complete"]:
        st.success(
            f"✅ Simulation Complete — all {MAX_CYCLES} cycles have been run. "
            "Click **↺ Reset Simulation** in the sidebar to start over."
        )

    if st.session_state["m3_cycle_results"]:
        st.divider()
        _render_cycle_results()
        st.divider()
        _render_cumulative()
        st.divider()
        _render_queue_panel()

    save_state()
