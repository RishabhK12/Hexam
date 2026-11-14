"""Baseline vs optimized comparison — both scored by the same evaluator, data and weights."""

from __future__ import annotations

from hexam.evaluation import EvaluationOutcome
from hexam.schemas import (
    EvaluationSettings,
    ImpactMetricKey,
    MetricDelta,
    PenaltyWeights,
    ScheduleComparison,
    StudentImpactMetrics,
    compare_metrics,
)


def _pct(before: float, after: float) -> float | None:
    return None if before == 0 else round(100.0 * (after - before) / before, 2)


def _affected_deltas(before: StudentImpactMetrics, after: StudentImpactMetrics) -> list[MetricDelta]:
    out = []
    for k in ImpactMetricKey:
        b, a = before.get(k).students_affected, after.get(k).students_affected
        out.append(MetricDelta(key=k, before=b, after=a, change=a - b, percent_change=_pct(b, a)))
    return out


def compare(
    *,
    dataset_id: str,
    dataset_version: str,
    before: EvaluationOutcome,
    after: EvaluationOutcome,
    weights: PenaltyWeights,
    settings: EvaluationSettings,
    before_mapping: dict[str, str],
    after_mapping: dict[str, str],
) -> ScheduleComparison:
    eb, ea = before.evaluation, after.evaluation
    if eb.weights_used != ea.weights_used or eb.settings != ea.settings:
        raise ValueError("schedules were scored under different weights/settings; refusing to compare")
    if set(before.students) != set(after.students):
        raise ValueError("schedules were evaluated on different student populations")
    pb, pa = float(eb.weighted_penalty or 0), float(ea.weighted_penalty or 0)
    improved = worsened = unchanged = 0
    for sid, d in before.students.items():
        a = after.students[sid].weighted_penalty
        if a < d.weighted_penalty:
            improved += 1
        elif a > d.weighted_penalty:
            worsened += 1
        else:
            unchanged += 1
    deltas = compare_metrics(eb.metrics, ea.metrics)
    return ScheduleComparison(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        before_schedule_id=eb.schedule_id,
        after_schedule_id=ea.schedule_id,
        weights=weights,
        settings=settings,
        count_deltas=deltas,
        students_affected_deltas=_affected_deltas(eb.metrics, ea.metrics),
        penalty_before=pb,
        penalty_after=pa,
        penalty_change=pa - pb,
        penalty_percent_change=_pct(pb, pa),
        students_with_any_issue_before=eb.metrics.students_with_any_issue,
        students_with_any_issue_after=ea.metrics.students_with_any_issue,
        students_improved=improved,
        students_worsened=worsened,
        students_unchanged=unchanged,
        exam_groups_moved=sum(1 for g, t in after_mapping.items() if before_mapping.get(g) != t),
        metrics_worsened=[d.key for d in deltas if d.change > 0],
    )
