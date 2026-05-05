"""TLB Inventory Dispatch Simulation - Streamlit UI."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from simulation import TOTAL_DAYS, DaySnapshot, Simulation

st.set_page_config(
    page_title="TLB Inventory Dispatch Simulator",
    page_icon="📦",
    layout="wide",
)

COLOR_HEX = {
    "GREEN": "#22c55e",
    "YELLOW": "#eab308",
    "RED": "#ef4444",
}


def init_session_state() -> None:
    defaults = {
        "TC": 15,
        "I_init": 10,
        "LT": 7,
        "LW": 2,
        "D": 1,
        "SIT_init": 5,
        "SIT_transit_time": 3,
        "PO_init": 0,
        "sim": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def start_simulation() -> None:
    st.session_state.sim = Simulation(
        TC=int(st.session_state.TC),
        I_init=int(st.session_state.I_init),
        LT=int(st.session_state.LT),
        LW=int(st.session_state.LW),
        D=int(st.session_state.D),
        SIT_init=int(st.session_state.SIT_init),
        SIT_transit_time=int(st.session_state.SIT_transit_time),
        PO_init=int(st.session_state.PO_init),
    )


def reset_simulation() -> None:
    st.session_state.sim = None


def advance_day() -> None:
    sim: Simulation = st.session_state.sim
    if sim is not None and sim.can_advance():
        sim.advance_day()


def render_sidebar() -> None:
    st.sidebar.title("📦 TLB Simulator")
    st.sidebar.markdown("### Parameters")

    sim: Simulation | None = st.session_state.sim
    locked = sim is not None

    st.sidebar.number_input(
        "TC — Total Capacity (days)",
        min_value=1, max_value=200, step=1,
        key="TC", disabled=locked,
        help="Maximum inventory capacity in days of supply.",
    )
    st.sidebar.number_input(
        "I — Initial Inventory (days)",
        min_value=0, max_value=200, step=1,
        key="I_init", disabled=locked,
        help="Starting on-hand inventory in days of supply.",
    )
    st.sidebar.number_input(
        "LT — Lead Time (days)",
        min_value=1, max_value=60, step=1,
        key="LT", disabled=locked,
        help="Days from dispatch to arrival.",
    )
    st.sidebar.number_input(
        "LW — Loading Window (days)",
        min_value=0, max_value=30, step=1,
        key="LW", disabled=locked,
        help="Loading window factored into ROP.",
    )
    st.sidebar.number_input(
        "D — Daily Consumption (days)",
        min_value=1, max_value=50, step=1,
        key="D", disabled=locked,
        help="Daily consumption rate.",
    )

    st.sidebar.markdown("### Day 0 Initial State")

    st.sidebar.number_input(
        "SIT — Stock in Transit at Day 0 (days)",
        min_value=0, max_value=200, step=1,
        key="SIT_init", disabled=locked,
        help="Quantity already in transit at the start of the simulation.",
    )
    st.sidebar.number_input(
        "SIT Transit Time (days)",
        min_value=1, max_value=30, step=1,
        key="SIT_transit_time", disabled=locked,
        help="Days until the initial SIT arrives and moves into Inventory (I).",
    )
    st.sidebar.number_input(
        "Open PO at Day 0 (days)",
        min_value=0, max_value=200, step=1,
        key="PO_init", disabled=locked,
        help=(
            "Quantity awaiting dispatch at Day 0. Moves PO → SIT on Day 2, "
            "then arrives in I after LT additional days."
        ),
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Controls")

    if sim is None:
        st.sidebar.button(
            "▶️ Start Simulation",
            on_click=start_simulation,
            use_container_width=True,
            type="primary",
        )
        st.sidebar.button(
            "⏭️ Next Day",
            disabled=True,
            use_container_width=True,
        )
    else:
        st.sidebar.button(
            "⏭️ Next Day",
            on_click=advance_day,
            disabled=not sim.can_advance(),
            use_container_width=True,
            type="primary",
        )

    st.sidebar.button(
        "🔄 Reset Simulation",
        on_click=reset_simulation,
        use_container_width=True,
        disabled=sim is None,
    )

    if sim is not None:
        st.sidebar.markdown("---")
        day = sim.current_day
        st.sidebar.metric("Current Day", f"Day {day} of {TOTAL_DAYS}")
        st.sidebar.progress(day / TOTAL_DAYS)
        if sim.completed:
            st.sidebar.success("✅ Simulation Complete")


def status_badge(color: str, label: str) -> str:
    hex_color = COLOR_HEX[color]
    return (
        f'<span style="background-color:{hex_color};color:white;'
        f'padding:4px 12px;border-radius:6px;font-weight:600;">{label}</span>'
    )


def render_status_dashboard(sim: Simulation) -> None:
    snap: DaySnapshot = sim.latest()

    header_col, badge_col = st.columns([3, 1])
    with header_col:
        if snap.day == 0:
            st.subheader("Day 0 — Initial State (Not Started)")
        else:
            st.subheader(f"Day {snap.day} of {TOTAL_DAYS}")
    with badge_col:
        st.markdown(
            status_badge(snap.status_color, snap.status_label),
            unsafe_allow_html=True,
        )

    if snap.day > 0:
        c1, c2, c3 = st.columns(3)
        c1.metric("Inventory Before Consumption", snap.I_before)
        c2.metric("After Consumption", snap.I_after_consumption,
                  delta=-(snap.I_before - snap.I_after_consumption) or None)
        c3.metric("Arrivals Today (SIT → I)", snap.arrivals_today)

    st.markdown("#### Current Metrics")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("I — Inventory", snap.I)
    m2.metric("SIT — Stock in Transit", snap.SIT)
    m3.metric("PO — Purchase Orders", snap.PO)
    m4.metric("Total Available (I+SIT+PO)", snap.total)

    n1, n2, n3, n4 = st.columns(4)
    n1.metric("AC — Available Capacity", snap.AC)
    n2.metric("ROP — Reorder Point", snap.ROP)
    n3.metric("TC — Total Capacity", sim.TC)
    trigger_str = "YES" if snap.triggered else "NO" if snap.day > 0 else "—"
    n4.metric("Trigger?", trigger_str)


def render_dispatch_info(sim: Simulation) -> None:
    snap: DaySnapshot = sim.latest()
    if snap.day == 0:
        st.info("Simulation has not started. Click **Next Day** to execute Day 1.")
        return

    st.markdown("#### Dispatch Information")
    if not snap.triggered:
        st.success(f"✅ No Order Needed — Total ({snap.total}) > ROP ({snap.ROP})")
    else:
        if snap.dispatch_status == "DISPATCHED":
            st.warning(
                f"📦 Order Triggered & **DISPATCHED** — "
                f"Q = {snap.Q} (AC + D×(LT+LW) − pending arrivals in window, capped at TC={sim.TC}). "
                f"Expected arrival: **Day {snap.arrival_day}**."
            )
        elif snap.dispatch_status == "NOT_NEEDED":
            st.info(
                "ℹ️ Order Triggered but **NOT NEEDED** — pending in-flight "
                "arrivals during the transit window already cover projected demand "
                "(computed Q ≤ 0)."
            )
        elif snap.dispatch_status == "BLOCKED":
            st.error(
                f"🚫 Order Triggered but **BLOCKED** (Capacity Exceeded) — "
                f"Total ({snap.total}) ≥ TC ({sim.TC})"
            )

    if snap.initial_po_dispatched_today > 0:
        st.info(
            f"📤 Initial Open PO dispatched today: {snap.initial_po_dispatched_today} "
            f"moved from PO → SIT (will arrive in I on Day {snap.day + sim.LT})."
        )

    kind_labels = {
        "system": "System dispatch",
        "initial_sit": "Initial SIT (Day 0)",
        "initial_po": "Initial Open PO",
    }

    if snap.pending_orders:
        st.markdown("##### Pending Orders (in transit, SIT → I)")
        rows = []
        for po in snap.pending_orders:
            rows.append({
                "Source": kind_labels.get(po.kind, po.kind),
                "Dispatch Day": po.dispatch_day,
                "Quantity": po.qty,
                "Expected Arrival Day": po.arrival_day,
                "Days Remaining": max(0, po.arrival_day - snap.day),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.caption("No pending orders in transit.")

    # Show Open PO awaiting dispatch (initial PO not yet moved to SIT)
    if snap.PO > 0:
        days_until_dispatch = max(0, 2 - snap.day)
        st.markdown("##### Open PO (awaiting dispatch)")
        st.dataframe(
            pd.DataFrame([{
                "Source": "Initial Open PO",
                "Quantity": snap.PO,
                "Dispatch Day": 2,
                "Days Until Dispatch": days_until_dispatch,
                "Expected Arrival Day": 2 + sim.LT,
            }]),
            hide_index=True, use_container_width=True,
        )


def render_chart(sim: Simulation) -> None:
    st.markdown("#### Inventory Over Time")
    history = sim.history
    days = [s.day for s in history]
    inv_plus_sit = [s.I + s.SIT for s in history]
    totals = [s.total for s in history]
    rop_value = sim.rop

    fig = go.Figure()

    # Color zones based on ROP ± 2
    x_max = TOTAL_DAYS
    fig.add_hrect(y0=rop_value + 2, y1=max(sim.TC, max(totals + [rop_value]) + 5),
                  fillcolor="green", opacity=0.08, line_width=0,
                  annotation_text="Safe Zone", annotation_position="top left")
    fig.add_hrect(y0=max(0, rop_value - 2), y1=rop_value + 2,
                  fillcolor="yellow", opacity=0.12, line_width=0,
                  annotation_text="Warning", annotation_position="top left")
    fig.add_hrect(y0=0, y1=max(0, rop_value - 2),
                  fillcolor="red", opacity=0.08, line_width=0,
                  annotation_text="Critical", annotation_position="bottom left")

    # ROP horizontal line
    fig.add_hline(
        y=rop_value, line_dash="dash", line_color="#dc2626",
        annotation_text=f"ROP = {rop_value}", annotation_position="right",
    )

    # I + SIT line
    fig.add_trace(go.Scatter(
        x=days, y=inv_plus_sit,
        name="I + SIT",
        mode="lines+markers",
        line=dict(color="#2563eb", width=3),
        marker=dict(size=8),
    ))

    # Inventory only
    fig.add_trace(go.Scatter(
        x=days, y=[s.I for s in history],
        name="I (On Hand)",
        mode="lines",
        line=dict(color="#0ea5e9", width=2, dash="dot"),
    ))

    # Trigger markers (red dots)
    trigger_days = [s.day for s in history if s.triggered]
    trigger_vals = [s.I + s.SIT for s in history if s.triggered]
    if trigger_days:
        fig.add_trace(go.Scatter(
            x=trigger_days, y=trigger_vals,
            name="Trigger",
            mode="markers",
            marker=dict(color="#dc2626", size=14, symbol="circle",
                        line=dict(color="white", width=2)),
        ))

    fig.update_layout(
        xaxis=dict(title="Day", range=[-0.5, x_max + 0.5], dtick=1),
        yaxis=dict(title="Inventory Level (days)"),
        height=450,
        margin=dict(l=40, r=40, t=30, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True)


def render_summary_table(sim: Simulation) -> None:
    st.markdown("#### 20-Day Summary")
    rows = []
    for s in sim.history:
        if s.day == 0:
            continue
        if s.dispatch_status == "DISPATCHED":
            action = f"DISPATCHED Q={s.Q} (arr Day {s.arrival_day})"
        elif s.dispatch_status == "BLOCKED":
            action = "BLOCKED (capacity)"
        elif s.dispatch_status == "NOT_NEEDED":
            action = "NOT NEEDED (in-flight covers demand)"
        else:
            action = "—"
        rows.append({
            "Day": s.day,
            "I": s.I,
            "SIT": s.SIT,
            "PO": s.PO,
            "Total": s.total,
            "ROP": s.ROP,
            "Trigger": "YES" if s.triggered else "NO",
            "Action": action,
            "Status": s.status_label,
        })
    if not rows:
        st.caption("Advance the simulation to populate the summary table.")
        return

    df = pd.DataFrame(rows)

    def color_status(val: str) -> str:
        if "Stockout" in val or "Critical" in val:
            return f"background-color: {COLOR_HEX['RED']}; color: white;"
        if "Warning" in val:
            return f"background-color: {COLOR_HEX['YELLOW']}; color: black;"
        return f"background-color: {COLOR_HEX['GREEN']}; color: white;"

    styled = df.style.map(color_status, subset=["Status"])
    st.dataframe(styled, hide_index=True, use_container_width=True)


def render_main() -> None:
    st.title("📦 TLB Inventory Dispatch Simulator")
    st.caption(
        "Interactive 20-day simulation. Configure parameters in the sidebar, "
        "click **Start Simulation**, then step through with **Next Day**."
    )

    sim: Simulation | None = st.session_state.sim

    if sim is None:
        st.info(
            "👈 Set parameters and click **Start Simulation** to begin. "
            "A Day 0 snapshot will appear once started."
        )
        with st.expander("📐 Formulas & Rules", expanded=True):
            st.markdown(
                """
- **ROP** = D × (10 + LT + LW)
- **Trigger** when (I + SIT + PO) ≤ ROP
- **AC** = TC − I
- **Q** = min(max(0, AC + D × (LT + LW) − pending SIT arrivals in transit window), TC)
- **Block** dispatch when (I + SIT + PO) ≥ TC
- Orders dispatched on Day X arrive on **Day X + LT + LW** (loading window + transit, SIT → I)
- System-generated orders go **directly to SIT** (PO is reserved for the Day-0 Open PO)
- **Initial SIT (Day 0):** moves to I after the user-defined transit time
- **Initial Open PO (Day 0):** moves PO → SIT on **Day 2** (Day 2 already covers the loading window), then SIT → I on **Day 2 + LT**
- Color zones: **Green** > ROP+2 · **Yellow** within ROP±2 · **Red** < ROP−2 or I=0
                """
            )
        return

    if sim.completed:
        st.success("✅ Simulation Complete — review the chart and summary below, "
                   "or click **Reset Simulation** to run again.")

    render_status_dashboard(sim)
    st.markdown("---")
    render_dispatch_info(sim)
    st.markdown("---")
    render_chart(sim)
    st.markdown("---")
    render_summary_table(sim)


def main() -> None:
    init_session_state()
    render_sidebar()
    render_main()


if __name__ == "__main__":
    main()
