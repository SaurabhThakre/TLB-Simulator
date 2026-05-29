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
MU_STOCK_DEFAULT = 50.0
# Fallbacks for branch data loaded from an older state file that predates the
# Lower Cap / Upper Cap columns.
LC_FALLBACK = 2.0
UC_FALLBACK = 1e12  # effectively "no cap"

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

# lc = Lower Cap (minimum dispatch load, MT) — LC = D × (0.5 × TC − I).
# uc = Upper Cap (fixed per SKU-branch production quota, MT) — set by management.
# Both are seeded with dummy values here and are editable in the UI.
DEFAULT_BRANCHES = [
    {"id": "B01", "name": "Branch 01", "qk": 60.0, "ui": -4.5, "lc": 24.0, "uc": 48.0},
    {"id": "B02", "name": "Branch 02", "qk": 55.0, "ui": -3.8, "lc": 22.0, "uc": 44.0},
    {"id": "B03", "name": "Branch 03", "qk": 50.0, "ui": -3.1, "lc": 20.0, "uc": 40.0},
    {"id": "B04", "name": "Branch 04", "qk": 45.0, "ui": -2.6, "lc": 18.0, "uc": 36.0},
    {"id": "B05", "name": "Branch 05", "qk": 40.0, "ui": -1.9, "lc": 16.0, "uc": 32.0},
    {"id": "B06", "name": "Branch 06", "qk": 35.0, "ui": -1.2, "lc": 14.0, "uc": 28.0},
    {"id": "B07", "name": "Branch 07", "qk": 30.0, "ui": -0.5, "lc": 12.0, "uc": 24.0},
    {"id": "B08", "name": "Branch 08", "qk": 25.0, "ui": 0.2, "lc": 10.0, "uc": 20.0},
    {"id": "B09", "name": "Branch 09", "qk": 20.0, "ui": 0.8, "lc": 8.0,  "uc": 16.0},
    {"id": "B10", "name": "Branch 10", "qk": 18.0, "ui": 1.5, "lc": 7.0,  "uc": 14.0},
    {"id": "B11", "name": "Branch 11", "qk": 15.0, "ui": 2.1, "lc": 6.0,  "uc": 12.0},
    {"id": "B12", "name": "Branch 12", "qk": 12.0, "ui": 2.8, "lc": 5.0,  "uc": 9.5},
    {"id": "B13", "name": "Branch 13", "qk": 10.0, "ui": 3.4, "lc": 4.0,  "uc": 8.0},
    {"id": "B14", "name": "Branch 14", "qk": 8.0,  "ui": 4.0, "lc": 3.0,  "uc": 6.5},
    {"id": "B15", "name": "Branch 15", "qk": 5.0,  "ui": 4.7, "lc": 2.0,  "uc": 4.0},
    {"id": "B16", "name": "Branch 16", "qk": 0.8,  "ui": 5.3, "lc": 0.5,  "uc": 0.5},
    {"id": "B17", "name": "Branch 17", "qk": 0.6,  "ui": 6.0, "lc": 0.5,  "uc": 0.5},
    {"id": "B18", "name": "Branch 18", "qk": 0.4,  "ui": 6.8, "lc": 0.5,  "uc": 0.5},
    {"id": "B19", "name": "Branch 19", "qk": 0.2,  "ui": 7.5, "lc": 0.5,  "uc": 0.5},
    {"id": "B20", "name": "Branch 20", "qk": 0.0,  "ui": 8.2, "lc": 0.5,  "uc": 0.5},
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
    # Simulation runtime keys — ALWAYS re-seed these with setdefault so they
    # exist even after the sidebar Reset button clears them and even when
    # render_sidebar() runs before render() in the same rerun.
    st.session_state.setdefault("m3_current_cycle", 0)
    st.session_state.setdefault("m3_cycle_results", [])
    st.session_state.setdefault("m3_new_queue", [])
    st.session_state.setdefault("m3_new_remaining", {})
    st.session_state.setdefault("m3_sim_complete", False)

    # Persistent inputs — only initialise once per browser session.
    # Re-reading disk on every rerun would overwrite in-flight edits with
    # stale persisted values (the alternating-edit-lost bug).
    if st.session_state.get("m3_initialized"):
        return

    # 1. Load persisted values from disk first so they win over hard-coded defaults.
    load_state()

    # 2. Apply hard-coded defaults for any key not present on disk.
    st.session_state.setdefault("m3_mu_stock", MU_STOCK_DEFAULT)
    st.session_state.setdefault("m3_branch_data", copy.deepcopy(DEFAULT_BRANCHES))

    # Backfill Lower/Upper Cap for branch rows loaded from an older state file.
    for b in st.session_state["m3_branch_data"]:
        b.setdefault("lc", LC_FALLBACK)
        b.setdefault("uc", UC_FALLBACK)

    st.session_state["m3_initialized"] = True


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
    st.caption(
        "The minimum dispatch threshold is now per-branch (Lower Cap, LC) and the "
        "maximum is the per-branch Upper Cap (UC) — both set in the Branch "
        "Configuration table below."
    )


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

    def _num(row, col, fallback=0.0):
        try:
            return float(row[col])
        except (TypeError, ValueError, KeyError):
            return fallback

    edited_by_id: dict = {}
    for _, row in edited.iterrows():
        bid = str(row["Branch ID"])
        edited_by_id[bid] = {
            "name": str(row["Branch Name"]),
            "qk": _num(row, "Dispatch Qty Qk (MT)"),
            "ui": _num(row, "UI Score"),
            "lc": _num(row, "Lower Cap LC (MT)", LC_FALLBACK),
            "uc": _num(row, "Upper Cap UC (MT)", UC_FALLBACK),
        }

    new_data: List[dict] = []
    for b in branches:
        bid = b["id"]
        if bid in edited_by_id:
            e = edited_by_id[bid]
            new_data.append({
                "id": bid, "name": e["name"], "qk": e["qk"],
                "ui": e["ui"], "lc": e["lc"], "uc": e["uc"],
            })
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
        "**UI > 0** → ascending UI Score  \n"
        "**Lower Cap (LC):**  `LC = D × (0.5 × TC − I)` — the minimum load a truck "
        "can move. A branch whose share would fall below its LC is excluded from "
        "that allocation.  \n"
        "**Upper Cap (UC):**  a fixed per SKU-branch production quota set by "
        "management — the most the MU may dispatch to that branch in one cycle. "
        "Any allocation above UC is clamped and the surplus is redistributed."
    )

    df = pd.DataFrame([
        {"Branch ID": b["id"], "Branch Name": b["name"],
         "Dispatch Qty Qk (MT)": float(b["qk"]),
         "UI Score": float(b.get("ui", 0.0)),
         "Lower Cap LC (MT)": float(b.get("lc", LC_FALLBACK)),
         "Upper Cap UC (MT)": float(b.get("uc", UC_FALLBACK))}
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
            "Lower Cap LC (MT)": st.column_config.NumberColumn(
                "Lower Cap LC (MT)",
                min_value=0.0, step=0.5, format="%.1f",
                help="LC = D × (0.5 × TC − I). Branch share below LC → excluded.",
            ),
            "Upper Cap UC (MT)": st.column_config.NumberColumn(
                "Upper Cap UC (MT)",
                min_value=0.0, step=0.5, format="%.1f",
                help="Fixed per SKU-branch production quota. Allocation clamped to UC.",
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
                "lc": LC_FALLBACK,
                "uc": UC_FALLBACK,
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
    # Two-group sort: UI ≤ 0 → descending Qk, UI > 0 → ascending UI.
    eligible_sorted = sort_branches_by_ui(eligible)
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
        "lc": float(branch.get("lc", LC_FALLBACK)),
        "uc": float(branch.get("uc", UC_FALLBACK)),
        "remaining_before": remaining_before,
        "allocated": allocated,
        "remaining_after": remaining_after,
        "status": status,
    }


def _run_new_logic(P: float) -> dict:
    """One Module-3 allocation pass for a single TLB cycle.

    Per the proposed logic: rank the queue, run Eligible Set Expansion using each
    branch's own Lower Cap (LC) as the floor, allocate proportionally, clamp every
    allocation to its Upper Cap (UC), pool the freed surplus (ΔP) and redistribute
    it inline — in the same pass — to zero-allocation branches. A single pass per
    cycle; no internal cascade and no carry-forward to the next cycle.
    """
    new_remaining: Dict[str, float] = st.session_state["m3_new_remaining"]
    queue: List[str] = st.session_state["m3_new_queue"]
    lc_lookup: Dict[str, float] = {}
    uc_lookup: Dict[str, float] = {}
    for b in st.session_state["m3_branch_data"]:
        lc_lookup[b["id"]] = float(b.get("lc", LC_FALLBACK))
        uc_lookup[b["id"]] = float(b.get("uc", UC_FALLBACK))

    # Active this cycle: in-queue branches whose remaining demand both is positive
    # and clears their own Lower Cap (a branch with Qk < LC can't justify a truck).
    # Queue order is preserved from the previous rotation (unserved at the top).
    active = [
        bid for bid in queue
        if new_remaining.get(bid, 0.0) > EPS
        and new_remaining.get(bid, 0.0) >= lc_lookup.get(bid, LC_FALLBACK) - EPS
    ]
    # Branches in queue with positive remaining but below their LC — parked, not
    # served this cycle, but still counted as active for reporting/rotation.
    below_lc = [
        bid for bid in queue
        if new_remaining.get(bid, 0.0) > EPS and bid not in active
    ]

    allocations: Dict[str, float] = {}

    # --- Eligible Set Expansion (ESE): largest top-k where every share ≥ its LC.
    k_star = 0
    for k in range(1, len(active) + 1):
        top_k = active[:k]
        total_q = sum(new_remaining[b] for b in top_k)
        if total_q <= EPS:
            break
        trial = {b: new_remaining[b] * P / total_q for b in top_k}
        if all(trial[b] >= lc_lookup[b] - EPS for b in top_k):
            k_star = k
        else:
            break

    served_eligible = list(active[:k_star])

    # --- Raw proportional allocation over the eligible set, then UC + demand clamp.
    delta_p = 0.0
    if served_eligible:
        total_q_star = sum(new_remaining[b] for b in served_eligible)
        for bid in served_eligible:
            raw = new_remaining[bid] * P / total_q_star
            capped = min(raw, uc_lookup[bid], new_remaining[bid])
            allocations[bid] = capped
            delta_p += max(0.0, raw - capped)

    # --- Inline redistribution of the freed surplus ΔP to zero-allocation branches
    # (those active but outside k*), proportional to their remaining demand, each
    # share still subject to its own LC floor and UC ceiling.
    candidates = list(active[k_star:])
    redistributed: Dict[str, float] = {}
    while candidates and delta_p > EPS:
        total_qj = sum(new_remaining[j] for j in candidates)
        if total_qj <= EPS:
            break
        shares = {j: new_remaining[j] * delta_p / total_qj for j in candidates}
        infeasible = [j for j in candidates if shares[j] < lc_lookup[j] - EPS]
        if infeasible:
            # Drop the smallest-share branch that can't clear its LC, then retry.
            worst = min(infeasible, key=lambda j: shares[j])
            candidates.remove(worst)
            continue
        used = 0.0
        for j in candidates:
            give = min(shares[j], uc_lookup[j], new_remaining[j])
            redistributed[j] = give
            used += give
        delta_p -= used
        break

    allocations.update(redistributed)

    # Served order for queue rotation: eligible set first (queue order), then any
    # branches that picked up redistributed surplus.
    all_served = [b for b in served_eligible if allocations.get(b, 0.0) > EPS]
    all_served += [b for b in candidates if redistributed.get(b, 0.0) > EPS]

    total_allocated = sum(allocations.values())
    residual = max(0.0, P - total_allocated)

    active_start = active + below_lc
    active_start_set = set(active_start)
    served_set = set(all_served)
    # Unserved, in queue order: had remaining demand at cycle start but got nothing.
    unserved_ids = [
        bid for bid in queue
        if bid in active_start_set and bid not in served_set
    ]

    warning = None
    if not active_start:
        warning = "All branches already fulfilled. No allocation needed this cycle."
    elif not all_served:
        warning = (
            "Available stock too low: no branch can clear its Lower Cap this cycle."
        )

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

    # Queue rotation: served branches go to the bottom (in served order); every
    # other branch keeps its current queue position so it advances toward the top.
    served_lookup = set(all_served)
    unserved_in_order = [bid for bid in queue if bid not in served_lookup]
    st.session_state["m3_new_queue"] = unserved_in_order + list(all_served)

    return {
        "rows": rows,
        "served_ids": list(all_served),
        "unserved_ids": list(unserved_ids),
        "served_ids_moved_to_bottom": list(all_served),
        "residual": residual,
        "delta_p": delta_p,
        "total_allocated": total_allocated,
        "branches_served": len(all_served),
        "branches_active": len(active_start),
        "warning": warning,
    }


def _run_cycle() -> None:
    if st.session_state["m3_current_cycle"] == 0:
        _initialise_simulation()
    st.session_state["m3_current_cycle"] += 1
    P = float(st.session_state["m3_mu_stock"])
    result = _run_new_logic(P)
    st.session_state["m3_cycle_results"].append({
        "cycle": st.session_state["m3_current_cycle"],
        "P": P,
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
    # Ensure runtime keys exist — this function is called from app.py before
    # render(), so _init_state() must run here too (it is idempotent).
    _init_state()
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
            "LC (MT)": r.get("lc", 0.0),
            "UC (MT)": r.get("uc", 0.0),
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
        "LC (MT)": "{:.2f}",
        "UC (MT)": "{:.2f}",
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
            delta_p = result.get("delta_p", 0.0)
            caption = f"MU Stock Used: {entry['P']:.2f} MT"
            if delta_p > EPS:
                caption += f" | ΔP redistributed inline: {delta_p:.2f} MT"
            st.caption(caption)
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
