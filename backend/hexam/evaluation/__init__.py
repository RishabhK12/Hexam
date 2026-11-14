"""Independent schedule evaluation (pure Python, no solver dependency)."""

from .evaluator import EvaluationOutcome, Evaluator, StudentExam, StudentFindings, evaluate_schedule
from .rules import SlotRelations, campus_date, is_back_to_back, is_late_to_early, overlaps

__all__ = [
    "EvaluationOutcome",
    "Evaluator",
    "SlotRelations",
    "StudentExam",
    "StudentFindings",
    "campus_date",
    "evaluate_schedule",
    "is_back_to_back",
    "is_late_to_early",
    "overlaps",
]
