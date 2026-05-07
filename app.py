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
        "TC": 10,
        "I_init": 5,
        "LT": 7,
        "LW": 2,
        "D": 1,
        "SIT_init": 5,
        "DD_init": 6,
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
        DD_init=int(st.session_state.DD_init),
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
        help="Loading window factored into arrival timing (LT+LW).",
    )
    st.sidebar.number_input(
        "D — Daily Consumption (days)",
        min_value=1, max_value=50, step=1,
        key="D", disabled=locked,
        help="Daily consumption rate.",
    )

    st.sidebar.markdown("### Day 0 Initial SIT")

    st.sidebar.number_input(
        "SIT[0] — Stock in Transit qty",
        min_value=0, max_value=200, step=1,
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
        c2.metric(
            "After Consumption", snap.I_after_consumption,
            delta=-(snap.I_before - snap.I_after_consumption) or None,
        )
        c3.metric("Arrivals Today (SIT → I)", snap.arrivals_today)

    st.markdown("#### Current Metrics")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("I — Inventory", snap.I)
    m2.metric("SIT_Total", snap.SIT_Total)
    m3.metric("TC — Total Capacity", sim.TC)
    trigger_str = "YES" if snap.triggered else "NO" if snap.day > 0 else "—"
    m4.metric("Trigger?", trigger_str)

    n1, n2, n3 = st.columns(3)
    n1.metric(
        f"Projected I (after {sim.TLT}d)",
        f"{snap.projected_I:g}",
        help="max(0, I − (LT+LW)·D) + sd(SIT, DD, I, LT+LW)",
    )
    n2.metric(
        "75% Floor",
        f"{snap.floor_75:g}",
        help="0.75 × TC — trigger fires when projected I drops to or below this floor.",
    )
    breach_text = "YES" if snap.floor_breached and snap.day > 0 else "NO" if snap.day > 0 else "—"
    n3.metric("Floor Breached?", breach_text)


def render_dispatch_info(sim: Simulation) -> None:
    snap: DaySnapshot = sim.latest()
    if snap.day == 0:
        st.info("Simulation has not started. Click **Next Day** to execute Day 1.")
        if snap.pending_orders:
            _render_pending_table(snap)
        return

    st.markdown("#### Dispatch Information")
    if snap.dispatch_status == "DISPATCHED":
        st.warning(
            f"📦 Order Triggered & **DISPATCHED** — Q = {snap.Q}. "
            f"Will arrive when DD reaches **{sim.TLT}**."
        )
    elif snap.dispatch_status == "NOT_NEEDED":
        st.info(
            "ℹ️ Trigger condition met but **dispatch skipped** — computed Q = 0 "
            "(in-flight SIT already covers projected demand)."
        )
    elif snap.dispatch_status == "NO_TRIGGER":
        st.success(
            f"✅ No Order Needed — projected I ({snap.projected_I:g}) "
            f"> 75% floor ({snap.floor_75:g})."
        )

    if snap.arrivals_today > 0:
        idx_str = ", ".join(f"#{i}" for i in snap.arrival_orders)
        st.success(
            f"🛬 Arrived today: {snap.arrivals_today} unit(s) from order(s) {idx_str} "
            f"(now in I)."
        )

    _render_pending_table(snap)


def _render_pending_table(snap: DaySnapshot) -> None:
    if snap.pending_orders:
        st.markdown("##### Pending Orders (in transit)")
        rows = [
            {
                "Order Index": p.order_index,
                "Quantity": p.qty,
                "Days in Transit (DD)": p.days_in_transit,
                "Days Remaining": p.days_remaining,
            }
            for p in snap.pending_orders
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.caption("No pending orders in transit.")


def render_chart(sim: Simulation) -> None:
    st.markdown("#### Inventory Over Time")
    history = sim.history
    days = [s.day for s in history]
    i_values = [s.I for s in history]
    i_plus_sit = [s.I + s.SIT_Total for s in history]

    floor_75 = sim.floor_75
    floor_50 = sim.floor_50
    tc = sim.TC

    fig = go.Figure()

    # Color zones based on I (status driver)
    chart_top = max(tc + 2, max(i_plus_sit + [floor_75]) + 2)
    fig.add_hrect(
        y0=floor_75, y1=chart_top,
        fillcolor="green", opacity=0.08, line_width=0,
        annotation_text="Safe (I > 75% TC)", annotation_position="top left",
    )
    fig.add_hrect(
        y0=floor_50, y1=floor_75,
        fillcolor="yellow", opacity=0.12, line_width=0,
        annotation_text="Warning (50%–75%)", annotation_position="top left",
    )
    fig.add_hrect(
        y0=0, y1=floor_50,
        fillcolor="red", opacity=0.08, line_width=0,
        annotation_text="Critical (≤ 50%)", annotation_position="bottom left",
    )

    # 75% TC floor line
    fig.add_hline(
        y=floor_75, line_dash="dash", line_color="#f97316",
        annotation_text=f"75% Floor = {floor_75:g}", annotation_position="right",
    )

    # I (solid blue)
    fig.add_trace(go.Scatter(
        x=days, y=i_values,
        name="I (On Hand)",
        mode="lines+markers",
        line=dict(color="#2563eb", width=3),
        marker=dict(size=7),
    ))

    # I + SIT_Total (dotted blue)
    fig.add_trace(go.Scatter(
        x=days, y=i_plus_sit,
        name="I + SIT_Total (committed)",
        mode="lines",
        line=dict(color="#0ea5e9", width=2, dash="dot"),
    ))

    # Trigger markers (red dots) — plotted on the I line
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

    # Arrival markers (green dots) — plotted on the I line
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

    fig.update_layout(
        xaxis=dict(title="Day", range=[-0.5, TOTAL_DAYS + 0.5], dtick=1),
        yaxis=dict(title="Inventory Level (days)", range=[0, chart_top]),
        height=460,
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
            action = f"DISPATCHED Q={s.Q}"
        elif s.dispatch_status == "NOT_NEEDED":
            action = "NOT NEEDED (Q=0)"
        else:
            action = "—"
        rows.append({
            "Day": s.day,
            "I": s.I,
            "SIT_Total": s.SIT_Total,
            f"Projected I (+{sim.TLT}d)": round(s.projected_I, 2),
            "75% Floor": round(s.floor_75, 2),
            "Floor Breached?": "YES" if s.floor_breached else "NO",
            "Trigger": "YES" if s.triggered else "NO",
            "Q": s.Q if s.Q is not None else "—",
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
- **75% Floor** = 0.75 × TC  *(replaces the legacy ROP)*
- **Projected I** = max(0, I − (LT+LW)·D) + sd(SIT, DD, I, LT+LW)
- **Trigger** when Projected I ≤ 75% Floor
- **Q** = max(0, min(term1, term2)) where
    - term1 = TC − max(0, I + SIT_Total − (LT+LW)·D)
    - term2 = TC − max(0, I − (LT+LW)·D) − sd(SIT, DD, I, LT+LW)
- If **Q = 0**, dispatch is skipped (no empty SIT entry).
- Orders are tracked in dictionaries `SIT[k]` (qty) and `DD[k]` (days in transit).
  An order arrives when `DD[k] == LT+LW`.
- Daily flow: **Consume → Process Arrivals → Trigger Check → Dispatch.**
- Status color (driven by **I** alone):
  🟢 I > 0.75·TC · 🟡 0.50·TC < I ≤ 0.75·TC · 🔴 I ≤ 0.50·TC or I = 0.
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
    render_sidebar()
    render_main()


if __name__ == "__main__":
    main()
