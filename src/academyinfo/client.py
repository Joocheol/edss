"""Small, dependency-light client utilities for the AcademyInfo Open API."""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

import yaml


@dataclass(frozen=True)
class ProbeDefaults:
    """Representative values used for bounded endpoint probes."""

    survey_year: str = "2025"
    school_id: str = "0000027"
    comparison_school_id: str = "0000014"
    school_division_code: str = "01"


INDICATOR_OVERRIDES = {
    "getComparisonFullTimeFacultyResearchCrntSt": "33",
    "getComparisonFullTimeFacultyEnsureCrntSt": "67",
}


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from *path*."""

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"YAML root must be a mapping: {path}")
    return value


def load_service_key(path: Path, name: str = "ACADEMYINFO_SERVICE_KEY") -> str:
    """Read and normalize one service key without exporting it to process state."""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            service_key = unquote(value.strip().strip('"').strip("'"))
            if not service_key:
                break
            return service_key
    raise KeyError(f"{name} is missing or empty in {path}")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root: ET.Element, names: tuple[str, ...]) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) in names:
            return (element.text or "").strip()
    return None


def _exact_rows(root: ET.Element) -> list[tuple[tuple[str, str], ...]]:
    return [
        tuple((_local_name(child.tag), (child.text or "").strip()) for child in list(element))
        for element in root.iter()
        if _local_name(element.tag) == "item"
    ]


def _parse_xml_response(payload: bytes, http_status: int, elapsed_ms: int) -> dict[str, Any]:
    root = ET.fromstring(payload)
    total_text = _first_text(root, ("totalCount",))
    rows = _exact_rows(root)
    return {
        "http_status": http_status,
        "result_code": _first_text(root, ("resultCode", "returnReasonCode")),
        "result_message": _first_text(
            root,
            ("resultMsg", "resultMessage", "returnAuthMsg", "errMsg"),
        ),
        "total_count": int(total_text) if total_text not in (None, "") else 0,
        "rows": rows,
        "row_count": len(rows),
        "school_ids": sorted(
            {
                dict(row).get("schlId")
                for row in rows
                if dict(row).get("schlId")
            }
        ),
        "response_sha256": hashlib.sha256(payload).hexdigest(),
        "elapsed_ms": elapsed_ms,
    }


def representative_params(
    endpoint: Mapping[str, Any],
    defaults: ProbeDefaults,
    *,
    page_no: int = 1,
    num_rows: int = 1,
) -> dict[str, str]:
    """Build non-secret representative parameters from an endpoint scope strategy."""

    params = {"pageNo": str(page_no), "numOfRows": str(num_rows)}
    strategy = endpoint["scope_strategy"]
    if strategy == "per_school_by_year":
        params.update({"schlId": defaults.school_id, "svyYr": defaults.survey_year})
    elif strategy == "regional_by_school_type":
        params["schlDivCd"] = defaults.school_division_code
    elif strategy == "regional_all_school_types":
        pass
    elif strategy == "nationwide_by_year":
        params.update({"svyYr": defaults.survey_year, "schlKrnNm": ""})
    elif strategy == "lookup_by_year":
        params["svyYr"] = defaults.survey_year
    elif strategy != "lookup_once":
        raise ValueError(f"Unknown scope_strategy: {strategy}")

    indicator_id = INDICATOR_OVERRIDES.get(str(endpoint["operation"]))
    if indicator_id:
        params["indctId"] = indicator_id
    return params


class AcademyInfoClient:
    """HTTP client that keeps the service key out of caller parameters and results."""

    def __init__(
        self,
        service_key: str,
        *,
        timeout_seconds: int = 30,
        attempts: int = 3,
        user_agent: str = "edss-academyinfo-validation/1.0",
    ) -> None:
        if not service_key:
            raise ValueError("service_key must not be empty")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._service_key = unquote(service_key)
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.user_agent = user_agent

    def _build_query(
        self,
        params: Mapping[str, str],
        repeated_school_ids: Sequence[str] | None = None,
    ) -> str:
        pairs = [("serviceKey", self._service_key)]
        pairs.extend(
            (key, value)
            for key, value in params.items()
            if key != "serviceKey" and value is not None
        )
        if repeated_school_ids is not None:
            pairs = [(key, value) for key, value in pairs if key != "schlId"]
            pairs.extend(("schlId", school_id) for school_id in repeated_school_ids)
        return urlencode(pairs)

    def request_xml(
        self,
        endpoint_url: str,
        params: Mapping[str, str],
        *,
        repeated_school_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Request and parse one XML response, retrying transient transport failures."""

        request = Request(
            f"{endpoint_url}?{self._build_query(params, repeated_school_ids)}",
            headers={"User-Agent": self.user_agent, "Accept": "application/xml"},
        )
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            started = time.perf_counter()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read()
                    http_status = response.status
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                return _parse_xml_response(payload, http_status, elapsed_ms)
            except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as error:
                last_error = error
                if attempt + 1 < self.attempts:
                    time.sleep(0.5 * (2**attempt))
        if isinstance(last_error, HTTPError):
            error_summary = f"HTTP {last_error.code}"
        else:
            error_summary = type(last_error).__name__
        raise RuntimeError(
            f"Request failed after {self.attempts} attempts: {error_summary}"
        )

    def probe_endpoint(
        self,
        endpoint: Mapping[str, Any],
        defaults: ProbeDefaults,
    ) -> dict[str, Any]:
        """Run one bounded representative probe and return a secret-free summary."""

        try:
            result = self.request_xml(
                str(endpoint["endpoint_url"]),
                representative_params(endpoint, defaults),
            )
            return {
                "service": endpoint["service"],
                "operation": endpoint["operation"],
                "scope_strategy": endpoint["scope_strategy"],
                **{key: value for key, value in result.items() if key != "rows"},
                "error": None,
            }
        except Exception as error:
            return {
                "service": endpoint["service"],
                "operation": endpoint["operation"],
                "scope_strategy": endpoint["scope_strategy"],
                "error": f"{type(error).__name__}: {error}",
            }

    def probe_endpoints(
        self,
        endpoints: Iterable[Mapping[str, Any]],
        defaults: ProbeDefaults,
        *,
        max_workers: int,
    ) -> list[dict[str, Any]]:
        """Probe endpoints concurrently and return results in stable service/operation order."""

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self.probe_endpoint, endpoint, defaults)
                for endpoint in endpoints
            ]
            results = [future.result() for future in as_completed(futures)]
        return sorted(results, key=lambda row: (row["service"], row["operation"]))
