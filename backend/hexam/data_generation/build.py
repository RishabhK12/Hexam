"""Run the whole pipeline from the cached snapshot (offline by default).

    python -m hexam.data_generation.build [--fetch] [--num-students N] [--seed S] [--granularity G]

``--fetch`` refreshes the public sources first (network); without it the
committed ``data/raw`` snapshot + ``data/provenance/sources.json`` are used, so
the output is reproducible.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import fetch_sources, generate, normalize, stats, validate
from .config import ExamGroupGranularity, GenerationConfig, load_config
from .paths import DataPaths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="refresh public sources from the network first")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--num-students", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--granularity", choices=[g.value for g in ExamGroupGranularity], default=None)
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    upd = {
        k: v
        for k, v in {"num_students": args.num_students, "seed": args.seed, "exam_group_granularity": args.granularity}.items()
        if v is not None
    }
    cfg = GenerationConfig.model_validate({**cfg.model_dump(), **upd})
    paths = DataPaths(**({"root": args.data_dir} if args.data_dir else {}), dataset_name=cfg.dataset_name)

    if args.fetch:
        print("== fetch_sources")
        fetch_sources.run(cfg.term, paths)
    print("== normalize")
    normalize.run(paths, cfg)
    print("== generate")
    generate.run(cfg, paths)
    print("== validate")
    report = validate.run_checks(paths, cfg)
    print(f"validation: {'PASSED' if report.valid else 'FAILED'} ({report.error_count} errors, {report.warning_count} warnings)")
    print("== stats")
    stats.run(paths)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
