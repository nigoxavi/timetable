"""Streamlit interface for fair timetable substitutions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from allocation import (
    HistoryStore, dataframe_csv_bytes, dataframe_excel_bytes,
    generate_substitution_plan, monthly_report,
)
from timetable_data import (
    DAY_ORDERS, DEFAULT_HOD, DEFAULT_RESTRICTED_STAFF, MAX_DAILY_PERIODS,
    STAFF, TIMETABLE,
)


st.set_page_config(page_title="Class Substitution Planner", page_icon="📚", layout="wide")
st.title("Class Substitution Planner")
st.caption("Same-class priority • 3-period daily cap • shared-lab protection • fair weekly/monthly rotation")

history_store = HistoryStore(Path(__file__).with_name("allocation_history.csv"))
history = history_store.load()

with st.sidebar:
    st.header("Allocation policy")
    hod_options = ["(None)", *STAFF]
    default_hod_index = hod_options.index(DEFAULT_HOD) if DEFAULT_HOD in hod_options else 0
    hod_value = st.selectbox("HOD (excluded)", hod_options, index=default_hod_index)
    hod = None if hod_value == "(None)" else hod_value
    restricted = st.multiselect(
        "Restricted staff (excluded)", STAFF,
        default=[name for name in DEFAULT_RESTRICTED_STAFF if name in STAFF],
    )
    st.info(f"Maximum total workload: {MAX_DAILY_PERIODS} periods per professor per day.")

left, middle, right = st.columns([1, 1, 2])
with left:
    selected_date = st.date_input("Date", value=date.today())
with middle:
    day_order = st.selectbox("Timetable day order", DAY_ORDERS)
with right:
    absent = st.multiselect("Professors on leave", STAFF)

if st.button("Generate substitution plan", type="primary", use_container_width=True):
    if not absent:
        st.warning("Select at least one professor on leave.")
    else:
        st.session_state["plan"] = generate_substitution_plan(
            TIMETABLE, selected_date, day_order, absent, history,
            hod=hod, restricted_staff=restricted, max_daily_periods=MAX_DAILY_PERIODS,
        )
        st.session_state["plan_key"] = (selected_date.isoformat(), day_order, tuple(sorted(absent)))

plan = st.session_state.get("plan")
if plan is not None:
    st.subheader("Daily plan")
    if plan.empty:
        st.info("The selected absent professors have no classes in this day order.")
    else:
        st.dataframe(plan, use_container_width=True, hide_index=True)
        proposed = int(plan["status"].eq("Proposed").sum())
        skipped = int(plan["status"].str.startswith("Skipped").sum())
        unassigned = int(plan["status"].eq("Unassigned").sum())
        a, b, c = st.columns(3)
        a.metric("Proposed", proposed)
        b.metric("Shared classes skipped", skipped)
        c.metric("Unassigned", unassigned)

        if st.button("Save confirmed allocations", disabled=proposed == 0):
            saved = history_store.save_confirmed(plan)
            if saved:
                st.success(f"Saved {saved} confirmed allocation(s).")
                history = history_store.load()
            else:
                st.info("Nothing new was saved; these allocations may already be in history.")

        daily_name = f"substitution_plan_{plan.iloc[0]['date']}"
        d1, d2 = st.columns(2)
        d1.download_button("Download daily CSV", dataframe_csv_bytes(plan), f"{daily_name}.csv", "text/csv")
        d2.download_button(
            "Download daily Excel", dataframe_excel_bytes(plan, "Daily Plan"),
            f"{daily_name}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()
st.subheader("Monthly extra-class report")
report_col, year_col, month_col = st.columns([2, 1, 1])
with year_col:
    report_year = st.number_input("Year", min_value=2000, max_value=2100, value=selected_date.year, step=1)
with month_col:
    report_month = st.selectbox("Month", range(1, 13), index=selected_date.month - 1)

eligible_report_staff = [name for name in STAFF if name != hod and name not in restricted]
report = monthly_report(history_store.load(), eligible_report_staff, int(report_year), int(report_month))
with report_col:
    st.dataframe(report, use_container_width=True, hide_index=True)

report_name = f"monthly_extra_classes_{int(report_year):04d}_{int(report_month):02d}"
r1, r2 = st.columns(2)
r1.download_button("Download monthly CSV", dataframe_csv_bytes(report), f"{report_name}.csv", "text/csv")
r2.download_button(
    "Download monthly Excel", dataframe_excel_bytes(report, "Monthly Report"),
    f"{report_name}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

with st.expander("Saved allocation history"):
    st.dataframe(history_store.load(), use_container_width=True, hide_index=True)

