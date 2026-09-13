#!/usr/bin/env python3
"""Resumable, secret-safe collector for data.go.kr AcademyInfo XML APIs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

KEY_ENV_NAMES = ("DATA_GO_KR_SERVICE_KEY", "PUBLIC_DATA_SERVICE_KEY", "SERVICE_KEY")
USER_AGENT = "edss-panel-collector/0.1 (+public research reproducibility)"
AUTH_CODES = {"20", "30", "31"}
RATE_LIMIT_CODES = {"22", "23"}

STUDENT_FIELD_DICTIONARY = {
    "indctId": ("지표아이디", "string", "지표별", "빈 태그 또는 필드 부재"),
    "indctVal1": ("지표값", "string", "지표별", "빈 태그 또는 필드 부재"),
    "schlDivNm": ("학교종류", "string", "명칭", "빈 태그 또는 필드 부재"),
    "schlEstbNm": ("설립구분", "string", "명칭", "빈 태그 또는 필드 부재"),
    "schlId": ("학교아이디", "string", "식별자", "빈 태그 또는 필드 부재"),
    "schlKrnNm": ("학교한글명", "string", "명칭", "빈 태그 또는 필드 부재"),
    "svyYr": ("공시년도", "string", "연도", "빈 태그 또는 필드 부재"),
}


class ApiResponseError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"API error {code}: {message}")
        self.code = code
        self.message = message


class DuplicateItemsError(RuntimeError):
    pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip("\"'")


def service_key() -> tuple[str | None, str | None]:
    for name in KEY_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None


def key_candidates(raw: str, mode: str) -> list[tuple[str, str]]:
    decoded = urllib.parse.unquote(raw)
    candidates: list[tuple[str, str]] = []
    if mode in ("auto", "decoded"):
        candidates.append(("normalized", decoded))
    if mode in ("auto", "encoded") and "%" in raw:
        candidates.append(("literal_encoded", raw))
    unique: list[tuple[str, str]] = []
    signatures = set()
    for label, value in candidates:
        signature = (label == "literal_encoded", value)
        if signature not in signatures:
            signatures.add(signature)
            unique.append((label, value))
    return unique


def build_url(base_url: str, operation: str, params: dict[str, str], key: str, candidate_mode: str) -> str:
    operation = "/" + operation.lstrip("/")
    if candidate_mode == "literal_encoded":
        query = urllib.parse.urlencode(params) + "&serviceKey=" + key
    else:
        query = urllib.parse.urlencode({**params, "serviceKey": key})
    return base_url.rstrip("/") + operation + "?" + query


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    for child in node.iter():
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_xml(data: bytes) -> dict:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ApiResponseError("INVALID_XML", str(exc)) from exc

    common_header = next((node for node in root.iter() if local_name(node.tag) == "cmmMsgHeader"), None)
    if common_header is not None:
        code = child_text(common_header, "returnReasonCode") or "UNKNOWN"
        message = child_text(common_header, "returnAuthMsg") or child_text(common_header, "errMsg") or "OpenAPI service error"
        raise ApiResponseError(code, message)

    header = next((node for node in root.iter() if local_name(node.tag) == "header"), None)
    code = child_text(header, "resultCode")
    message = child_text(header, "resultMsg")
    if code and code not in {"00", "0"}:
        raise ApiResponseError(code, message)

    body = next((node for node in root.iter() if local_name(node.tag) == "body"), None)
    items: list[dict[str, str]] = []
    if body is not None:
        for node in body.iter():
            if local_name(node.tag) != "item":
                continue
            items.append({local_name(child.tag): (child.text or "").strip() for child in list(node)})
    total_text = child_text(body, "totalCount")
    page_text = child_text(body, "pageNo")
    rows_text = child_text(body, "numOfRows")
    return {
        "result_code": code,
        "result_message": message,
        "total_count": int(total_text or 0),
        "page_no": int(page_text or 1),
        "num_rows": int(rows_text or 0),
        "items": items,
    }


def get_bytes(url: str, retries: int, timeout: int, delay: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if body:
                try:
                    parse_xml(body)
                except ApiResponseError as api_error:
                    raise api_error from exc
            if exc.code < 500 and exc.code != 429:
                raise
            if attempt >= retries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt >= retries:
                raise
        time.sleep(max(delay, 2 ** attempt))
    raise RuntimeError("unreachable")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(data)
    os.replace(partial, path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def valid_cached_page(path: Path) -> tuple[bytes, dict] | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    try:
        parsed = parse_xml(data)
    except ApiResponseError:
        return None
    if parsed["result_code"] not in {"00", "0"}:
        return None
    return data, parsed


def choose_service(config: list[dict], value: str) -> dict:
    normalized = value.strip().lower()
    for service in config:
        candidates = {
            service.get("key", "").lower(),
            service.get("id", "").lower(),
            service.get("base_url", "").rstrip("/").rsplit("/", 1)[-1].lower(),
        }
        if normalized in candidates:
            return service
    raise KeyError(f"unknown service: {value}")


def resolve_operation(service: dict, value: str) -> dict:
    path = "/" + value.lstrip("/")
    for operation in service.get("operations", []):
        if operation["path"] == path:
            return operation
    raise KeyError(f"operation not present in official configuration: {path}")


def request_page(service: dict, operation: dict, params: dict[str, str], raw_key: str, key_mode: str, retries: int, timeout: int, delay: float) -> tuple[bytes, dict, str]:
    last_error: Exception | None = None
    for candidate_mode, candidate in key_candidates(raw_key, key_mode):
        url = build_url(service["base_url"], operation["path"], params, candidate, candidate_mode)
        try:
            data = get_bytes(url, retries, timeout, delay)
            parsed = parse_xml(data)
            return data, parsed, candidate_mode
        except ApiResponseError as exc:
            last_error = exc
            if exc.code not in AUTH_CODES:
                raise
            logging.warning("authentication attempt failed using %s key interpretation", candidate_mode)
    if last_error:
        raise last_error
    raise RuntimeError("no service-key candidate was generated")


def collect_year(service: dict, operation: dict, school_id: str, year: int, raw_root: Path, manifest: Path, raw_key: str, args: argparse.Namespace, request_budget: list[int]) -> dict:
    page = 1
    all_items: list[dict[str, str]] = []
    page_records = []
    while True:
        page_path = raw_root / service["key"] / operation["path"].lstrip("/") / str(year) / f"page_{page:04d}.xml"
        cached = valid_cached_page(page_path)
        key_interpretation = "cached"
        if cached:
            data, parsed = cached
        else:
            if request_budget[0] >= args.max_requests:
                raise RuntimeError("request budget exhausted before collection completed")
            params = {"pageNo": str(page), "numOfRows": str(args.num_rows), "schlId": school_id, "svyYr": str(year)}
            request_budget[0] += 1
            data, parsed, key_interpretation = request_page(service, operation, params, raw_key, args.key_mode, args.retries, args.timeout, args.delay)
            atomic_write(page_path, data)
            time.sleep(args.delay)
        record = {
            "service": service["key"],
            "operation": operation["path"],
            "school_id": school_id,
            "year": year,
            "page": page,
            "item_count": len(parsed["items"]),
            "total_count": parsed["total_count"],
            "result_code": parsed["result_code"],
            "result_message": parsed["result_message"],
            "relative_path": page_path.as_posix(),
            "bytes": len(data),
            "sha256": sha256_bytes(data),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "key_interpretation": key_interpretation,
        }
        page_records.append(record)
        all_items.extend(parsed["items"])
        total = parsed["total_count"]
        if not parsed["items"] or len(all_items) >= total or page * args.num_rows >= total:
            break
        page += 1

    fingerprints = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in all_items]
    if len(fingerprints) != len(set(fingerprints)):
        raise DuplicateItemsError(f"duplicate API items across pages for {year}")
    for record in page_records:
        append_jsonl(manifest, record)
    fields = sorted({field for item in all_items for field in item})
    return {
        "year": year,
        "result_code": page_records[0]["result_code"],
        "result_message": page_records[0]["result_message"],
        "total_count": page_records[0]["total_count"],
        "item_count": len(all_items),
        "fields": fields,
        "sample_items": all_items[:3],
        "pages": len(page_records),
    }


def write_field_dictionary(path: Path, service: dict, operation: dict, summaries: list[dict]) -> None:
    prefix = "body.items.item."
    declared = {
        field[len(prefix):]
        for field in operation.get("response_fields", [])
        if field.startswith(prefix) and "." not in field[len(prefix):]
    }
    observed = {field for summary in summaries for field in summary["fields"]}
    fields = sorted(declared | observed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        names = ["service", "operation", "original_field", "korean_meaning", "data_type", "unit", "missing_value_definition"]
        writer = csv.DictWriter(handle, fieldnames=names, lineterminator="\n")
        writer.writeheader()
        for field in fields:
            korean, dtype, unit, missing = STUDENT_FIELD_DICTIONARY.get(field, ("확인 필요", "string", "확인 필요", "빈 태그 또는 필드 부재"))
            writer.writerow({"service": service["key"], "operation": operation["path"], "original_field": field, "korean_meaning": korean, "data_type": dtype, "unit": unit, "missing_value_definition": missing})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/api_services.json"))
    parser.add_argument("--service", default="student")
    parser.add_argument("--operation", default="getComparisonEnrolledStudentCrntSt")
    parser.add_argument("--school-id", default="0000149")
    parser.add_argument("--years", nargs="+", default=["2008", "2009", "latest"])
    parser.add_argument("--latest-lookback", type=int, default=7)
    parser.add_argument("--num-rows", type=int, default=999)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/open_api"))
    parser.add_argument("--manifest", type=Path, default=Path("data/metadata/api_manifest.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("data/metadata/student_service_validation.json"))
    parser.add_argument("--field-dictionary", type=Path, default=Path("data/metadata/api_field_dictionary.csv"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--key-mode", choices=("auto", "decoded", "encoded"), default="auto")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--log", type=Path, default=Path("logs/api_collection.log"))
    parser.add_argument("--schema-only", action="store_true", help="write the official response-field dictionary without using an API key")
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), logging.FileHandler(args.log, encoding="utf-8")])
    config = json.loads(args.config.read_text(encoding="utf-8"))
    service = choose_service(config, args.service)
    operation = resolve_operation(service, args.operation)
    if args.schema_only:
        write_field_dictionary(args.field_dictionary, service, operation, [])
        logging.info("wrote official response-field dictionary without making an API request")
        return
    load_dotenv(args.dotenv)
    raw_key, key_source = service_key()
    if not raw_key:
        logging.error("no API key found; set DATA_GO_KR_SERVICE_KEY or create a local .env")
        raise SystemExit(2)
    logging.info("using API key from %s (value redacted)", key_source)

    requested_years = [int(value) for value in args.years if value != "latest"]
    summaries = []
    budget = [0]
    try:
        for year in requested_years:
            summaries.append(collect_year(service, operation, args.school_id, year, args.raw_root, args.manifest, raw_key, args, budget))
        if "latest" in args.years:
            current_year = datetime.now().year
            latest_summary = None
            for year in range(current_year, current_year - args.latest_lookback - 1, -1):
                summary = collect_year(service, operation, args.school_id, year, args.raw_root, args.manifest, raw_key, args, budget)
                if summary["item_count"] > 0:
                    latest_summary = summary
                    break
            summaries.append(latest_summary or summary)
    except ApiResponseError as exc:
        failure_report = {
            "service": service["key"],
            "base_url": service["base_url"],
            "operation": operation["path"],
            "school_id": args.school_id,
            "school_name_expected": "연세대학교" if args.school_id == "0000149" else "",
            "requested_years": args.years,
            "num_rows": args.num_rows,
            "request_count_this_run": budget[0],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "api_error",
            "error_code": exc.code,
            "error_message": exc.message,
            "results": summaries,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(failure_report, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.error("API collection stopped code=%s message=%s", exc.code, exc.message)
        raise SystemExit(3) from None

    report = {
        "service": service["key"],
        "base_url": service["base_url"],
        "operation": operation["path"],
        "school_id": args.school_id,
        "school_name_expected": "연세대학교" if args.school_id == "0000149" else "",
        "num_rows": args.num_rows,
        "request_count_this_run": budget[0],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": summaries,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_field_dictionary(args.field_dictionary, service, operation, summaries)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
