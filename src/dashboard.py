from collections import Counter

import streamlit as st

from src.dashboard_data import (
    DEFAULT_DB_PATH,
    confidence_bucket_summary,
    health,
    latest_daily_report,
    latest_signals,
    orders,
    resolved_outcomes,
    risk_events,
    signal_counts,
)


st.set_page_config(page_title="Paper Trading Monitor", layout="wide")
st.title("PAPER TRADING / SIMULATED")
st.caption("READ-ONLY local database dashboard")
if st.button("Refresh"):
    st.rerun()

status = health(DEFAULT_DB_PATH)
st.subheader("Health")
columns = st.columns(4)
columns[0].metric("Last signal", status["last_signal_time"] or "None")
columns[1].metric("Latest report date", status["last_nightly_report_time"] or "None")
columns[2].metric("KILL switch", "Present" if status["kill_switch_exists"] else "Clear")
columns[3].metric("Database size", f"{status['database_size_bytes']:,} bytes")
st.caption(status["database_path"])

st.subheader("Latest nightly report")
report = latest_daily_report(DEFAULT_DB_PATH)
st.text(report.iloc[0]["text"] if not report.empty else "No nightly report available.")

st.subheader("Signals")
st.dataframe(latest_signals(DEFAULT_DB_PATH), hide_index=True, use_container_width=True)
st.subheader("Signal counts")
st.dataframe(signal_counts(DEFAULT_DB_PATH), hide_index=True, use_container_width=True)

st.subheader("Orders")
st.dataframe(orders(DEFAULT_DB_PATH), hide_index=True, use_container_width=True)

st.subheader("Risk rejections by reason")
events = risk_events(DEFAULT_DB_PATH)
rejected = events[events["approved"] == 0] if not events.empty else events
reason_counts: Counter[str] = Counter()
for reasons in rejected.get("reasons", []):
    reason_counts.update(reasons if isinstance(reasons, list) else [str(reasons)])
rejection_summary = sorted(reason_counts.items())
st.dataframe(
    [{"reason": reason, "count": count} for reason, count in rejection_summary],
    hide_index=True,
    use_container_width=True,
)

st.subheader("Outcomes and confidence-bucket summary")
st.dataframe(resolved_outcomes(DEFAULT_DB_PATH), hide_index=True, use_container_width=True)
st.dataframe(
    confidence_bucket_summary(DEFAULT_DB_PATH),
    hide_index=True,
    use_container_width=True,
)
