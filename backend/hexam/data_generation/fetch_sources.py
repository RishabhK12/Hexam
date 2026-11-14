"""Download and cache every public source; write ``data/provenance/sources.json``.

    python -m hexam.data_generation.fetch_sources --term 202608 [--force] [--offline]

* Existing cached files are reused unless ``--force`` (re-download) is given.
* ``--offline`` only validates the cache and rebuilds the manifest.
* If the requested term is missing from the GT Scheduler index, the most recent
  available Fall term is used instead and the substitution is recorded.
"""

from __future__ import annotations

import argparse
import json
import sys

from .adapters.gt_scheduler import index_terms
from .adapters.irp import parse_factbook_ug_by_college
from .paths import DEFAULT_TERM, DataPaths
from .sources import (
    SourceManifest,
    SourceStatus,
    fetch_source,
    load_manifest,
    save_manifest,
    term_sources,
)
from ..schemas.base import now_campus

FACTBOOK_EXTRACT = "irp_factbook_2025_ug_by_college.json"


def resolve_term(requested: str, available: list[str]) -> tuple[str, str | None]:
    """Return (term to use, substitution note or None)."""
    if requested in available:
        return requested, None
    falls = sorted(t for t in available if t.endswith("08") and t < requested) or sorted(t for t in available if t.endswith("08"))
    if not falls:
        raise SystemExit(f"term {requested} unavailable and no Fall term in the index: {available}")
    chosen = falls[-1]
    return chosen, f"term {requested} not in GT Scheduler index; substituted most recent available Fall term {chosen}"


def run(term: str, paths: DataPaths, *, force: bool = False, offline: bool = False, log=print) -> SourceManifest:
    previous = {}
    if paths.sources_manifest.exists():
        try:
            previous = {s.key: s for s in load_manifest(paths.sources_manifest).sources}
        except ValueError:
            previous = {}

    # 1. Index first, to confirm the term exists before trusting a term URL.
    specs = {s.key: s for s in term_sources(term)}
    index_rec = fetch_source(specs["gt_scheduler_index"], paths.raw, offline=offline, force=True if not offline else False, previous=previous.get("gt_scheduler_index"))
    substitution = None
    if index_rec.status in (SourceStatus.FETCHED, SourceStatus.CACHED):
        available = index_terms(json.loads((paths.raw / index_rec.local_path).read_bytes()))  # type: ignore[operator]
        term, substitution = resolve_term(term, available)
        if substitution:
            log(f"WARNING: {substitution}")
            specs = {s.key: s for s in term_sources(term)}
    else:
        log(f"WARNING: GT Scheduler index unavailable ({index_rec.error}); trying term {term} directly")

    records = [index_rec]
    for key, spec in specs.items():
        if key == "gt_scheduler_index":
            continue
        rec = fetch_source(spec, paths.raw, offline=offline, force=force, previous=previous.get(key))
        if key == "final_exam_matrix" and rec.status is not SourceStatus.FAILED:
            page = paths.raw / "registrar_matrix_page.html"
            if page.exists():
                fname = spec.url.rsplit("/", 1)[-1]
                linked = fname in page.read_text(encoding="utf-8", errors="replace")
                rec.notes = f"registrar landing page {'links' if linked else 'does NOT link'} {fname}"
        records.append(rec)
    if "final_exam_matrix" not in specs:
        log(f"WARNING: no official final exam matrix URL is registered for term {term}")

    # Derived extract of the (git-ignored) Fact Book PDF, so offline runs need only the small JSON.
    fb = next(r for r in records if r.key == "irp_factbook_2025")
    extract = paths.raw / FACTBOOK_EXTRACT
    if fb.status in (SourceStatus.FETCHED, SourceStatus.CACHED):
        table = parse_factbook_ug_by_college(paths.raw / fb.local_path)  # type: ignore[operator]
        table.update(source_url=fb.url, source_sha256=fb.sha256)
        extract.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    elif extract.exists():
        fb.notes = f"PDF unavailable; using previously extracted table {FACTBOOK_EXTRACT}"

    manifest = SourceManifest(term=term, term_substitution=substitution, generated_at=now_campus().replace(microsecond=0), sources=records)
    old = load_manifest(paths.sources_manifest) if paths.sources_manifest.exists() else None
    unchanged = old is not None and [
        r.model_dump(exclude={"status"}) for r in old.sources
    ] == [r.model_dump(exclude={"status"}) for r in records] and old.term == term
    if unchanged:
        manifest = old  # nothing new: keep the manifest byte-identical (fixed snapshot)
    else:
        save_manifest(manifest, paths.sources_manifest)
    for r in records:
        detail = r.error or r.notes or ""
        log(f"  {r.status.value:9} {r.key:30} {r.http_status or '':>4} {r.bytes or '':>9}  {detail}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", default=DEFAULT_TERM)
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    ap.add_argument("--offline", action="store_true", help="do not access the network; validate cache only")
    ap.add_argument("--data-dir", default=None, help="override the data/ directory")
    args = ap.parse_args(argv)
    paths = DataPaths(**({"root": __import__("pathlib").Path(args.data_dir)} if args.data_dir else {}))
    manifest = run(args.term, paths, force=args.force, offline=args.offline)
    required = ["gt_scheduler_term", "final_exam_matrix"]
    missing = [k for k in required if (r := manifest.get(k)) is None or r.status is SourceStatus.FAILED]
    if missing:
        print(f"ERROR: required sources unavailable: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
