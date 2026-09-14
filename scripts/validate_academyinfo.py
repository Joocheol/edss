#!/usr/bin/env python3
"""Run the AcademyInfo live-validation workflow from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from academyinfo import (  # noqa: E402
    ProbeDefaults,
    ValidationError,
    load_validation_context,
    run_validation,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate all 103 AcademyInfo operations without writing response files.",
    )
    parser.add_argument("--survey-year", default="2025")
    parser.add_argument("--school-id", default="0000027")
    parser.add_argument("--comparison-school-id", default="0000014")
    parser.add_argument("--school-division-code", default="01")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only the final JSON summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    defaults = ProbeDefaults(
        survey_year=args.survey_year,
        school_id=args.school_id,
        comparison_school_id=args.comparison_school_id,
        school_division_code=args.school_division_code,
    )
    try:
        context = load_validation_context(
            REPO_ROOT / ".env",
            REPO_ROOT / "config" / "academyinfo" / "endpoints.yaml",
            REPO_ROOT / "config" / "academyinfo" / "runtime.yaml",
        )
        progress = None if args.json else print
        result = run_validation(context, defaults=defaults, progress=progress)
    except ValidationError as error:
        print(f"AcademyInfo validation mismatch: {error}", file=sys.stderr)
        return 2
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        print(f"AcademyInfo validation failed: {error}", file=sys.stderr)
        return 1

    summary = result.summary()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            "AcademyInfo validation passed: "
            f"{summary['operations_passed']}/{summary['operations']} operations, "
            f"{summary['operations_with_rows']} with rows, "
            f"{summary['operations_with_normal_zero_rows']} normal zero rows, "
            f"SchoolMajor boundary duplicates={summary['school_major_page_boundary_duplicates']}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
