#!/usr/bin/env python3
"""Download complete AcademyInfo XML responses into immutable raw storage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from academyinfo.client import load_service_key, load_yaml  # noqa: E402
from academyinfo.collection import (  # noqa: E402
    AcademyInfoCollector,
    CollectionError,
    CollectionPolicy,
    select_endpoint_config,
)
from academyinfo.downloader import (  # noqa: E402
    AcademyInfoDownloadError,
    AcademyInfoDownloader,
    AcademyInfoLocalIOError,
)
from academyinfo.planning import CollectionPlanningError  # noqa: E402
from academyinfo.storage import AcademyInfoStorage, StorageError  # noqa: E402


def survey_year(value: str) -> str:
    if not re.fullmatch(r"\d{4}", value):
        raise argparse.ArgumentTypeError("survey year must be four digits")
    return value


def school_id(value: str) -> str:
    if not re.fullmatch(r"\d{7}", value):
        raise argparse.ArgumentTypeError(
            "school ID must be a seven-digit string, including leading zeroes"
        )
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect reviewed AcademyInfo API operations as complete XML responses. "
            "Completed tasks are verified and resumed before any new request."
        )
    )
    parser.add_argument(
        "--year",
        action="append",
        required=True,
        dest="years",
        type=survey_year,
        help="Survey year; repeat for multiple years (example: --year 2024 --year 2025).",
    )
    parser.add_argument(
        "--operation",
        action="append",
        dest="operations",
        help="Collect only this operation; repeat to select several operations.",
    )
    parser.add_argument(
        "--school-id",
        action="append",
        dest="school_ids",
        type=school_id,
        help=(
            "Limit per-school operations to this ID; repeat as needed. If omitted, "
            "getUniversityCode discovers each year's school universe."
        ),
    )
    parser.add_argument(
        "--school-division-code",
        action="append",
        dest="school_division_codes",
        choices=("01", "02"),
        help="School type for regional operations; default: 01 and 02.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        help="Concurrent requests (reviewed maximum is read from runtime.yaml).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N deterministic tasks (intended for smoke tests).",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "Print the exact task count without executing it. Uses --school-id or "
            "an already cached getUniversityCode response."
        ),
    )
    parser.add_argument(
        "--discover-schools",
        action="store_true",
        help=(
            "Allow --plan-only to download a missing getUniversityCode response. "
            "Normal collection discovers missing school lists automatically."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT,
        help="Root containing data/raw and data/metadata (default: repository root).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Suppress progress and print a JSON result.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    try:
        endpoint_config = load_yaml(
            REPO_ROOT / "config" / "academyinfo" / "endpoints.yaml"
        )
        runtime_config = load_yaml(
            REPO_ROOT / "config" / "academyinfo" / "runtime.yaml"
        )
        policy = CollectionPolicy.from_runtime_config(
            runtime_config, concurrency=args.concurrency
        )
        runtime = runtime_config["academyinfo"]
        storage = AcademyInfoStorage(args.output_root)
        selected_config = select_endpoint_config(endpoint_config, args.operations)
        needs_schools = any(
            endpoint.get("scope_strategy") == "per_school_by_year"
            for endpoint in selected_config["endpoints"]
        )
        needs_network = not args.plan_only or (
            needs_schools
            and args.school_ids is None
            and args.discover_schools
        )
        downloader = None
        if needs_network:
            service_key = load_service_key(REPO_ROOT / ".env")
            downloader = AcademyInfoDownloader(
                service_key,
                timeout_seconds=float(
                    runtime["single_page"].get(
                        "request_timeout_seconds", runtime["request_timeout_seconds"]
                    )
                ),
            )
        collector = AcademyInfoCollector(
            endpoint_config=endpoint_config,
            downloader=downloader,
            storage=storage,
            policy=policy,
            progress=None if args.json else print,
        )
        discovery = None
        schools_by_year = None
        if needs_schools and args.school_ids is None:
            discovery = collector.discover_schools(
                args.years,
                allow_download=(not args.plan_only or args.discover_schools),
            )
            schools_by_year = discovery.schools_by_year
        tasks = collector.plan(
            years=args.years,
            operations=args.operations,
            school_ids=args.school_ids,
            schools_by_year=schools_by_year,
            school_division_codes=(
                args.school_division_codes
                if args.school_division_codes is not None
                else ("01", "02")
            ),
        )
        if args.limit is not None:
            tasks = tasks[: args.limit]

        if args.plan_only:
            result = {
                "planned": len(tasks),
                "years": sorted(set(args.years)),
                "operations": len({task.operation for task in tasks}),
                "services": len({task.service for task in tasks}),
                "school_counts": (
                    None
                    if discovery is None
                    else {
                        year: len(school_ids)
                        for year, school_ids in discovery.schools_by_year.items()
                    }
                ),
                "discovery_downloaded": bool(
                    discovery
                    and any(result.request_count for result in discovery.results)
                ),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        run = collector.run(tasks)
        result = run.summary()
        task_http_requests = result["http_requests"]
        discovery_http_requests = (
            0
            if discovery is None
            else sum(item.request_count for item in discovery.results)
        )
        result = {
            **result,
            "task_http_requests": task_http_requests,
            "discovery_http_requests": discovery_http_requests,
            "http_requests": task_http_requests + discovery_http_requests,
            "school_counts": (
                None
                if discovery is None
                else {
                    year: len(school_ids)
                    for year, school_ids in discovery.schools_by_year.items()
                }
            ),
            "failures": [failure.__dict__ for failure in run.failures],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if run.failures else 0
    except (
        CollectionError,
        CollectionPlanningError,
        AcademyInfoDownloadError,
        AcademyInfoLocalIOError,
        StorageError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"AcademyInfo collection failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
