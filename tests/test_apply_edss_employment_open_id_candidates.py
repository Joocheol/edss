import csv
import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_edss_employment_open_id_candidates.py"
SPEC = importlib.util.spec_from_file_location("apply_open_ids", SCRIPT)
apply_open_ids = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(apply_open_ids)


SOURCE_FIELDS = [
    "_panel_year",
    "학교명",
    "본분교명",
    "시도명",
    "학교종류명",
    "_school_identity_key",
    "_source_row_id",
]
CANDIDATE_FIELDS = [
    "_panel_year",
    "_school_identity_key",
    "학교명",
    "본분교명",
    "시도명",
    "학교종류명",
    "candidate_open_id",
    "candidate_method",
    "resolution_status",
]


def source_row(row_id="row-1", key="school-1", open_id=""):
    return {
        "_panel_year": "2023",
        "학교명": "테스트대학교",
        "본분교명": "본교",
        "시도명": "서울",
        "학교종류명": "대학",
        "_school_identity_key": key,
        "_source_row_id": row_id,
        "개방ID": open_id,
    }


def candidate_row(key="school-1", open_id="1234567890"):
    return {
        "_panel_year": "2023",
        "_school_identity_key": key,
        "학교명": "테스트대학교",
        "본분교명": "본교",
        "시도명": "서울",
        "학교종류명": "대학",
        "candidate_open_id": open_id,
        "candidate_method": "academyinfo_two_year_exact_enrollment_school_context",
        "resolution_status": "candidate_two_year_exact_enrollment",
    }


class ApplyEmploymentOpenIDCandidatesTests(unittest.TestCase):
    def write_source(self, path, rows, include_open_id=False):
        fields = list(SOURCE_FIELDS)
        if include_open_id:
            fields.insert(1, "개방ID")
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row.get(field, "") for field in fields} for row in rows)

    def write_candidates(self, path, rows):
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_applies_only_matching_candidate_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.csv.gz"
            candidate_path = root / "candidates.csv"
            self.write_source(
                source_path,
                [source_row(), source_row(row_id="row-2", key="school-2")],
            )
            self.write_candidates(candidate_path, [candidate_row()])
            lookup, candidate_summary = apply_open_ids.read_candidates(candidate_path)
            rows, fields, summary = apply_open_ids.apply_candidates(source_path, lookup)

            self.assertIn("개방ID", fields)
            self.assertEqual([row["_source_row_id"] for row in rows], ["row-1", "row-2"])
            self.assertEqual(rows[0]["개방ID"], "1234567890")
            self.assertEqual(rows[0]["_open_id_resolution_status"], apply_open_ids.APPLICATION_STATUS)
            self.assertEqual(rows[1]["개방ID"], "")
            self.assertEqual(rows[1]["_open_id_resolution_status"], apply_open_ids.UNRESOLVED_STATUS)
            self.assertEqual(summary["applied_row_count"], 1)
            self.assertEqual(summary["remaining_missing_open_id_row_count"], 1)
            self.assertEqual(candidate_summary["candidate_school_year_count"], 1)

    def test_refuses_to_overwrite_conflicting_existing_open_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.csv.gz"
            self.write_source(source_path, [source_row(open_id="9999999999")], include_open_id=True)
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                apply_open_ids.apply_candidates(
                    source_path,
                    {("2023", "school-1"): candidate_row()},
                )

    def test_rejects_duplicate_candidate_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidates.csv"
            self.write_candidates(path, [candidate_row(), candidate_row(open_id="0987654321")])
            with self.assertRaisesRegex(RuntimeError, "duplicate candidate key"):
                apply_open_ids.read_candidates(path)

    def test_rejects_reverse_duplicate_open_id_within_year(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidates.csv"
            self.write_candidates(
                path,
                [candidate_row(), candidate_row(key="school-2")],
            )
            with self.assertRaisesRegex(RuntimeError, "not reverse-unique"):
                apply_open_ids.read_candidates(path)

    def test_rejects_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "source.csv.gz"
            self.write_source(source_path, [source_row()])
            candidate = candidate_row()
            candidate["학교명"] = "다른대학교"
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                apply_open_ids.apply_candidates(
                    source_path,
                    {("2023", "school-1"): candidate},
                )


if __name__ == "__main__":
    unittest.main()
