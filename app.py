"""TLB Inventory Dispatch Simulation - Streamlit UI."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import module2
import module3
from simulation import TOTAL_DAYS, DaySnapshot, Simulation

PAGE_M1 = "Module 1 — Dispatch Simulation"
PAGE_M2 = "Module 2 — Truck Capacity Optimization"
PAGE_M3 = "Module 3 — Inventory Allocation Logic"

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
        "TC": 10.0,
        "I_init": 5.0,
        "LT": 7,
        "LW": 2,
        "D": 1.0,
        "SIT_init": 0.0,
        "DD_init": 0,
        "threshold_pct": 75,
        "sim": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def start_simulation() -> None:
    st.session_state.sim = Simulation(
        TC=float(st.session_state.TC),
        I_init=float(st.session_state.I_init),
        LT=int(st.session_state.LT),
        LW=int(st.session_state.LW),
        D=float(st.session_state.D),
        SIT_init=float(st.session_state.SIT_init),
        DD_init=int(st.session_state.DD_init),
        threshold_pct=float(st.session_state.threshold_pct) / 100.0,
    )


def reset_simulation() -> None:
    st.session_state.sim = None


def advance_day() -> None:
    sim: Simulation = st.session_state.sim
    if sim is not None and sim.can_advance():
        sim.advance_day()
        st.session_state.chart_window_start = None  # Reset chart to latest window


def render_sidebar() -> None:
    st.sidebar.title("📦 TLB Simulator")
    st.sidebar.markdown("### Parameters")

    sim: Simulation | None = st.session_state.sim
    locked = sim is not None

    st.sidebar.number_input(
        "TC — Total Capacity (days)",
        min_value=0.1, max_value=200.0, step=0.1, format="%.4f",
        key="TC", disabled=locked,
        help="Maximum inventory capacity in days of supply.",
    )
    st.sidebar.number_input(
        "I — Initial Inventory (days)",
        min_value=0.0, max_value=200.0, step=0.1, format="%.4f",
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
        help="Loading window factored into arrival timing (LT+LW).",
    )
    st.sidebar.number_input(
        "D — Daily Consumption (days)",
        min_value=0.1, max_value=50.0, step=0.1, format="%.4f",
        key="D", disabled=locked,
        help="Daily consumption rate.",
    )

    st.sidebar.markdown("### Trigger Threshold")
    st.sidebar.number_input(
        "Threshold — Trigger Floor (% of TC)",
        min_value=1, max_value=99, step=1,
        key="threshold_pct", disabled=locked,
        help="Trigger fires when projected I ≤ (this %) × TC. Default 75.",
    )

    st.sidebar.markdown("### Day 0 Initial SIT")

    st.sidebar.number_input(
        "SIT[0] — Stock in Transit qty",
        min_value=0.0, max_value=200.0, step=0.1, format="%.4f",
        key="SIT_init", disabled=locked,
        help="Initial Stock in Transit quantity at Day 0.",
    )
    st.sidebar.number_input(
        "DD[0] — Days Already in Transit",
        min_value=0, max_value=60, step=1,
        key="DD_init", disabled=locked,
        help="How many days ago this stock was dispatched. Arrives when DD reaches LT+LW.",
    )

    tlt = int(st.session_state.LT) + int(st.session_state.LW)
    days_remaining = max(0, tlt - int(st.session_state.DD_init))
    st.sidebar.caption(
        f"📍 Days Remaining = (LT+LW) − DD[0] = ({tlt} − {int(st.session_state.DD_init)}) "
        f"= **{days_remaining}** day(s)"
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


CHART_WINDOW = 20


def get_chart_window(current_day: int) -> tuple[int, int]:
    """Return (x_start, x_end) for the fixed CHART_WINDOW-step sliding window."""
    x_end = max(CHART_WINDOW, current_day)
    x_start = x_end - (CHART_WINDOW - 1)
    return x_start, x_end


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def render_status_dashboard(sim: Simulation) -> None:
    snap: DaySnapshot = sim.latest()
    pct = int(round(sim.threshold_pct * 100))

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
        c1.metric("Day Start (Before Consume)", _fmt(snap.I_before))
        c2.metric("After Consumption", _fmt(snap.I_after_consumption))
        c3.metric("After Arrivals", _fmt(snap.I_after_arrivals))

    st.markdown("#### Current Metrics")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("I — Inventory", _fmt(snap.I))
    m2.metric("SIT_Total", _fmt(snap.SIT_Total))
    m3.metric("TC — Total Capacity", _fmt(sim.TC))
    trigger_str = "YES" if snap.triggered else "NO" if snap.day > 0 else "—"
    m4.metric("Trigger?", trigger_str)

    n1, n2, n3 = st.columns(3)
    n1.metric(
        f"Projected I (after {sim.TLT}d)",
        _fmt(snap.projected_I),
        help="Forward-simulated I over LT+LW days, processing arrivals then consumption each day.",
    )
    n2.metric(
        f"{pct}% Floor",
        _fmt(snap.threshold_floor),
        help=f"{pct}% × TC — trigger fires when projected I drops to or below this floor.",
    )
    breach_text = "YES" if snap.floor_breached and snap.day > 0 else "NO" if snap.day > 0 else "—"
    n3.metric("Floor Breached?", breach_text)


def render_dispatch_info(sim: Simulation) -> None:
    snap: DaySnapshot = sim.latest()
    pct = int(round(sim.threshold_pct * 100))
    if snap.day == 0:
        st.info("Simulation has not started. Click **Next Day** to execute Day 1.")
        if snap.pending_orders:
            _render_pending_table(snap)
        return

    st.markdown("#### Dispatch Information")
    if snap.dispatch_status == "DISPATCHED":
        st.warning(
            f"📦 Order Triggered & **DISPATCHED** — Q = {_fmt(snap.Q)}. "
            f"Will arrive when DD reaches **{sim.TLT}**."
        )
    elif snap.dispatch_status == "NO_TRIGGER":
        st.success(
            f"✅ No Order Needed — projected I ({_fmt(snap.projected_I)}) "
            f"> {pct}% floor ({_fmt(snap.threshold_floor)})."
        )

    if snap.arrivals_today > 0:
        idx_str = ", ".join(f"#{i}" for i in snap.arrival_orders)
        st.success(
            f"🛬 Arrived today: {_fmt(snap.arrivals_today)} unit(s) from order(s) {idx_str} "
            f"(now in I)."
        )

    _render_pending_table(snap)


def _render_pending_table(snap: DaySnapshot) -> None:
    if snap.pending_orders:
        st.markdown("##### Pending Orders (in transit)")
        rows = [
            {
                "Order Index": p.order_index,
                "Quantity": _fmt(p.qty),
                "Days in Transit (DD)": p.days_in_transit,
                "Days Remaining": p.days_remaining,
            }
            for p in snap.pending_orders
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.caption("No pending orders in transit.")


def render_chart(sim: Simulation) -> None:
    pct = int(round(sim.threshold_pct * 100))
    st.markdown("#### Inventory Over Time")
    history = sim.history
    days = [s.day for s in history]
    i_values = [s.I for s in history]
    i_plus_sit = [s.I + s.SIT_Total for s in history]

    threshold_floor = sim.threshold_floor
    floor_50 = sim.floor_50
    tc = sim.TC

    fig = go.Figure()

    chart_top = max(tc + 2, max(i_plus_sit + [threshold_floor]) + 2)

    # Color zones (option a): GREEN > threshold, YELLOW between 50% and threshold, RED ≤ 50%
    fig.add_hrect(
        y0=threshold_floor, y1=chart_top,
        fillcolor="green", opacity=0.08, line_width=0,
        annotation_text=f"Safe (I > {pct}% TC)", annotation_position="top left",
    )
    if threshold_floor > floor_50:
        fig.add_hrect(
            y0=floor_50, y1=threshold_floor,
            fillcolor="yellow", opacity=0.12, line_width=0,
            annotation_text=f"Warning (50%–{pct}%)", annotation_position="top left",
        )
    fig.add_hrect(
        y0=0, y1=floor_50,
        fillcolor="red", opacity=0.08, line_width=0,
        annotation_text="Critical (≤ 50%)", annotation_position="bottom left",
    )

    # Threshold floor line
    fig.add_hline(
        y=threshold_floor, line_dash="dash", line_color="#f97316",
        annotation_text=f"{pct}% Floor = {threshold_floor:.4f}", annotation_position="right",
    )

    fig.add_trace(go.Scatter(
        x=days, y=i_values,
        name="I (On Hand)",
        mode="lines+markers",
        line=dict(color="#2563eb", width=3),
        marker=dict(size=7),
    ))

    fig.add_trace(go.Scatter(
        x=days, y=i_plus_sit,
        name="I + SIT_Total (committed)",
        mode="lines",
        line=dict(color="#0ea5e9", width=2, dash="dot"),
    ))

    trig_days = [s.day for s in history if s.triggered]
    trig_vals = [s.I for s in history if s.triggered]
    if trig_days:
        fig.add_trace(go.Scatter(
            x=trig_days, y=trig_vals,
            name="Trigger",
            mode="markers",
            marker=dict(color="#dc2626", size=14, symbol="circle",
                        line=dict(color="white", width=2)),
        ))

    arr_days = [s.day for s in history if s.arrivals_today > 0]
    arr_vals = [s.I for s in history if s.arrivals_today > 0]
    if arr_days:
        fig.add_trace(go.Scatter(
            x=arr_days, y=arr_vals,
            name="Arrival",
            mode="markers",
            marker=dict(color="#16a34a", size=14, symbol="diamond",
                        line=dict(color="white", width=2)),
        ))

    # Initialize manual window position state
    if "chart_window_start" not in st.session_state:
        st.session_state.chart_window_start = None

    # Determine x-axis range: auto-scroll to latest or show manual position
    if sim.current_day > 0 and st.session_state.chart_window_start is not None:
        # User has manually scrolled — show that window
        x_start = st.session_state.chart_window_start
        x_end = min(x_start + CHART_WINDOW - 1, sim.current_day)
    else:
        # Auto-scroll to latest window
        x_start, x_end = get_chart_window(sim.current_day)

    fig.update_layout(
        xaxis=dict(title="Day", range=[x_start - 0.5, x_end + 0.5], dtick=1),
        yaxis=dict(title="Inventory Level (days)", range=[0, chart_top]),
        height=460,
        margin=dict(l=40, r=40, t=30, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True)

    # Scrollbar for manual window navigation (below chart)
    if sim.current_day > 0:
        max_window_start = max(1, sim.current_day - CHART_WINDOW + 1)
        col1, col2 = st.columns([5, 1])
        with col1:
            st.slider(
                "🔍 Navigate history (window start day):",
                min_value=1,
                max_value=max_window_start,
                value=st.session_state.chart_window_start or max_window_start,
                step=1,
                key="chart_window_start",
                label_visibility="collapsed",
            )
        with col2:
            if st.button("⏭ Latest", use_container_width=True, key="reset_chart_window"):
                st.session_state.chart_window_start = None
                st.rerun()


def render_summary_table(sim: Simulation) -> None:
    pct = int(round(sim.threshold_pct * 100))
    st.markdown(f"#### {TOTAL_DAYS}-Day Summary")
    rows = []
    for s in sim.history:
        if s.day == 0:
            continue
        if s.dispatch_status == "DISPATCHED":
            action = f"DISPATCHED Q={s.Q:.4f}"
        else:
            action = "—"
        rows.append({
            "Day": s.day,
            "I": round(s.I, 4),
            "SIT_Total": round(s.SIT_Total, 4),
            f"Projected I (+{sim.TLT}d)": round(s.projected_I, 4),
            f"{pct}% Floor": round(s.threshold_floor, 4),
            "Floor Breached?": "YES" if s.floor_breached else "NO",
            "Trigger": "YES" if s.triggered else "NO",
            "Q": round(s.Q, 4) if s.Q is not None else "—",
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
    st.dataframe(
        styled,
        height=400,
        hide_index=True,
        use_container_width=True,
    )


def render_main() -> None:
    st.title("📦 TLB Inventory Dispatch Simulator")
    st.caption(
        f"Interactive {TOTAL_DAYS}-day simulation. Configure parameters in the sidebar, "
        "click **Start Simulation**, then step through with **Next Day**. "
        f"The chart slides forward in a {CHART_WINDOW}-day window as you advance."
    )

    sim: Simulation | None = st.session_state.sim

    if sim is None:
        threshold = int(st.session_state.threshold_pct)
        st.info(
            "👈 Set parameters and click **Start Simulation** to begin. "
            "A Day 0 snapshot will appear once started."
        )
        with st.expander("📐 Formulas & Rules", expanded=True):
            st.markdown(
                f"""
- **Threshold Floor** = (Threshold % / 100) × TC  *(currently {threshold}% × TC)*
- **Projected I** = forward-simulate I over (LT+LW) days, processing arrivals then consumption each day (no new dispatch).
- **Trigger** when Projected I ≤ Threshold Floor
- **Q** = max(0, TC − Projected I)
- Orders are tracked in dictionaries `SIT[k]` (qty) and `DD[k]` (days in transit).
  An order arrives when `DD[k] == LT+LW`. New dispatch reuses slot `j` if `SIT[j] == 0`, else `j` is incremented.
- Daily flow: **Consume → Process Arrivals → Trigger Check → Dispatch.**
- New dispatches start with `DD=1` so the order arrives exactly `LT+LW` days later.
- Status color (driven by **I** alone, with configurable threshold):
  🟢 I > threshold·TC · 🟡 0.50·TC < I ≤ threshold·TC · 🔴 I ≤ 0.50·TC or I = 0.
                """
            )
        return

    if sim.completed:
        st.success(
            "✅ Simulation Complete — review the chart and summary below, "
            "or click **Reset Simulation** to run again."
        )

    render_status_dashboard(sim)
    st.markdown("---")
    render_dispatch_info(sim)
    st.markdown("---")
    render_chart(sim)
    st.markdown("---")
    render_summary_table(sim)


def main() -> None:
    init_session_state()
    page = st.sidebar.radio(
        "Navigation", [PAGE_M1, PAGE_M2, PAGE_M3], key="active_page"
    )
    st.sidebar.divider()
    if page == PAGE_M1:
        render_sidebar()
        render_main()
    elif page == PAGE_M2:
        module2.render()
    else:
        module3.render_sidebar()
        module3.render()


if __name__ == "__main__":
    main()
