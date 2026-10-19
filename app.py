"""Streamlit entry point: streamlit run app.py."""
import streamlit as st

from clinical_planner.db import get_engine, init_db, session_scope
from clinical_planner.seed import seed_data
from clinical_planner.security import require_auth
from clinical_planner.ui import render_app

st.set_page_config(page_title="Clinical Executive Planner · V2.0", page_icon="🩺", layout="wide")
engine = get_engine()
init_db(engine)
require_auth(engine)
with session_scope(engine) as session:
    seed_data(session)
    session.commit()
    render_app(session)
