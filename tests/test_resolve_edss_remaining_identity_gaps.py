import csv
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import resolve_edss_remaining_identity_gaps as resolution


FIELDS = [
    "_panel_year", "개방ID", "학교명", "본분교명", "시도명", "학교종류명",
    "대학대학원구분명", "대학계열명", "단과대학명", "학과명",
    *resolution.PUBLIC_FIELDS[9:],
    *resolution.PROVENANCE_FIELDS,
]


def write_panel(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def make_row(year: str, open_id: str, school: str, department: str, number: int) -> dict:
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "_panel_year": year,
            "개방ID": open_id,
            "학교명": school,
            "본분교명": "본교",
            "시도명": "서울특별시",
            "학교종류명": "대학",
            "단과대학명": "단과대",
            "학과명": department,
            "_source_archive": "archive.zip",
            "_source_member": "member.csv",
            "_source_row_number": str(number),
            "_source_row_id": f"row-{number}",
        }
    )
    return row


def write_bridge(path: Path) -> None:
    fields = ["_panel_year", "개방ID", "_0101_provinces", "_0101_branch_names"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "_panel_year": "2023",
                "개방ID": "A",
                "_0101_provinces": "서울",
                "_0101_branch_names": "본교",
            }
        )


def write_high_panel_decision(path: Path, expected_row_count: str = "16") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=resolution.HIGH_MANUAL_DECISION_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "source": "대학정보공시",
                "catalog_code": "1209",
                "year": "2019",
                "open_id": "X",
                "expected_row_count": expected_row_count,
                "decision_status": "approved",
                "manual_classification": "confirmed_degree_program_scope_gap_same_open_id",
                "confirmed_entity_name": "테스트캠퍼스",
                "evidence_url": "https://example.edu/history",
                "evidence_summary": "공식 연혁과 연도별 행을 대조했다.",
                "recommended_handling": "retain_same_open_id_preserve_rows_no_imputation",
            }
        )


def write_employment_scope_decision(
    path: Path,
    expected_source_row_count: str = "4",
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=resolution.EMPLOYMENT_SCOPE_DECISION_FIELDS,
        )
        writer.writeheader()
        writer.writerow(
            {
                "source": "취업통계",
                "catalog_code": "0001",
                "dataset": "학생인적취업정보",
                "excluded_years": "2023|2024",
                "expected_source_row_count": expected_source_row_count,
                "expected_school_year_identity_count": "2",
                "expected_inferred_applied_row_count": "1",
                "expected_remaining_missing_open_id_row_count": "3",
                "decision_status": "approved",
                "decision_classification": "schema_break_excluded",
                "excluded_from_scope": "legacy_open_id_longitudinal_panel",
                "evidence_artifacts": "official.json|application.json",
                "evidence_summary": "원천 스키마와 식별자 제공 범위가 바뀌었다.",
                "recommended_handling": (
                    "preserve_raw_exclude_from_legacy_panel_"
                    "no_canonical_imputation_standalone_reference_only"
                ),
            }
        )


def write_employment_scope_audits(root: Path) -> tuple[Path, Path]:
    official_path = root / "official.json"
    official_path.write_text(
        json.dumps(
            {
                "crosswalk_conclusion": {"official_crosswalk_available": False},
                "employment_raw_headers": [
                    {"year": "2023", "has_open_id": False, "column_count": 24},
                    {"year": "2024", "has_open_id": False, "column_count": 24},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    application_path = root / "application.json"
    application_path.write_text(
        json.dumps(
            {
                "application": {
                    "source_row_count": 4,
                    "source_school_year_identity_count": 2,
                    "applied_row_count": 1,
                    "remaining_missing_open_id_row_count": 3,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return official_path, application_path


class RemainingIdentityGapTests(unittest.TestCase):
    def test_employment_candidates_never_impute_canonical_open_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            panel = root / "panel.csv.gz"
            rows = []
            for index, department in enumerate(["학과1", "학과2", "학과3"], start=1):
                rows.append(make_row("2022", "A", "", department, index))
                rows.append(make_row("2023", "", "테스트대학교", department, index + 10))
            rows.append(make_row("2023", "", "소규모대학교", "단일학과", 30))
            write_panel(panel, rows)
            bridge = root / "bridge.csv"
            write_bridge(bridge)

            derived, candidates, summary = resolution.build_employment_outputs(panel, bridge)

            self.assertEqual(len(derived), 4)
            by_school = {row["학교명"]: row for row in candidates}
            matched = by_school["테스트대학교"]
            self.assertEqual(matched["candidate_open_id"], "A")
            self.assertEqual(matched["resolution_status"], "candidate_signature_context_confirmed")
            self.assertEqual(by_school["소규모대학교"]["resolution_status"], "unresolved_signature_too_small")
            self.assertEqual(summary["canonical_open_id_imputed_row_count"], 0)
            self.assertNotIn("개방ID", derived[0])

    def test_approved_schema_break_excludes_all_rows_from_legacy_panel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            decision_path = root / "decision.csv"
            write_employment_scope_decision(decision_path)
            official_path, application_path = write_employment_scope_audits(root)
            employment_summary = {
                "source_missing_open_id_row_count": 4,
                "current_school_year_identity_count": 2,
                "canonical_open_id_imputed_row_count": 0,
            }

            result = resolution.apply_employment_scope_decision(
                employment_summary,
                decision_path,
                official_path,
                application_path,
            )

            self.assertEqual(result["status"], "complete_with_scope_exclusion")
            self.assertEqual(result["scope_excluded_row_count"], 4)
            self.assertEqual(result["scope_excluded_school_year_identity_count"], 2)
            self.assertEqual(result["reference_only_inferred_open_id_row_count"], 1)
            self.assertEqual(result["unresolved_open_id_row_count_at_exclusion"], 3)
            self.assertEqual(result["legacy_panel_eligible_row_count"], 0)
            self.assertTrue(result["raw_and_derived_records_preserved"])

    def test_schema_break_decision_rejects_source_row_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            decision_path = root / "decision.csv"
            write_employment_scope_decision(
                decision_path,
                expected_source_row_count="5",
            )
            official_path, application_path = write_employment_scope_audits(root)
            employment_summary = {
                "source_missing_open_id_row_count": 4,
                "current_school_year_identity_count": 2,
            }

            with self.assertRaisesRegex(RuntimeError, "source-row mismatch"):
                resolution.apply_employment_scope_decision(
                    employment_summary,
                    decision_path,
                    official_path,
                    application_path,
                )

    def test_high_panel_review_separates_boundary_and_internal_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit = {
                "high_panels": [
                    {"source": "대학정보공시", "catalog_code": code, "dataset": code,
                     "row_count": 1000, "orphan_row_rate": 0.02}
                    for code in ("0202", "0204", "1102", "1209")
                ]
            }
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
            orphan_path = root / "orphans.csv"
            fields = ["source", "catalog_code", "dataset", "year", "open_id", "row_count",
                      "classification", "first_0101_year", "last_0101_year"]
            with orphan_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for code in ("0202", "0204", "1102"):
                    writer.writerow({"source": "대학정보공시", "catalog_code": code, "dataset": code,
                                     "year": "2009", "open_id": code, "row_count": "2",
                                     "classification": "before_first_0101_year",
                                     "first_0101_year": "2010", "last_0101_year": "2025"})
                writer.writerow({"source": "대학정보공시", "catalog_code": "1209", "dataset": "1209",
                                 "year": "2019", "open_id": "X", "row_count": "16",
                                 "classification": "internal_0101_gap",
                                 "first_0101_year": "2010", "last_0101_year": "2025"})

            reviews, summary = resolution.review_high_panels(audit_path, orphan_path)

            self.assertEqual(summary["explained_temporal_boundary_panel_count"], 3)
            self.assertEqual(summary["manual_review_required_panel_count"], 1)
            self.assertEqual(summary["manual_review_keys"][0]["open_id"], "X")
            self.assertEqual(next(row for row in reviews if row["catalog_code"] == "1209")["internal_gap_row_count"], 16)

    def test_high_panel_review_accepts_current_audit_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            panels = [
                {"source": "대학정보공시", "catalog_code": code, "dataset": code,
                 "row_count": 1000, "orphan_row_rate": 0.02, "severity": "high"}
                for code in ("0202", "0204", "1102", "1209")
            ]
            panels.append(
                {"source": "대학정보공시", "catalog_code": "9999", "dataset": "9999",
                 "row_count": 1000, "orphan_row_rate": 0.001, "severity": "medium"}
            )
            audit_path = root / "audit.json"
            audit_path.write_text(
                json.dumps({"highest_risk_panels": panels}, ensure_ascii=False),
                encoding="utf-8",
            )
            orphan_path = root / "orphans.csv"
            fields = ["source", "catalog_code", "dataset", "year", "open_id", "row_count",
                      "classification", "first_0101_year", "last_0101_year"]
            with orphan_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for code in ("0202", "0204", "1102", "1209"):
                    writer.writerow({"source": "대학정보공시", "catalog_code": code,
                                     "dataset": code, "year": "2009", "open_id": code,
                                     "row_count": "2", "classification": "before_first_0101_year",
                                     "first_0101_year": "2010", "last_0101_year": "2025"})

            reviews, summary = resolution.review_high_panels(audit_path, orphan_path)

            self.assertEqual(len(reviews), 4)
            self.assertEqual(summary["explained_temporal_boundary_panel_count"], 4)

    def test_approved_manual_decision_closes_internal_gap_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit = {
                "high_panels": [
                    {
                        "source": "대학정보공시",
                        "catalog_code": code,
                        "dataset": code,
                        "row_count": 1000,
                        "orphan_row_rate": 0.02,
                    }
                    for code in ("0202", "0204", "1102", "1209")
                ]
            }
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
            orphan_path = root / "orphans.csv"
            fields = [
                "source", "catalog_code", "dataset", "year", "open_id", "row_count",
                "classification", "first_0101_year", "last_0101_year",
            ]
            with orphan_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for code in ("0202", "0204", "1102"):
                    writer.writerow(
                        {
                            "source": "대학정보공시",
                            "catalog_code": code,
                            "dataset": code,
                            "year": "2009",
                            "open_id": code,
                            "row_count": "2",
                            "classification": "before_first_0101_year",
                            "first_0101_year": "2010",
                            "last_0101_year": "2025",
                        }
                    )
                writer.writerow(
                    {
                        "source": "대학정보공시",
                        "catalog_code": "1209",
                        "dataset": "1209",
                        "year": "2019",
                        "open_id": "X",
                        "row_count": "16",
                        "classification": "internal_0101_gap",
                        "first_0101_year": "2010",
                        "last_0101_year": "2025",
                    }
                )
            decision_path = root / "decisions.csv"
            write_high_panel_decision(decision_path)

            reviews, summary = resolution.review_high_panels(
                audit_path,
                orphan_path,
                decision_path,
            )

            panel = next(row for row in reviews if row["catalog_code"] == "1209")
            self.assertEqual(panel["review_disposition"], "explained_manual_identity_scope_decision")
            self.assertEqual(panel["manually_resolved_key_count"], 1)
            self.assertEqual(panel["manually_resolved_row_count"], 16)
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["manual_review_required_panel_count"], 0)
            self.assertEqual(summary["manually_resolved_key_count"], 1)
            self.assertEqual(summary["manual_review_keys"], [])

    def test_manual_decision_rejects_row_count_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "decisions.csv"
            write_high_panel_decision(path, expected_row_count="not-a-number")
            with self.assertRaisesRegex(RuntimeError, "invalid expected row count"):
                resolution.load_high_panel_manual_decisions(path)


if __name__ == "__main__":
    unittest.main()
