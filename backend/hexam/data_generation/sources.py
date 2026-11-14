"""Registry of public sources, a caching downloader, and the source manifest.

Every source is downloaded once into ``data/raw/`` and recorded in
``data/provenance/sources.json`` with its URL, retrieval time, HTTP status,
SHA-256 and size. Later steps read only the cache, so generation is offline
and reproducible from a fixed snapshot. A failed download never produces
data: the step falls back to an existing cached copy (status ``cached``) or
records the failure (status ``failed``) so downstream code can use a
documented assumption instead.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from ..schemas.base import CampusDateTime, HexamModel, now_campus

USER_AGENT = "hexam-data-pipeline/0.1 (Georgia Tech hackathon project; public data only)"
TIMEOUT_S = 45


class SourceStatus(StrEnum):
    FETCHED = "fetched"  # downloaded now and validated
    CACHED = "cached"  # download skipped or failed; validated cached copy used
    PROBE_OK = "probe_ok"  # reachability check only; content not used as data
    FAILED = "failed"  # unavailable and no usable cache


class SourceCategory(StrEnum):
    COURSE_DATA = "course_data"
    EXAM_MATRIX = "exam_matrix"
    EXAM_POLICY = "exam_policy"
    ENROLLMENT_STATS = "enrollment_stats"
    CURRICULUM = "curriculum"
    VERIFICATION = "verification"


@dataclass(frozen=True)
class SourceSpec:
    key: str
    url: str
    category: SourceCategory
    description: str
    filename: str | None = None  # None = probe only (reachability), nothing cached
    validator: Callable[[bytes], str | None] | None = None  # returns error message or None
    git_ignored: bool = False  # large file: only its derived extract is committed
    used_for: list[str] = field(default_factory=list)


class SourceRecord(HexamModel):
    key: str
    url: str
    category: SourceCategory
    description: str
    status: SourceStatus
    local_path: str | None = None
    http_status: int | None = None
    retrieved_at: CampusDateTime | None = None
    sha256: str | None = None
    bytes: int | None = None
    error: str | None = None
    notes: str | None = None
    git_ignored: bool = Field(default=False, description="large file kept out of git; only its derived extract is committed")
    used_for: list[str] = Field(default_factory=list)


class SourceManifest(HexamModel):
    term: str
    term_substitution: str | None = None
    generated_at: CampusDateTime
    sources: list[SourceRecord]

    def get(self, key: str) -> SourceRecord | None:
        return next((s for s in self.sources if s.key == key), None)

    def require(self, key: str) -> SourceRecord:
        rec = self.get(key)
        if rec is None or rec.status not in (SourceStatus.FETCHED, SourceStatus.CACHED):
            status = rec.status if rec else "missing"
            raise SourceUnavailableError(f"required source {key!r} is unavailable ({status}); run fetch_sources")
        return rec


class SourceUnavailableError(RuntimeError):
    pass


# ----------------------------------------------------------------- validators


def _json_with_keys(*keys: str) -> Callable[[bytes], str | None]:
    def check(data: bytes) -> str | None:
        try:
            obj = json.loads(data)
        except ValueError as e:
            return f"not valid JSON: {e}"
        missing = [k for k in keys if k not in obj]
        return f"JSON missing keys {missing}" if missing else None

    return check


def _pdf(data: bytes) -> str | None:
    return None if data.startswith(b"%PDF-") else "not a PDF file"


def _html_containing(*needles: str) -> Callable[[bytes], str | None]:
    def check(data: bytes) -> str | None:
        text = data.decode("utf-8", errors="replace")
        missing = [n for n in needles if n not in text]
        return f"page does not contain {missing}" if missing else None

    return check


# ------------------------------------------------------------------- registry

CRAWLER_BASE = "https://gt-scheduler.github.io/crawler-v2"

# Official matrix PDFs by term (from https://registrar.gatech.edu/info/current-term-final-exam-matrices)
FINAL_EXAM_MATRIX_URLS = {
    "202608": "https://registrar.gatech.edu/public/files/Final%20Exam%20Matrix%20202608%20Fall_3.pdf",
}


def term_sources(term: str) -> list[SourceSpec]:
    specs = [
        SourceSpec(
            "gt_scheduler_index",
            f"{CRAWLER_BASE}/index.json",
            SourceCategory.COURSE_DATA,
            "GT Scheduler crawler-v2 term index",
            "gt_scheduler_index.json",
            _json_with_keys("terms"),
            used_for=["term availability check"],
        ),
        SourceSpec(
            "gt_scheduler_term",
            f"{CRAWLER_BASE}/{term}.json",
            SourceCategory.COURSE_DATA,
            f"GT Scheduler crawler-v2 OSCAR snapshot for term {term} (source: github.com/gt-scheduler/crawler-v2)",
            f"gt_scheduler_{term}.json",
            _json_with_keys("courses", "caches", "updatedAt", "version"),
            used_for=["courses", "sections", "CRNs", "instructors", "meeting times", "credit hours", "prerequisites", "section restrictions"],
        ),
        SourceSpec(
            "registrar_exam_guidelines",
            "https://registrar.gatech.edu/faculty-and-staff/exams",
            SourceCategory.EXAM_POLICY,
            "Registrar final exam guidelines (which courses get a matrix final; conflict rules)",
            "registrar_exam_guidelines.html",
            _html_containing("2-credits or more", "Final Exam Matrix"),
            used_for=["exam-bearing section filter (lecture, >= 2 credits)"],
        ),
        SourceSpec(
            "registrar_matrix_page",
            "https://registrar.gatech.edu/info/current-term-final-exam-matrices",
            SourceCategory.VERIFICATION,
            "Registrar current-term final exam matrix landing page",
            "registrar_matrix_page.html",
            _html_containing("Final%20Exam%20Matrix"),
            used_for=["verify the matrix PDF is the currently published one"],
        ),
        SourceSpec(
            "irp_coe_enrollment",
            "https://irp.gatech.edu/dashboards/COE/Enrollment",
            SourceCategory.ENROLLMENT_STATS,
            "GT IRP College of Engineering enrollment by major and year (engineering majors only)",
            "irp_coe_enrollment.html",
            _html_containing("Enrollment by Major", "2026-2027"),
            used_for=["engineering major weights", "academic-year distribution"],
        ),
        SourceSpec(
            "irp_factbook_2025",
            "https://irp.gatech.edu/sites/default/files/FactBook/2025_FactBook_Final.pdf",
            SourceCategory.ENROLLMENT_STATS,
            "GT IRP 2025 Fact Book (undergraduate enrollment by college, Fall 2025)",
            "irp_factbook_2025.pdf",
            _pdf,
            git_ignored=True,
            used_for=["College of Computing / College of Sciences undergraduate headcounts"],
        ),
        SourceSpec(
            "oscar",
            "https://oscar.gatech.edu/",
            SourceCategory.VERIFICATION,
            "OSCAR landing page (reachability only). Section capacity/enrollment lives behind Banner class search, "
            "which this pipeline does not scrape; capacities therefore remain unknown.",
            used_for=["reachability only"],
        ),
        SourceSpec(
            "registrar_schedule_of_classes",
            "https://registrar.gatech.edu/academic-scheduling/schedule-of-classes",
            SourceCategory.VERIFICATION,
            "Registrar schedule of classes landing page",
            used_for=["reachability only"],
        ),
        SourceSpec(
            "catalog_cs_courses",
            "https://catalog.gatech.edu/courses-undergrad/cs/",
            SourceCategory.CURRICULUM,
            "GT Catalog: undergraduate CS course listings",
            "catalog_cs_courses.html",
            _html_containing("CS 1331"),
            used_for=["curriculum verification"],
        ),
        SourceSpec(
            "catalog_cs_bs",
            "https://catalog.gatech.edu/programs/computer-science-bs/",
            SourceCategory.CURRICULUM,
            "GT Catalog: BS Computer Science program requirements",
            "catalog_cs_bs.html",
            _html_containing("CS 1331"),
            used_for=["curriculum verification"],
        ),
    ]
    if term in FINAL_EXAM_MATRIX_URLS:
        specs.append(
            SourceSpec(
                "final_exam_matrix",
                FINAL_EXAM_MATRIX_URLS[term],
                SourceCategory.EXAM_MATRIX,
                f"Official GT registrar final exam matrix for term {term}",
                f"final_exam_matrix_{term}.pdf",
                _pdf,
                used_for=["exam periods", "meeting-pattern → exam slot mapping", "common exams", "reading/conflict periods"],
            )
        )
    return specs


# ------------------------------------------------------------------- download


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def http_get(url: str, timeout: float = TIMEOUT_S) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs)
        return resp.status, resp.read()


def fetch_source(
    spec: SourceSpec,
    raw_dir: Path,
    *,
    offline: bool = False,
    force: bool = False,
    previous: SourceRecord | None = None,
    getter: Callable[[str], tuple[int, bytes]] = http_get,
) -> SourceRecord:
    """Download (or reuse) one source and return its manifest record."""
    base = dict(
        key=spec.key, url=spec.url, category=spec.category, description=spec.description, used_for=spec.used_for, git_ignored=spec.git_ignored
    )
    local = raw_dir / spec.filename if spec.filename else None

    def from_cache(error: str | None) -> SourceRecord:
        if local is None or not local.exists():
            return SourceRecord(**base, status=SourceStatus.FAILED, error=error or "no cached copy")
        data = local.read_bytes()
        if spec.validator and (problem := spec.validator(data)):
            return SourceRecord(**base, status=SourceStatus.FAILED, error=f"cached copy invalid: {problem}; {error or ''}".strip())
        prev_ok = previous is not None and previous.sha256 == sha256_bytes(data)
        return SourceRecord(
            **base,
            status=SourceStatus.CACHED,
            local_path=local.name,
            http_status=previous.http_status if prev_ok else None,
            retrieved_at=previous.retrieved_at if prev_ok else None,
            sha256=sha256_bytes(data),
            bytes=len(data),
            error=error,
            notes=None if prev_ok else "cached file predates manifest; retrieval time unknown",
        )

    if offline or (local is not None and local.exists() and not force):
        return from_cache(None)

    try:
        status, data = getter(spec.url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return from_cache(f"download failed: {e}")
    retrieved = now_campus().replace(microsecond=0)

    if local is None:
        return SourceRecord(**base, status=SourceStatus.PROBE_OK, http_status=status, retrieved_at=retrieved, bytes=len(data))
    if spec.validator and (problem := spec.validator(data)):
        return from_cache(f"downloaded content invalid: {problem}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return SourceRecord(
        **base,
        status=SourceStatus.FETCHED,
        local_path=local.name,
        http_status=status,
        retrieved_at=retrieved,
        sha256=sha256_bytes(data),
        bytes=len(data),
    )


def load_manifest(path: Path) -> SourceManifest:
    if not path.exists():
        raise SourceUnavailableError(f"{path} not found; run `python -m hexam.data_generation.fetch_sources` first")
    return SourceManifest.model_validate_json(path.read_bytes())


def save_manifest(manifest: SourceManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def verify_cache(manifest: SourceManifest, raw_dir: Path) -> list[str]:
    """Return problems where a cached file no longer matches the manifest hash."""
    problems = []
    for rec in manifest.sources:
        if rec.local_path and rec.status in (SourceStatus.FETCHED, SourceStatus.CACHED):
            p = raw_dir / rec.local_path
            if not p.exists():
                if not rec.git_ignored:  # git-ignored downloads are optional once their extract exists
                    problems.append(f"{rec.key}: cached file {p.name} is missing")
            elif sha256_file(p) != rec.sha256:
                problems.append(f"{rec.key}: {p.name} changed since it was recorded (sha256 mismatch)")
    return problems


def retrieved_at(manifest: SourceManifest, key: str) -> datetime | None:
    rec = manifest.get(key)
    return rec.retrieved_at if rec else None
