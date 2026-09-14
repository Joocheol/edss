"""Pure, deterministic task planning for AcademyInfo collection runs.

This module deliberately performs no I/O and never accepts an API key.  Callers
load the endpoint configuration, pass it here as a mapping, and execute the
returned tasks elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


SUPPORTED_SCOPE_STRATEGIES = frozenset(
    {
        "per_school_by_year",
        "nationwide_by_year",
        "regional_by_school_type",
        "regional_all_school_types",
        "lookup_once",
        "lookup_by_year",
    }
)

FORBIDDEN_FIXED_PARAM_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "service_key",
        "servicekey",
        "token",
    }
)

SCOPE_PARAM_KEYS = frozenset({"schlDivCd", "schlId", "svyYr"})


class CollectionPlanningError(ValueError):
    """Raised when collection scope inputs cannot produce valid tasks."""


@dataclass(frozen=True)
class CollectionTask:
    """One secret-free AcademyInfo request scope.

    ``params`` is an immutable, key-sorted tuple so tasks are comparable and
    deterministic.  Use :meth:`request_params` when a mutable mapping is needed
    by the HTTP client.
    """

    task_id: str
    data_id: str
    service: str
    operation: str
    endpoint_url: str
    scope_strategy: str
    params: tuple[tuple[str, str], ...]

    def request_params(self) -> dict[str, str]:
        """Return this task's non-secret request parameters as a new mapping."""

        return dict(self.params)


@dataclass(frozen=True)
class _Endpoint:
    data_id: str
    service: str
    operation: str
    endpoint_url: str
    scope_strategy: str
    fixed_params: tuple[tuple[str, str], ...]


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CollectionPlanningError(f"{label} must be a non-empty string")
    return value


def _sorted_unique_strings(
    values: Iterable[str] | None,
    label: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    if values is None:
        if required:
            raise CollectionPlanningError(f"Missing required input: {label}")
        return ()
    if isinstance(values, (str, bytes)):
        raise CollectionPlanningError(f"{label} must be an iterable of strings")

    try:
        materialized = tuple(values)
    except TypeError as error:
        raise CollectionPlanningError(
            f"{label} must be an iterable of strings"
        ) from error

    invalid = [value for value in materialized if not isinstance(value, str) or not value]
    if invalid:
        raise CollectionPlanningError(
            f"{label} must contain only non-empty strings; got {invalid[0]!r}"
        )
    normalized = tuple(sorted(set(materialized)))
    if required and not normalized:
        raise CollectionPlanningError(f"{label} must not be empty")
    return normalized


def _normalize_endpoints(config: Mapping[str, Any]) -> tuple[_Endpoint, ...]:
    endpoints_value = config.get("endpoints")
    if not isinstance(endpoints_value, list) or not endpoints_value:
        raise CollectionPlanningError(
            "Endpoint config must contain a non-empty 'endpoints' list"
        )

    by_key: dict[tuple[str, str], _Endpoint] = {}
    for index, raw_endpoint in enumerate(endpoints_value):
        label = f"endpoints[{index}]"
        if not isinstance(raw_endpoint, Mapping):
            raise CollectionPlanningError(f"{label} must be a mapping")
        raw_fixed_params = raw_endpoint.get("fixed_params", {})
        if not isinstance(raw_fixed_params, Mapping):
            raise CollectionPlanningError(f"{label}.fixed_params must be a mapping")
        fixed_params: dict[str, str] = {}
        for raw_key, raw_value in raw_fixed_params.items():
            key = _nonempty_string(raw_key, f"{label}.fixed_params key")
            if not isinstance(raw_value, str):
                raise CollectionPlanningError(
                    f"{label}.fixed_params[{key!r}] must be a string"
                )
            value = raw_value
            if key.lower() in FORBIDDEN_FIXED_PARAM_KEYS:
                raise CollectionPlanningError(
                    f"{label}.fixed_params must not contain secret parameter {key!r}"
                )
            if key in SCOPE_PARAM_KEYS:
                raise CollectionPlanningError(
                    f"{label}.fixed_params must not contain scope parameter {key!r}"
                )
            fixed_params[key] = value

        endpoint = _Endpoint(
            data_id=_nonempty_string(raw_endpoint.get("data_id"), f"{label}.data_id"),
            service=_nonempty_string(raw_endpoint.get("service"), f"{label}.service"),
            operation=_nonempty_string(
                raw_endpoint.get("operation"), f"{label}.operation"
            ),
            endpoint_url=_nonempty_string(
                raw_endpoint.get("endpoint_url"), f"{label}.endpoint_url"
            ),
            scope_strategy=_nonempty_string(
                raw_endpoint.get("scope_strategy"), f"{label}.scope_strategy"
            ),
            fixed_params=tuple(sorted(fixed_params.items())),
        )
        if endpoint.scope_strategy not in SUPPORTED_SCOPE_STRATEGIES:
            raise CollectionPlanningError(
                f"Unknown scope_strategy for {endpoint.service}/{endpoint.operation}: "
                f"{endpoint.scope_strategy}"
            )

        key = (endpoint.service, endpoint.operation)
        previous = by_key.get(key)
        if previous is not None and previous != endpoint:
            raise CollectionPlanningError(
                f"Conflicting duplicate endpoint: {endpoint.service}/{endpoint.operation}"
            )
        by_key[key] = endpoint

    return tuple(
        sorted(
            by_key.values(),
            key=lambda item: (item.service, item.operation, item.endpoint_url),
        )
    )


def _normalize_schools_by_year(
    schools_by_year: Mapping[str, Iterable[str]] | None,
    *,
    required_years: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    if schools_by_year is None:
        if required_years:
            raise CollectionPlanningError("Missing required input: schools_by_year")
        return {}
    if not isinstance(schools_by_year, Mapping):
        raise CollectionPlanningError("schools_by_year must be a mapping")

    normalized: dict[str, tuple[str, ...]] = {}
    for raw_year, school_ids in schools_by_year.items():
        year = _nonempty_string(raw_year, "schools_by_year key")
        normalized[year] = _sorted_unique_strings(
            school_ids,
            f"schools_by_year[{year!r}]",
            required=year in required_years,
        )

    for year in required_years:
        if year not in normalized:
            raise CollectionPlanningError(
                f"Missing required school list for survey year {year}"
            )
        if not normalized[year]:
            raise CollectionPlanningError(
                f"School list for survey year {year} must not be empty"
            )
    return normalized


def _make_task(endpoint: _Endpoint, params: Mapping[str, str]) -> CollectionTask:
    conflicting_keys = set(params) & dict(endpoint.fixed_params).keys()
    if conflicting_keys:
        names = ", ".join(sorted(conflicting_keys))
        raise CollectionPlanningError(
            f"Fixed and scope parameters conflict for "
            f"{endpoint.service}/{endpoint.operation}: {names}"
        )
    merged_params = {**dict(endpoint.fixed_params), **params}
    sorted_params = tuple(sorted(merged_params.items()))
    identity = {
        "data_id": endpoint.data_id,
        "endpoint_url": endpoint.endpoint_url,
        "operation": endpoint.operation,
        "params": sorted_params,
        "scope_strategy": endpoint.scope_strategy,
        "service": endpoint.service,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    task_id = f"academyinfo-{hashlib.sha256(canonical).hexdigest()[:24]}"
    return CollectionTask(
        task_id=task_id,
        data_id=endpoint.data_id,
        service=endpoint.service,
        operation=endpoint.operation,
        endpoint_url=endpoint.endpoint_url,
        scope_strategy=endpoint.scope_strategy,
        params=sorted_params,
    )


def build_collection_tasks(
    endpoint_config: Mapping[str, Any],
    *,
    years: Iterable[str] | None = None,
    schools_by_year: Mapping[str, Iterable[str]] | None = None,
    school_division_codes: Iterable[str] | None = None,
) -> list[CollectionTask]:
    """Expand configured endpoints into a stable, duplicate-free task list.

    Years, school IDs, and school-division codes must already be strings.  They
    are never coerced from integers, which protects leading zeroes in codes.
    Inputs are required only when at least one configured strategy uses them.
    """

    if not isinstance(endpoint_config, Mapping):
        raise CollectionPlanningError("endpoint_config must be a mapping")
    endpoints = _normalize_endpoints(endpoint_config)
    strategies = {endpoint.scope_strategy for endpoint in endpoints}

    needs_years = bool(
        strategies & {"per_school_by_year", "nationwide_by_year", "lookup_by_year"}
    )
    normalized_years = _sorted_unique_strings(years, "years", required=needs_years)
    normalized_schools = _normalize_schools_by_year(
        schools_by_year,
        required_years=normalized_years if "per_school_by_year" in strategies else (),
    )
    normalized_divisions = _sorted_unique_strings(
        school_division_codes,
        "school_division_codes",
        required="regional_by_school_type" in strategies,
    )

    tasks: list[CollectionTask] = []
    for endpoint in endpoints:
        strategy = endpoint.scope_strategy
        if strategy == "per_school_by_year":
            for year in normalized_years:
                for school_id in normalized_schools[year]:
                    tasks.append(
                        _make_task(endpoint, {"schlId": school_id, "svyYr": year})
                    )
        elif strategy == "nationwide_by_year":
            for year in normalized_years:
                tasks.append(_make_task(endpoint, {"svyYr": year}))
        elif strategy == "regional_by_school_type":
            for division in normalized_divisions:
                tasks.append(_make_task(endpoint, {"schlDivCd": division}))
        elif strategy == "regional_all_school_types":
            tasks.append(_make_task(endpoint, {}))
        elif strategy == "lookup_once":
            tasks.append(_make_task(endpoint, {}))
        elif strategy == "lookup_by_year":
            for year in normalized_years:
                tasks.append(_make_task(endpoint, {"svyYr": year}))

    unique_tasks = {task.task_id: task for task in tasks}
    if len(unique_tasks) != len(tasks):
        tasks = list(unique_tasks.values())
    return sorted(
        tasks,
        key=lambda task: (
            task.service,
            task.operation,
            task.params,
            task.endpoint_url,
            task.task_id,
        ),
    )
