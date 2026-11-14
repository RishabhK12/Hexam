"""Cross-record validation report (returned by API endpoints as-is)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, computed_field

from .base import HexamModel


class Severity(StrEnum):
    ERROR = "error"  # data cannot be used by the solver/evaluator
    WARNING = "warning"  # suspicious but usable


class ValidationIssue(HexamModel):
    severity: Severity
    code: str = Field(description="Stable machine code, e.g. 'missing_reference'.")
    message: str
    entity_type: str | None = None
    entity_id: str | None = None


class ValidationReport(HexamModel):
    issues: list[ValidationIssue] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def valid(self) -> bool:
        return self.error_count == 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_count(self) -> int:
        return sum(i.severity is Severity.ERROR for i in self.issues)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warning_count(self) -> int:
        return sum(i.severity is Severity.WARNING for i in self.issues)

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                severity=severity, code=code, message=message, entity_type=entity_type, entity_id=entity_id
            )
        )

    def error(self, code: str, message: str, entity_type: str | None = None, entity_id: str | None = None) -> None:
        self.add(Severity.ERROR, code, message, entity_type, entity_id)

    def warning(self, code: str, message: str, entity_type: str | None = None, entity_id: str | None = None) -> None:
        self.add(Severity.WARNING, code, message, entity_type, entity_id)

    def codes(self, severity: Severity | str | None = None) -> set[str]:
        sev = None if severity is None else Severity(severity)
        return {i.code for i in self.issues if sev is None or i.severity is sev}

    def extend(self, other: ValidationReport) -> None:
        self.issues.extend(other.issues)

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise DatasetValidationError(self)


class DatasetValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        errors = [i for i in report.issues if i.severity is Severity.ERROR]
        preview = "\n".join(f"  [{i.code}] {i.message}" for i in errors[:20])
        more = f"\n  ... and {len(errors) - 20} more" if len(errors) > 20 else ""
        super().__init__(f"{len(errors)} validation error(s):\n{preview}{more}")
