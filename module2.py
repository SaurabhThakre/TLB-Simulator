"""Module 2 — Truck Capacity Optimization (Streamlit page)."""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

EPS = 1e-9
FILL_THRESHOLD = 0.90

TRUCK_POOL_DEFAULTS = [9, 13, 18, 25, 30]

REQUIRED_COLS = [
    "Material Code",
    "Material",
    "Dry/Wet",
    "Cases/Bags per Pallet",
]

# Pre-fill defaults for the 3 top-up rows (numeric fields only).
TOPUP_DEFAULTS = [
    {"I": 60.0, "AC": 15.0, "D": 3.0, "SIT": 5.0, "PO": 0.0, "LT": 2, "LW": 2, "PC": 10.0},
    {"I": 45.0, "AC": 15.0, "D": 1.5, "SIT": 5.0, "PO": 0.0, "LT": 2, "LW": 2, "PC": 10.0},
    {"I": 55.0, "AC": 20.0, "D": 5.0, "SIT": 0.0, "PO": 0.0, "LT": 2, "LW": 1, "PC": 0.0},
]

NUMERIC_FIELDS = ["I", "AC", "D", "SIT", "PO", "PC"]   # float, step 0.5
INT_FIELDS = ["LT", "LW"]                               # int, step 1


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def _init_state() -> None:
    if "m2_initialized" in st.session_state:
        return
    st.session_state["m2_initialized"] = True
    st.session_state["m2_primary_q"] = 0.0
    st.session_state["m2_truck_pool"] = list(TRUCK_POOL_DEFAULTS)
    for idx, row in enumerate(TOPUP_DEFAULTS):
        for field, val in row.items():
            st.session_state.setdefault(f"m2_topup_{idx}_{field}", val)


# --------------------------------------------------------------------------
# Display helper
# --------------------------------------------------------------------------

def fmt(pallets: float, cases_per_pallet: float) -> str:
    cases = round(pallets * cases_per_pallet)
    return f"{pallets:.3f} MT ({cases} cases)"


# --------------------------------------------------------------------------
# SKU master
# --------------------------------------------------------------------------

def _build_lookup(df: pd.DataFrame) -> Dict[str, dict]:
    lookup: Dict[str, dict] = {}
    for _, row in df.iterrows():
        cpp = row["Cases/Bags per Pallet"]
        # Skip rows with missing Cases/Bags per Pallet
        if pd.isna(cpp):
            continue
        cpp = float(cpp)
        label = f"{row['Material Code']} — {row['Material']} ({int(cpp)} cases/Pallet)"
        lookup[label] = {
            "code": row["Material Code"],
            "material": row["Material"],
            "type": str(row["Dry/Wet"]).strip(),
            "cpp": cpp,
        }
    return lookup


def _render_sku_master() -> Optional[pd.DataFrame]:
    st.subheader("Step 0 — SKU Master")

    if "sku_master" in st.session_state:
        df = st.session_state["sku_master"]
        st.success(f"SKU Master loaded — {len(df)} SKUs available")
        if st.button("Clear SKU Master"):
            del st.session_state["sku_master"]
            st.rerun()
        return df

    uploaded = st.file_uploader("Upload SKU Master", type=["xlsx"])
    if uploaded is not None:
        try:
            df = pd.read_excel(uploaded)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read the Excel file: {exc}")
            return None
        # Normalize column names: strip whitespace, collapse newlines/multiple spaces
        df.columns = [" ".join(col.split()) for col in df.columns]
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            st.error(f"SKU Master is missing required columns: {', '.join(missing)}")
            return None
        st.session_state["sku_master"] = df
        st.rerun()

    st.info("Upload a SKU Master `.xlsx` file to begin Module 2.")
    return None


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def _render_primary_inputs(lookup: Dict[str, dict]) -> None:
    st.subheader("Step 1 — Primary SKU & Dispatch Quantity")

    labels = list(lookup.keys())

    key = "m2_primary_sku"
    if key not in st.session_state or st.session_state[key] not in labels:
        st.session_state[key] = labels[0]

    c1, c2 = st.columns([3, 1])
    with c1:
        st.selectbox("Primary SKU", options=labels, key=key)
        primary_type = lookup[st.session_state[key]]["type"]
        st.session_state["m2_primary_type"] = primary_type
        if primary_type.lower() == "dry":
            st.markdown("\U0001f7e4 **Dry product**")
        elif primary_type.lower() == "wet":
            st.markdown("\U0001f535 **Wet product**")
        else:
            st.markdown(f"**{primary_type} product**")
    with c2:
        st.number_input(
            "Primary Q (Pallets)",
            min_value=0.0, step=0.5, format="%.1f",
            key="m2_primary_q",
            help="Quantity to dispatch, in Pallets (1 Pallet = 1 MT).",
        )

    st.multiselect(
        "Available Truck Sizes (MT)",
        options=TRUCK_POOL_DEFAULTS,
        default=st.session_state.get("m2_truck_pool", list(TRUCK_POOL_DEFAULTS)),
        key="m2_truck_pool",
    )


def _render_topup_table(lookup: Dict[str, dict]) -> None:
    st.subheader("Step 2 — Top-up Candidate SKUs (same branch · same MU)")

    primary_type = st.session_state.get("m2_primary_type", "")
    filtered = [lbl for lbl, info in lookup.items() if info["type"] == primary_type]

    if not filtered:
        st.warning(
            f"No top-up candidates of type **{primary_type or '?'}** in the SKU master."
        )

    for idx in range(3):
        st.markdown(f"**Row {idx + 1}**")
        sku_key = f"m2_topup_{idx}_sku"
        if filtered:
            if sku_key not in st.session_state or st.session_state[sku_key] not in filtered:
                st.session_state[sku_key] = filtered[0]
            st.selectbox(
                f"SKU (Row {idx + 1})", options=filtered, key=sku_key,
                label_visibility="collapsed",
            )
        else:
            st.session_state[sku_key] = None
            st.caption("— no eligible SKU —")

        cols = st.columns(8)
        labels_units = [
            ("I", "Closing Stock (I) — Pallets"),
            ("AC", "Available Capacity (AC) — Pallets"),
            ("D", "Daily Sales (D) — Pallets/day"),
            ("SIT", "Stock in Transit (SIT) — Pallets"),
            ("PO", "Open PO (PO) — Pallets"),
            ("LT", "Lead Time (LT) — days"),
            ("LW", "Loading Window (LW) — days"),
            ("PC", "Plant Stock (PC) — Pallets"),
        ]
        for col, (field, lbl) in zip(cols, labels_units):
            with col:
                if field in INT_FIELDS:
                    st.number_input(
                        lbl, min_value=0, step=1,
                        key=f"m2_topup_{idx}_{field}",
                    )
                else:
                    st.number_input(
                        lbl, min_value=0.0, step=0.5, format="%.1f",
                        key=f"m2_topup_{idx}_{field}",
                    )
        st.divider()


# --------------------------------------------------------------------------
# Algorithm
# --------------------------------------------------------------------------

def _priority_index(I: float, SIT: float, PO: float, D: float, LT: float, LW: float) -> float:
    return (I + SIT + PO) / (D * (10 + LT + LW))


def _pack_primary(Q: float, pool: List[int]) -> List[dict]:
    """Greedy truck packing for the primary quantity.

    - While Q does not fit in a single truck (Q >= largest), use the largest
      truck fully loaded and repeat.
    - Otherwise pick the smallest truck whose capacity >= remaining Q
      (exact fit allowed); load the remainder there (it may have spare space
      that the top-up loop will use).
    """
    pool_sorted = sorted(pool)
    trucks: List[dict] = []
    if Q <= EPS or not pool_sorted:
        return trucks
    max_truck = max(pool_sorted)
    remaining = float(Q)
    guard = 0
    while remaining > EPS and guard < 10_000:
        guard += 1
        fitting = [t for t in pool_sorted if t >= remaining - EPS]
        if fitting:
            size = min(fitting)
            load = remaining
            remaining = 0.0
        else:
            size = max_truck
            load = float(size)
            remaining -= size
        trucks.append({
            "size_mt": int(size),
            "loads": [{"sku": None, "pallets": float(load), "kind": "primary"}],
            "current_load": float(load),
        })
    return trucks


def _optimize(lookup: Dict[str, dict]) -> dict:
    primary_label = st.session_state["m2_primary_sku"]
    primary_q = float(st.session_state["m2_primary_q"])
    pool = sorted(int(t) for t in st.session_state["m2_truck_pool"])

    trucks = _pack_primary(primary_q, pool)
    for tr in trucks:
        for ld in tr["loads"]:
            if ld["kind"] == "primary":
                ld["sku"] = primary_label

    # Gather eligible top-up rows.
    topups: List[dict] = []
    for idx in range(3):
        sku_label = st.session_state.get(f"m2_topup_{idx}_sku")
        if not sku_label:
            continue
        I = float(st.session_state[f"m2_topup_{idx}_I"])
        AC = float(st.session_state[f"m2_topup_{idx}_AC"])
        D = float(st.session_state[f"m2_topup_{idx}_D"])
        SIT = float(st.session_state[f"m2_topup_{idx}_SIT"])
        PO = float(st.session_state[f"m2_topup_{idx}_PO"])
        LT = float(st.session_state[f"m2_topup_{idx}_LT"])
        LW = float(st.session_state[f"m2_topup_{idx}_LW"])
        PC = float(st.session_state[f"m2_topup_{idx}_PC"])
        if D == 0 or PC == 0:
            continue
        TC = I + AC
        sku_cap = min(AC + D * (LT + LW), TC, PC)
        topups.append({
            "row": idx, "label": sku_label,
            "I": I, "AC": AC, "D": D, "SIT": SIT, "PO": PO, "LT": LT, "LW": LW, "PC": PC,
            "i_score": _priority_index(I, SIT, PO, D, LT, LW),
            "TC": TC, "sku_cap": sku_cap,
        })
    topups.sort(key=lambda t: t["i_score"])

    loaded_map: Dict[int, float] = {}
    below_threshold = False

    if trucks:
        last = trucks[-1]
        cap = last["size_mt"]
        if (last["current_load"] / cap) < FILL_THRESHOLD:
            for t in topups:
                TrC = cap - last["current_load"]
                if TrC <= EPS:
                    break
                TQ = min(TrC, min(t["AC"] + t["D"] * (t["LT"] + t["LW"]), t["TC"]), t["PC"])
                TQ = max(0.0, TQ)
                if TQ > EPS:
                    last["loads"].append({"sku": t["label"], "pallets": float(TQ), "kind": "topup"})
                    last["current_load"] += TQ
                    loaded_map[t["row"]] = loaded_map.get(t["row"], 0.0) + TQ
                    if (last["current_load"] / cap) >= FILL_THRESHOLD:
                        break
            if (last["current_load"] / cap) < FILL_THRESHOLD:
                below_threshold = True

    truck_results: List[dict] = []
    total_loaded = 0.0
    for tr in trucks:
        cap = tr["size_mt"]
        fill_pct = (tr["current_load"] / cap * 100.0) if cap else 100.0
        truck_results.append({
            "size_mt": cap,
            "loads": tr["loads"],
            "fill_pct": fill_pct,
            "dispatched": fill_pct >= FILL_THRESHOLD * 100.0,
        })
        total_loaded += tr["current_load"]

    topup_ranking: List[dict] = []
    for rank, t in enumerate(topups, start=1):
        topup_ranking.append({
            "rank": rank,
            "sku": t["label"],
            "i_score": t["i_score"],
            "TC_pallets": t["TC"],
            "TQ_cap_pallets": t["sku_cap"],
            "loaded_pallets": loaded_map.get(t["row"], 0.0),
        })

    avg_fill = (
        sum(tr["fill_pct"] for tr in truck_results) / len(truck_results)
        if truck_results else 0.0
    )

    return {
        "primary_sku": primary_label,
        "primary_q_pallets": primary_q,
        "trucks": truck_results,
        "topup_ranking": topup_ranking,
        "plan_summary": {
            "primary_q_pallets": primary_q,
            "num_trucks": len(truck_results),
            "total_loaded_pallets": total_loaded,
            "avg_fill_pct": avg_fill,
            "below_threshold": below_threshold,
        },
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def _render_results(results: dict, lookup: Dict[str, dict]) -> None:
    summary = results["plan_summary"]
    primary_label = results["primary_sku"]
    primary_cpp = lookup.get(primary_label, {}).get("cpp", 1.0)

    st.subheader("Plan Summary")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("**PRIMARY Q**")
        st.markdown(
            f"<span style='color:#dc2626;font-size:1.25rem;font-weight:700'>"
            f"{fmt(summary['primary_q_pallets'], primary_cpp)}</span>",
            unsafe_allow_html=True,
        )
    c2.metric("TRUCKS", summary["num_trucks"])
    c3.metric("TOTAL LOADED", f"{summary['total_loaded_pallets']:.3f} MT")
    with c4:
        st.markdown("**AVG FILL**")
        color = "#16a34a" if summary["avg_fill_pct"] >= 90.0 else "#dc2626"
        st.markdown(
            f"<span style='color:{color};font-size:1.25rem;font-weight:700'>"
            f"{summary['avg_fill_pct']:.1f}%</span>",
            unsafe_allow_html=True,
        )

    if summary["below_threshold"]:
        st.warning(
            "⚠️ Last truck is below 90% fill after top-up. Consider waiting for "
            "more SKUs or adjusting dispatch timing."
        )

    st.divider()
    st.subheader("Top-up Priority Ranking")
    ranking = results["topup_ranking"]
    if not ranking:
        st.caption("No eligible top-up SKUs (D = 0 or PC = 0 for all rows).")
    else:
        rows = []
        for r in ranking:
            cpp = lookup.get(r["sku"], {}).get("cpp", 1.0)
            loaded = r["loaded_pallets"]
            rows.append({
                "#": r["rank"],
                "SKU": r["sku"],
                "i Score": round(r["i_score"], 4),
                "TC (MT / cases)": fmt(r["TC_pallets"], cpp),
                "TQ Cap (MT / cases)": fmt(r["TQ_cap_pallets"], cpp),
                "Loaded (MT / cases)": fmt(loaded, cpp) if loaded > EPS else "—",
            })
        df_rank = pd.DataFrame(rows)

        def _hl_loaded(row):
            if row["Loaded (MT / cases)"] != "—":
                return ["background-color: #dcfce7"] * len(row)
            return [""] * len(row)

        st.dataframe(
            df_rank.style.apply(_hl_loaded, axis=1),
            hide_index=True, use_container_width=True,
        )

    st.divider()
    st.subheader("Per-Truck Plan")
    trucks = results["trucks"]
    if not trucks:
        st.info("Nothing to dispatch (Primary Q = 0).")
        return

    for n, tr in enumerate(trucks, start=1):
        with st.container(border=True):
            if tr["dispatched"]:
                badge = (
                    "<span style='background:#16a34a;color:white;padding:2px 10px;"
                    "border-radius:6px;font-weight:600'>✓ DISPATCHED</span>"
                )
            else:
                badge = (
                    "<span style='background:#dc2626;color:white;padding:2px 10px;"
                    "border-radius:6px;font-weight:600'>⚠ BELOW THRESHOLD</span>"
                )
            st.markdown(
                f"#### Truck {n} — {tr['size_mt']} MT &nbsp;&nbsp; {badge}",
                unsafe_allow_html=True,
            )
            loaded_mt = sum(ld["pallets"] for ld in tr["loads"])
            st.progress(
                min(1.0, tr["fill_pct"] / 100.0),
                text=f"Fill: {tr['fill_pct']:.1f}% — {loaded_mt:.3f} / {tr['size_mt']} MT",
            )
            load_rows = []
            for ld in tr["loads"]:
                cpp = lookup.get(ld["sku"], {}).get("cpp", 1.0)
                load_rows.append({
                    "SKU": ld["sku"],
                    "Load (MT / cases)": fmt(ld["pallets"], cpp),
                    "Kind": ld["kind"],
                })
            df_loads = pd.DataFrame(load_rows)

            def _hl_kind(row):
                if row["Kind"] == "topup":
                    return ["background-color: #dbeafe"] * len(row)
                return [""] * len(row)

            st.dataframe(
                df_loads.style.apply(_hl_kind, axis=1),
                hide_index=True, use_container_width=True,
            )


# --------------------------------------------------------------------------
# Page entry point
# --------------------------------------------------------------------------

def render() -> None:
    _init_state()

    st.title("\U0001f69b Module 2 — Truck Capacity Optimization")
    st.caption(
        "Pack a single-SKU dispatch from the MU to a Branch Warehouse and "
        "top-up the last truck with same-branch, same-type SKUs."
    )

    df = _render_sku_master()
    if df is None:
        st.stop()

    lookup = _build_lookup(df)
    if not lookup:
        st.error("The SKU Master contains no rows.")
        st.stop()

    # Ensure top-up defaults are set before any widgets render them.
    # Uses per-key check so user edits are never overwritten.
    for i, defaults in enumerate(TOPUP_DEFAULTS):
        for field, value in defaults.items():
            key = f"m2_topup_{i}_{field}"
            if key not in st.session_state:
                st.session_state[key] = value

    st.divider()
    _render_primary_inputs(lookup)
    st.divider()
    _render_topup_table(lookup)
    st.divider()

    st.subheader("Step 3 — Optimize")
    b1, b2 = st.columns([1, 1])
    run = b1.button("Optimize Trucks", type="primary", use_container_width=True)
    if b2.button("Clear Results", use_container_width=True):
        st.session_state.pop("m2_results", None)
        st.rerun()

    if run:
        if not st.session_state["m2_truck_pool"]:
            st.error("Select at least one truck size in the pool.")
        else:
            with st.spinner("Optimizing trucks..."):
                st.session_state["m2_results"] = _optimize(lookup)

    if "m2_results" in st.session_state:
        st.divider()
        _render_results(st.session_state["m2_results"], lookup)
