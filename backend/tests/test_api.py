"""FastAPI endpoint tests against the real fall2026 dataset."""

import pytest
from fastapi.testclient import TestClient

from hexam.api.main import create_app
from hexam.schemas import (
    DatasetInfo,
    EvaluateResponse,
    ExamGroupView,
    OptimizationRun,
    ScheduleComparison,
    StudentImpactResponse,
    StudentPage,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(load_precomputed=False)) as c:
        yield c


@pytest.fixture(scope="module")
def run(client):
    body = {"time_limit_seconds": 5, "num_workers": 4, "random_seed": 3, "wait": True}
    r = client.post("/api/optimize", json=body)
    assert r.status_code == 200, r.text
    return OptimizationRun.model_validate(r.json())


def test_health(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and "fall2026" in r["datasets"] and r["ortools_version"]


def test_datasets(client):
    items = [DatasetInfo.model_validate(d) for d in client.get("/api/datasets").json()]
    fall = next(d for d in items if d.dataset_id == "fall2026")
    assert fall.student_count == 1000 and fall.exam_group_count == 52 and fall.has_baseline
    assert "SYNTHETIC" in fall.synthetic_disclosure
    assert client.get("/api/datasets/fall2026").status_code == 200
    assert client.get("/api/datasets/nope").status_code == 404


def test_exams_and_filters(client):
    exams = [ExamGroupView.model_validate(e) for e in client.get("/api/exams").json()]
    assert len(exams) == 52 and all(e.baseline_timeslot_id for e in exams)
    common = client.get("/api/exams", params={"kind": "common"}).json()
    assert len(common) == 5 and all(e["fixed_timeslot_id"] for e in common)
    math = client.get("/api/exams", params={"department": "MATH"}).json()
    assert math and all("MATH" in e["departments"] for e in math)


def test_slots(client):
    slots = client.get("/api/slots").json()
    assert len(slots) == 21 and slots[0]["start"] < slots[-1]["start"]


def test_students_search_and_detail(client):
    page = StudentPage.model_validate(client.get("/api/students", params={"limit": 10}).json())
    assert page.total == 1000 and len(page.items) == 10
    one = client.get("/api/students", params={"q": "S000042"}).json()
    assert one["total"] == 1 and one["items"][0]["student_id"] == "S000042"
    cs = client.get("/api/students", params={"major": "CS", "limit": 200}).json()
    assert cs["total"] > 0 and all(s["major"] == "CS" for s in cs["items"])
    d = client.get("/api/students/S000042").json()
    assert d["synthetic"] is True and d["sections"] and d["exam_group_ids"]


@pytest.mark.parametrize("bad", ["S999999", "not-an-id"])
def test_unknown_student(client, bad):
    assert client.get(f"/api/students/{bad}").status_code == 404
    assert client.get(f"/api/students/{bad}/impact").status_code == 404


def test_baseline_and_evaluate(client):
    base = client.get("/api/schedules/baseline").json()
    assert len(base["assignments"]) == 52
    r = EvaluateResponse.model_validate(
        client.post("/api/evaluate", json={"schedule_id": base["schedule_id"], "include_students": True}).json()
    )
    assert r.evaluation.valid and r.evaluation.metrics.students_evaluated == 1000
    assert len(r.students) == 1000
    # ad-hoc assignments: drop one group -> invalid, reported
    adhoc = base["assignments"][1:]
    r2 = client.post("/api/evaluate", json={"assignments": adhoc}).json()
    assert r2["evaluation"]["valid"] is False and r2["evaluation"]["diagnostics"]["unassigned_exam_group_ids"]


def test_evaluate_validation_errors(client):
    assert client.post("/api/evaluate", json={}).status_code == 422
    assert client.post("/api/evaluate", json={"schedule_id": "nope"}).status_code == 404
    assert client.post("/api/evaluate", json={"assignments": [], "weights": {"back_to_back": -1}}).status_code == 422


def test_optimize_run(client, run):
    assert run.state == "completed"
    res = run.result
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert run.integrity.passed, [c for c in run.integrity.checks if not c.passed]
    assert res.after.metrics.simultaneous_conflicts.count == 0
    assert res.objective_value >= res.after.weighted_penalty
    assert run.comparison.penalty_after < run.comparison.penalty_before
    # stored and retrievable
    assert client.get(f"/api/runs/{run.run_id}").json()["run_id"] == run.run_id
    assert any(r["run_id"] == run.run_id for r in client.get("/api/runs").json())


def test_compare_and_impact(client, run):
    c = ScheduleComparison.model_validate(client.get("/api/compare", params={"run_id": run.run_id}).json())
    assert c.after_schedule_id == run.schedule.schedule_id
    assert c.penalty_before == run.result.before.weighted_penalty
    imp = StudentImpactResponse.model_validate(
        client.get("/api/students/S000001/impact", params={"run_id": run.run_id}).json()
    )
    assert imp.optimized is not None and imp.run_id == run.run_id
    assert imp.penalty_change == imp.optimized.detail.weighted_penalty - imp.baseline.detail.weighted_penalty
    ranked = client.get(f"/api/runs/{run.run_id}/students", params={"order": "improved", "limit": 5}).json()
    assert ranked and all(i["change"] < 0 for i in ranked)
    # ad-hoc POST compare with the same schedule reproduces the run comparison
    body = {"after_assignments": run.schedule.as_mapping()}
    c2 = client.post("/api/compare", json=body).json()
    assert c2["penalty_after"] == c.penalty_after


def test_optimize_rejects_bad_locks(client):
    r = client.post("/api/optimize", json={
        "wait": True, "time_limit_seconds": 1,
        "fixed_assignments": [{"exam_group_id": "EG-NOPE", "timeslot_id": "TS-20261210-1440"}],
    })
    assert r.status_code == 422
    r = client.post("/api/optimize", json={
        "wait": True, "time_limit_seconds": 1,
        "fixed_assignments": [{"exam_group_id": "EG-COMMON-MATH-1551", "timeslot_id": "TS-20261210-1440"}],
    })
    assert r.status_code == 422 and "officially fixed" in r.text
    assert client.post("/api/optimize", json={"time_limit_seconds": 0}).status_code == 422
    assert client.post("/api/optimize", json={"dataset_id": "nope"}).status_code == 404


def test_optimize_infeasible_is_reported_not_faked(client):
    # 52 groups but at most 2 groups per slot × 21 slots = 42 < 52 → infeasible
    r = client.post("/api/optimize", json={
        "wait": True, "time_limit_seconds": 5,
        "hard_constraints": {"max_exam_groups_per_slot": 2},
    })
    run = OptimizationRun.model_validate(r.json())
    assert run.result.status in ("INFEASIBLE", "UNKNOWN")  # the solver's real status, never faked
    assert any("provably infeasible" in h for h in run.result.infeasibility_hints)
    assert run.schedule is None and run.result.after is None and run.comparison is None
    assert run.result.infeasibility_hints
    assert client.get("/api/compare", params={"run_id": run.run_id}).status_code == 409


def test_async_job_pattern(client):
    r = client.post("/api/optimize", json={"time_limit_seconds": 2, "num_workers": 2})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    import time

    for _ in range(100):
        state = client.get(f"/api/runs/{run_id}").json()["state"]
        if state in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert state == "completed"


def test_run_dataset_mismatch_and_missing(client, run):
    assert client.get("/api/runs/run-nope").status_code == 404
    assert client.get("/api/compare", params={"run_id": run.run_id, "dataset_id": "sample"}).status_code == 409


def test_run_report_pdf(client, run):
    from io import BytesIO

    from pypdf import PdfReader

    r = client.get(f"/api/runs/{run.run_id}/report.pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert f'hexam-report-{run.run_id}.pdf' in r.headers["content-disposition"]
    reader = PdfReader(BytesIO(r.content))
    assert len(reader.pages) >= 4
    text = "\n".join(p.extract_text() for p in reader.pages)
    for needle in ("Final exam schedule comparison", "Late-evening exams", "Early-morning exams",
                   "Exam times: official vs optimized", "Integrity checks", run.run_id):
        assert needle in text, needle
    moved = run.comparison.exam_groups_moved
    assert f"{moved} of 52 exam groups moved." in text
    assert f"{run.result.after.weighted_penalty:,.0f}" in text  # same score the API reports


def test_run_report_missing_run(client):
    assert client.get("/api/runs/run-nope/report.pdf").status_code == 404
