"""Run from the repository root: python -m streamlit run app/streamlit_app.py."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app.service import ConsoleService, SCENARIOS, SCENARIO_DESCRIPTIONS, readiness
from nightwatch.docker_runner import DEFAULT_IMAGE
import os

st.set_page_config(page_title="Nightwatch · On-call console", page_icon="◉", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1440px; padding-top: 2.6rem;}
[data-testid="stMetric"] {background: #19212b; border: 1px solid #2a3644; padding: 16px; border-radius: 12px;}
[data-testid="stMetricValue"] {font-size: clamp(1rem, 1.5vw, 1.6rem);}
[data-testid="stSidebar"] {border-right: 1px solid #2a3644;}
h1 {letter-spacing: -.045em;} h3 {letter-spacing: -.02em;}
.eyebrow {color: #c5ec86; font-size: 11px; font-weight: 700; letter-spacing: .18em; margin-bottom: 12px;}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def service():
    return ConsoleService(ROOT)


@st.cache_data(ttl=15)
def sandbox_status(image):
    return readiness(image)


def clean(text):
    key = service().settings.api_key
    return str(text).replace(key, "[redacted]") if key else str(text)


def json_text(value):
    return clean(json.dumps(value, indent=2))


def checks(verdict):
    if verdict:
        st.text(clean(verdict.reason))
        st.dataframe([{"Check": name.replace("_", " ").capitalize(), "Result": "PASS" if ok else "FAIL"}
                      for name, ok in verdict.checks.items()], hide_index=True, width="stretch")


def run_details(label, result):
    if result:
        with st.expander(f"{label} · {result.status} · {result.duration_s:.2f}s"):
            st.caption(f"Commit {result.commit[:12]} · exit {result.exit_code} · {len(result.passed)} tests passed")
            if result.failures:
                st.dataframe([{"Test": f.nodeid, "Exception": f.exception_type, "Message": clean(f.message),
                               "Location": f"{f.file or '—'}:{f.function or '—'}"} for f in result.failures],
                             hide_index=True, width="stretch")
            st.code(clean(result.output_tail) or "No output captured.", language="text")


def incident_view(incident, mode, running):
    metadata = console.metadata(incident.id)
    status = incident.state.value
    st.caption(f"{incident.id} / {incident.created_at:%d %b %Y · %H:%M UTC}")
    st.subheader(incident.alert.title)
    st.caption("SCRIPTED DEMO · prepared model responses, real Docker verification" if mode == "demo"
               else "LIVE MODEL · NVIDIA Nemotron via Nebius Token Factory")
    columns = st.columns(4)
    columns[0].metric("Status", status.capitalize())
    columns[1].metric("Attempts", f"{len(incident.attempts)} / 3")
    columns[2].metric("Model cost", f"${incident.usage.cost_usd:.4f}")
    columns[3].metric("Tokens", f"{incident.usage.tokens:,}")
    st.write("")
    if status == "verified":
        st.success("Patch verified. The reproduction and test suite passed; test hashes are unchanged.")
        st.caption("Review, merging and deployment remain with your team.")
    elif status == "reported":
        st.info("Investigation ended with a diagnostic report. No verified patch is available.")
    elif status == "failed":
        st.error("Nightwatch encountered an execution error. Review the timeline and test output.")
    elif running:
        st.info("Investigation in progress · this view refreshes automatically.")
    else:
        st.warning("This saved investigation is incomplete. It is not running in this console process.")

    acceptance = metadata.get("acceptance")
    if acceptance:
        if acceptance.get("ok"):
            st.success("Independent acceptance passed · exact expected test set confirmed in Docker.")
        elif acceptance.get("status") == "running":
            st.info("Checking independent acceptance. Pipeline verification is not the final scenario result.")
        else:
            st.error("Independent acceptance did not pass: " + clean(acceptance.get("reason", "Incomplete evidence")))
        with st.expander("Independent acceptance details"):
            st.dataframe([{"Check": key, "Passed": value} for key, value in acceptance.get("checks", {}).items()],
                         hide_index=True, width="stretch")
            for name, result in acceptance.get("runs", {}).items():
                from nightwatch.schemas import RunResult
                run_details(name, RunResult.model_validate(result))
            st.download_button("Download acceptance report", json_text(acceptance),
                               f"{incident.id}-acceptance.json", "application/json", key=f"acceptance-{incident.id}")

    overview, evidence, execution = st.tabs(["Investigation", "Evidence", "Tests & cost"])
    with overview:
        main, timeline = st.columns([1.65, 1], gap="large")
        with main:
            if incident.final_patch:
                st.markdown("### Verified patch")
                st.text(clean(incident.final_patch.explanation))
                st.code(incident.final_patch.diff, language="diff")
                st.download_button("Download patch", incident.final_patch.diff, f"{incident.id}.patch", "text/plain",
                                   key=f"patch-{incident.id}")
                if metadata.get("kind") == "repository":
                    st.caption("GitHub target: " + metadata.get("github_repo", "Not configured"))
                    published = console.store.publication(incident.id)
                    if published and published["state"] == "created":
                        st.link_button("View pull request", published["result"]["url"])
                    else:
                        publishing = bool(published and published["state"] == "publishing")
                        if publishing:
                            st.info("This incident is being published. The pull request link will appear here.")
                        elif published and published["state"] == "failed":
                            st.caption("The previous publication failed. You can retry after resolving the error.")
                        confirmed = st.checkbox("Publish this patch and reproduction test to the configured GitHub repository.",
                                                key=f"publish-confirm-{incident.id}", disabled=running or publishing)
                        if st.button("Create Draft PR", key=f"publish-{incident.id}",
                                     disabled=running or publishing or not confirmed):
                            try:
                                with st.spinner("Rechecking tests and creating a Draft PR…"):
                                    created = console.publish(incident.id)
                                st.link_button("View Draft PR", created["url"])
                            except Exception as error:
                                st.error(clean(str(error)))
                else:
                    st.caption("Simulated alerts can be downloaded; GitHub publication requires a real repository alert.")
            if incident.report:
                st.markdown("### Diagnostic report")
                for fact in incident.report.facts:
                    st.text("• " + clean(fact))
                st.markdown("#### Next steps")
                for n, step in enumerate(incident.report.next_steps, 1):
                    st.text(f"{n}. {clean(step)}")
            hypotheses = (incident.report.hypotheses if incident.report else
                          incident.investigation.hypotheses if incident.investigation else [])
            if hypotheses:
                st.markdown("### Hypotheses")
                for hypothesis in sorted(hypotheses, key=lambda h: h.confidence, reverse=True):
                    with st.container(border=True):
                        st.text(clean(hypothesis.summary))
                        st.caption(f"Model confidence: {hypothesis.confidence:.0%} · "
                                   f"Evidence: {', '.join(hypothesis.evidence_ids) or 'None cited'}")
                        if hypothesis.would_refute:
                            st.text("Would refute: " + clean(hypothesis.would_refute))
            elif not incident.report:
                st.caption("Hypotheses will appear after the investigation step.")
            if incident.investigation and incident.investigation.repro_test:
                test = incident.investigation.repro_test
                with st.expander("Reproduction test"):
                    st.caption(test.path)
                    st.code(test.code, language="python")
                    st.download_button("Download reproduction test", test.code, Path(test.path).name,
                                       "text/plain", key=f"test-{incident.id}")
        with timeline:
            st.markdown("### Timeline")
            for event in reversed(incident.events):
                st.caption(f"{event.at:%H:%M:%S} UTC · {event.state.value.upper()}")
                st.text(clean(event.message))
            if incident.error:
                with st.expander("Execution error", expanded=True):
                    st.code(clean(incident.error), language="text")
    with evidence:
        if not incident.evidence:
            st.info("Evidence is being collected.")
        for item in incident.evidence:
            with st.expander(f"[{item.id}] {item.kind.upper()} · {item.source}"):
                if item.truncated:
                    st.caption("Truncated to fit the context budget")
                st.code(clean(item.content), language="diff" if item.kind == "diff" else "text")
        if incident.similar_incidents:
            st.caption("Related incidents: " + ", ".join(incident.similar_incidents))
    with execution:
        st.markdown("### Reproduction")
        checks(incident.repro_verdict)
        run_details("Broken version", incident.repro_broken)
        run_details("Previous good version", incident.repro_good)
        for attempt in incident.attempts:
            st.markdown(f"### Patch attempt {attempt.n}")
            checks(attempt.verdict)
            run_details("Full test suite", attempt.run)
            if attempt.patch and not (incident.final_patch and attempt.verdict and attempt.verdict.ok):
                with st.expander("Proposed diff"):
                    st.code(attempt.patch.diff, language="diff")
        st.markdown("### Model calls")
        if mode == "demo":
            st.caption("Prepared responses have no API usage or token cost.")
        if incident.usage.calls:
            st.dataframe([{"Step": c.step, "Model": c.model, "Input tokens": c.prompt_tokens,
                           "Output tokens": c.completion_tokens, "Cost ($)": c.cost_usd,
                           "Seconds": c.duration_s} for c in incident.usage.calls],
                         hide_index=True, width="stretch")
        else:
            st.caption("No model calls recorded yet.")
    st.download_button("Export incident JSON", clean(incident.model_dump_json(indent=2)),
                       f"{incident.id}.json", "application/json", key=f"export-{incident.id}")


console = service()
job = console.snapshot()
busy = bool(job and job.running)
with st.sidebar:
    st.markdown('<div class="eyebrow">NIGHTWATCH / CONTROL ROOM</div>', unsafe_allow_html=True)
    st.markdown("### Simulate an alert")
    st.caption("Reproduce. Verify. Then review.")
    mode_label = st.radio("Model mode", ["Scripted demo", "Live Nemotron"], disabled=busy)
    mode = "demo" if mode_label == "Scripted demo" else "live"
    scenario = st.selectbox("Scenario", list(SCENARIOS), format_func=SCENARIOS.get, disabled=busy)
    st.caption(SCENARIO_DESCRIPTIONS[scenario])
    if st.button("Refresh services", disabled=busy):
        sandbox_status.clear()
    ready, reason = sandbox_status(os.getenv("NIGHTWATCH_RUNNER_IMAGE", DEFAULT_IMAGE))
    if ready:
        st.success(reason)
    else:
        st.warning(reason)
    consent = False
    if mode == "live":
        st.caption(f"Model: {console.settings.model}")
        st.caption(f"Budget: ${console.settings.incident_budget_usd:.2f} / incident · "
                   f"${console.settings.daily_budget_usd:.2f} / day")
        if not console.settings.api_key:
            st.warning("Set NEBIUS_API_KEY in .env and restart the console.")
        consent = st.checkbox("Use the live API; model calls incur charges.", disabled=busy)
    else:
        st.caption("No API key required. Test execution uses real Docker containers.")
    allowed = ready and not busy and (mode == "demo" or (bool(console.settings.api_key) and consent))
    if st.button("Run investigation" if mode == "demo" else "Run live investigation", type="primary",
                 width="stretch", disabled=not allowed):
        try:
            console.submit(mode, scenario)
            st.session_state["was_running"] = True
        except ValueError as error:
            st.error(str(error))
        st.rerun()
    if busy:
        st.caption("One investigation is running. Follow its timeline in the main panel.")
    st.divider()
    st.caption("Python demo · Docker sandbox · independent verifier")

st.markdown('<div class="eyebrow">AI ON-CALL ENGINEER</div>', unsafe_allow_html=True)
st.title("Nightwatch")
st.caption("Every proposed fix starts with a reproduced failure.")


@st.fragment(run_every="1s")
def dashboard():
    current = console.snapshot()
    previous_running = st.session_state.get("was_running", False)
    now_running = bool(current and current.running)
    st.session_state["was_running"] = now_running
    if previous_running and not now_running:
        st.rerun()
    if current and current.error:
        st.error(clean(current.error))
    records = console.records()
    if current and current.incident:
        records = [(current.mode, current.incident)] + [(m, inc) for m, inc in records
                                                        if inc.id != current.incident.id]
        if st.session_state.get("display_job") != current.id:
            st.session_state["selected_incident"] = current.incident.id
            st.session_state["display_job"] = current.id
    if current and current.running and current.incident is None:
        st.info("Preparing the demo repository and alert…")
        if not records:
            return
    if not records:
        st.markdown("## From alert to evidence.")
        st.write("Run an incident from the sidebar. Nightwatch investigates, reproduces the failure "
                 "and checks a proposed patch against the test suite.")
        left, right = st.columns(2)
        with left, st.container(border=True):
            st.markdown("### A fix you can inspect")
            st.write("A verified diff, a reproduction test and the checks behind the result.")
        with right, st.container(border=True):
            st.markdown("### An honest diagnostic report")
            st.write("When evidence is insufficient, get ranked hypotheses and concrete next steps.")
        return
    lookup = {inc.id: (m, inc) for m, inc in records}
    if st.session_state.get("selected_incident") not in lookup:
        st.session_state["selected_incident"] = records[0][1].id
    selected = st.selectbox("Incident history", list(lookup), key="selected_incident",
                            format_func=lambda key: f"{lookup[key][1].alert.title} · "
                            f"{lookup[key][1].state.value} · {lookup[key][0]} · {key}")
    selected_mode, incident = lookup[selected]
    running = bool(current and current.running and current.incident and current.incident.id == selected)
    incident_view(incident, selected_mode, running)


dashboard()
