"""REST routes. Thin: validate input, call a service, shape the response."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from hexam import __version__
from hexam.schemas import (
    CompareRequest,
    DatasetInfo,
    EvaluateResponse,
    EvaluateScheduleRequest,
    ExamGroupView,
    ExamSchedule,
    ExamTimeslot,
    HealthResponse,
    OptimizationRun,
    OptimizeRequest,
    RunState,
    RunSummary,
    ScheduleComparison,
    StudentDetailView,
    StudentImpactResponse,
    StudentPage,
    StudentRankItem,
)
from hexam.services import comparison_service, report_service, student_service
from hexam.services.dataset_service import DatasetHandle, DatasetRegistry
from hexam.services.optimization_service import RunStore

router = APIRouter()
DatasetQ = Annotated[str, Query(description="Dataset ID (see /api/datasets)")]


def _registry(req: Request) -> DatasetRegistry:
    return req.app.state.registry


def _store(req: Request) -> RunStore:
    return req.app.state.store


def _handle(req: Request, dataset_id: str) -> DatasetHandle:
    return _registry(req).get(dataset_id)


def _run(req: Request, run_id: str | None, dataset_id: str) -> OptimizationRun | None:
    store = _store(req)
    if run_id is None:
        return store.latest_completed(dataset_id)
    try:
        run = store.get(run_id)
    except KeyError:
        raise HTTPException(404, f"run {run_id!r} not found") from None
    if run.dataset_id != dataset_id:
        raise HTTPException(409, f"run {run_id} belongs to dataset {run.dataset_id}, not {dataset_id}")
    return run


# ------------------------------------------------------------------ meta


@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health(req: Request):
    try:
        import ortools

        ov = ortools.__version__
    except Exception:  # pragma: no cover
        ov = None
    return HealthResponse(
        status="ok", version=__version__, ortools_version=ov,
        datasets=_registry(req).available(), active_runs=_store(req).active_count(),
    )


@router.get("/datasets", response_model=list[DatasetInfo], tags=["data"])
def datasets(req: Request):
    reg = _registry(req)
    return [reg.get(d).info for d in reg.available()]


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo, tags=["data"])
def dataset(req: Request, dataset_id: str):
    return _handle(req, dataset_id).info


# ------------------------------------------------------------------ data


@router.get("/exams", response_model=list[ExamGroupView], tags=["data"])
def exams(
    req: Request,
    dataset_id: DatasetQ = "fall2026",
    course: str | None = None,
    department: str | None = None,
    kind: str | None = None,
):
    h = _handle(req, dataset_id)
    idx = h.index
    out = []
    cn = (course or "").upper().replace(" ", "-")
    for g in sorted(idx.exam_groups):
        grp = idx.exam_groups[g]
        courses = h.group_courses.get(g, [])
        depts = sorted({c.split("-")[0] for c in courses})
        if cn and not any(c.startswith(cn) for c in courses):
            continue
        if department and department.upper() not in depts:
            continue
        if kind and grp.kind.value != kind:
            continue
        out.append(ExamGroupView(
            exam_group_id=g, name=grp.name, kind=grp.kind, meeting_pattern=grp.meeting_pattern,
            description=grp.description, course_ids=courses, departments=depts,
            section_count=len(idx.sections_by_group.get(g, [])), student_count=idx.group_size(g),
            fixed_timeslot_id=grp.fixed_timeslot_id, allowed_timeslot_ids=grp.allowed_timeslot_ids,
            forbidden_timeslot_ids=grp.forbidden_timeslot_ids,
            baseline_timeslot_id=h.baseline_mapping.get(g), source=grp.provenance.source,
        ))
    return out


@router.get("/slots", response_model=list[ExamTimeslot], tags=["data"])
def slots(req: Request, dataset_id: DatasetQ = "fall2026"):
    return sorted(_handle(req, dataset_id).dataset.timeslots, key=lambda t: t.start)


@router.get("/students", response_model=StudentPage, tags=["students"])
def students(
    req: Request,
    dataset_id: DatasetQ = "fall2026",
    q: Annotated[str | None, Query(description="Synthetic student ID (substring)")] = None,
    major: str | None = None,
    year: str | None = None,
    course: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return student_service.search(_handle(req, dataset_id), q=q, major=major, year=year, course=course,
                                  offset=offset, limit=limit)


@router.get("/students/{student_id}", response_model=StudentDetailView, tags=["students"])
def student(req: Request, student_id: str, dataset_id: DatasetQ = "fall2026"):
    return student_service.detail(_handle(req, dataset_id), student_id.upper())


@router.get("/students/{student_id}/impact", response_model=StudentImpactResponse, tags=["students"])
def student_impact(req: Request, student_id: str, dataset_id: DatasetQ = "fall2026", run_id: str | None = None):
    h = _handle(req, dataset_id)
    sid = student_id.upper()
    if sid not in h.index.students:
        raise student_service.StudentNotFound(sid)
    run = _run(req, run_id, dataset_id)
    if run is not None and run.schedule is not None:
        art = _store(req).artifacts(run.run_id)
        return student_service.impact(h, sid, run_id=run.run_id, before=art.before, after=art.after, schedule=run.schedule)
    before = h.evaluator().evaluate(h.baseline)
    return student_service.impact(h, sid, run_id=None, before=before, after=None, schedule=None)


# ------------------------------------------------------------- schedules


@router.get("/schedules/baseline", response_model=ExamSchedule, tags=["schedules"])
def baseline(req: Request, dataset_id: DatasetQ = "fall2026"):
    h = _handle(req, dataset_id)
    if h.baseline is None:
        raise HTTPException(404, f"dataset {dataset_id} has no baseline schedule")
    return h.baseline


@router.post("/evaluate", response_model=EvaluateResponse, tags=["schedules"])
def evaluate(req: Request, body: EvaluateScheduleRequest):
    h = _handle(req, body.dataset_id)
    if body.schedule_id is not None:
        if h.baseline and body.schedule_id == h.baseline.schedule_id:
            target = h.baseline
        else:
            run = next((r for r in (_store(req).get(s.run_id) for s in _store(req).list(body.dataset_id))
                        if r.schedule and r.schedule.schedule_id == body.schedule_id), None)
            if run is None:
                raise HTTPException(404, f"schedule {body.schedule_id!r} not found")
            target = run.schedule
    else:
        target = {a.exam_group_id: a.timeslot_id for a in body.assignments or []}
    out = h.evaluator(body.settings).evaluate(target, weights=body.weights)
    students = sorted(out.students.values(), key=lambda d: d.student_id) if body.include_students else None
    return EvaluateResponse(evaluation=out.evaluation, students=students)


# ---------------------------------------------------------- optimization


@router.post("/optimize", response_model=OptimizationRun, tags=["optimization"],
             responses={202: {"description": "Run queued; poll GET /api/runs/{run_id}"}})
def optimize(req: Request, body: OptimizeRequest, response: Response):
    run = _store(req).submit(body)
    if run.state in (RunState.QUEUED, RunState.RUNNING):
        response.status_code = 202
    return run


@router.get("/runs", response_model=list[RunSummary], tags=["optimization"])
def runs(req: Request, dataset_id: str | None = None):
    return _store(req).list(dataset_id)


@router.get("/runs/{run_id}", response_model=OptimizationRun, tags=["optimization"])
def run(req: Request, run_id: str):
    try:
        return _store(req).get(run_id)
    except KeyError:
        raise HTTPException(404, f"run {run_id!r} not found") from None


@router.get("/runs/{run_id}/students", response_model=list[StudentRankItem], tags=["optimization"])
def run_students(
    req: Request,
    run_id: str,
    order: Literal["improved", "worsened", "remaining", "baseline"] = "improved",
    limit: Annotated[int, Query(ge=1, le=500)] = 25,
):
    r = run(req, run_id)
    if r.schedule is None:
        raise HTTPException(409, f"run {run_id} has no optimized schedule (state {r.state.value})")
    art = _store(req).artifacts(run_id)
    return student_service.ranking(_handle(req, r.dataset_id), art.before, art.after, order, limit)


@router.get(
    "/runs/{run_id}/report.pdf", tags=["optimization"], response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "Baseline vs optimized comparison report"}},
)
def run_report(req: Request, run_id: str):
    r = run(req, run_id)
    if r.schedule is None or r.result is None or r.result.after is None:
        raise HTTPException(409, f"run {run_id} has no optimized schedule to report on (state {r.state.value})")
    h = _handle(req, r.dataset_id)
    if r.dataset_version != h.version:
        raise HTTPException(409, f"run {run_id} was computed on a different dataset version")
    pdf = report_service.build_run_report(h, r, _store(req).artifacts(run_id))
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="hexam-report-{run_id}.pdf"'},
    )


@router.get("/compare", response_model=ScheduleComparison, tags=["optimization"])
def compare_run(req: Request, dataset_id: DatasetQ = "fall2026", run_id: str | None = None):
    r = _run(req, run_id, dataset_id)
    if r is None:
        raise HTTPException(404, f"no completed optimization run for dataset {dataset_id}")
    if r.comparison is None:
        raise HTTPException(409, f"run {r.run_id} has no comparison (status {r.result.status if r.result else r.state})")
    if r.dataset_version != _handle(req, dataset_id).version:
        raise HTTPException(409, f"run {r.run_id} was computed on a different dataset version")
    return r.comparison


@router.post("/compare", response_model=ScheduleComparison, tags=["optimization"])
def compare_adhoc(req: Request, body: CompareRequest):
    h = _handle(req, body.dataset_id)
    if body.before_schedule_id not in (None, h.baseline.schedule_id if h.baseline else None):
        raise HTTPException(404, f"schedule {body.before_schedule_id!r} not found (only the baseline is stored)")
    if h.baseline is None:
        raise HTTPException(404, f"dataset {body.dataset_id} has no baseline schedule")
    ev = h.evaluator(body.settings)
    before = ev.evaluate(h.baseline, weights=body.weights)
    after = ev.evaluate(body.after_assignments, weights=body.weights, schedule_id="adhoc")
    return comparison_service.compare(
        dataset_id=h.dataset_id, dataset_version=h.version, before=before, after=after,
        weights=body.weights, settings=body.settings,
        before_mapping=h.baseline_mapping, after_mapping=dict(body.after_assignments),
    )
