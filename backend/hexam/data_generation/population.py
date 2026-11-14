"""Major and academic-year distribution for the synthetic cohort.

* Engineering majors: Fall headcounts by year-in-program from the IRP College of
  Engineering dashboard for the term's academic year (real, ``gt_public``).
* CS / Applied Math / Physics: the dashboard does not cover them, so a college
  total from the IRP Fact Book (most recent Fall available) is multiplied by an
  assumed within-college share (``assumed``). Their year mix uses the average of
  the engineering majors (``assumed``).

The cohort covers only the STEM majors whose curricula the course subset models;
it is a sample, not a picture of all Georgia Tech undergraduates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..schemas.base import DataSource
from .adapters.irp import MajorEnrollment, parse_coe_enrollment
from .config import GenerationConfig
from .fetch_sources import FACTBOOK_EXTRACT
from .sources import SourceManifest, SourceStatus


def academic_year_for_term(term: str) -> str:
    """Fall term 202608 -> "2026-2027"; spring 202702 -> "2026-2027"."""
    year, month = int(term[:4]), int(term[4:])
    start = year if month >= 8 else year - 1
    return f"{start}-{start + 1}"


@dataclass(frozen=True)
class MajorPopulation:
    major: str
    name: str
    headcount: float
    headcount_source: DataSource
    headcount_detail: str
    year_weights: tuple[float, float, float, float, float]
    year_source: DataSource


def build_population(cfg: GenerationConfig, manifest: SourceManifest, raw_dir: Path) -> list[MajorPopulation]:
    ay = academic_year_for_term(manifest.term)
    irp: dict[str, MajorEnrollment] = {}
    irp_rec = manifest.get("irp_coe_enrollment")
    if irp_rec and irp_rec.status in (SourceStatus.FETCHED, SourceStatus.CACHED):
        records = parse_coe_enrollment(raw_dir / irp_rec.local_path)  # type: ignore[operator]
        years_available = sorted({r.academic_year for r in records})
        use_ay = ay if ay in years_available else years_available[-1]
        irp = {r.major_name: r for r in records if r.academic_year == use_ay}
        irp_label = f"IRP COE enrollment {use_ay}" + ("" if use_ay == ay else f" (requested {ay} unavailable)")
    factbook: dict = {}
    if (raw_dir / FACTBOOK_EXTRACT).exists():
        factbook = json.loads((raw_dir / FACTBOOK_EXTRACT).read_text(encoding="utf-8"))

    eng = [irp[m.headcount.irp_major_name] for m in cfg.majors.values() if m.headcount.source == "irp_coe" and m.headcount.irp_major_name in irp]
    eng_avg = tuple(sum(r.undergrad_by_year[i] for r in eng) for i in range(5)) if eng else (27.0, 25.0, 23.0, 20.0, 5.0)

    out: list[MajorPopulation] = []
    for code in sorted(cfg.majors):
        mc = cfg.majors[code]
        h = mc.headcount
        years: tuple = eng_avg
        year_src = DataSource.ASSUMED
        if h.source == "irp_coe":
            rec = irp.get(h.irp_major_name or "")
            if rec is None:
                raise ValueError(f"{code}: IRP major {h.irp_major_name!r} not found (is the IRP source cached?)")
            count, src, detail = float(rec.undergrad_total), DataSource.GT_PUBLIC, f"{irp_label}: {rec.major_name} undergraduates"
            if mc.year_distribution == "irp":
                years, year_src = rec.undergrad_by_year, DataSource.GT_PUBLIC
        elif h.source == "factbook_college":
            colleges = factbook.get("colleges", {})
            if h.college not in colleges:
                raise ValueError(f"{code}: Fact Book college {h.college!r} not available")
            latest = max(colleges[h.college], key=int)
            total = colleges[h.college][latest]
            count = total * h.share
            src = DataSource.ASSUMED
            detail = f"IRP Fact Book Fall {latest} {h.college} undergraduates ({total}) x assumed share {h.share}"
        else:
            count, src, detail = float(h.value or 0), DataSource.ASSUMED, h.notes or "assumed headcount"
        out.append(MajorPopulation(code, mc.name, count, src, detail, tuple(float(x) for x in years), year_src))  # type: ignore[arg-type]
    return out


def population_summary(pop: list[MajorPopulation]) -> list[dict]:
    total = sum(p.headcount for p in pop)
    return [
        {
            "major": p.major,
            "name": p.name,
            "headcount": round(p.headcount, 1),
            "sampling_weight": round(p.headcount / total, 4),
            "headcount_source": p.headcount_source.value,
            "headcount_detail": p.headcount_detail,
            "year_distribution": [round(y / sum(p.year_weights), 4) for y in p.year_weights],
            "year_distribution_source": p.year_source.value,
        }
        for p in pop
    ]
