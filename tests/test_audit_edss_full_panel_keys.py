import csv
import gzip
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_edss_full_panel_keys as audit


class AuditEdssFullPanelKeysTests(unittest.TestCase):
    def test_classify_orphan_temporal_position(self):
        base = {"A": {"2015", "2017"}}
        self.assertEqual(audit.classify_orphan("2014", "A", base)[0], "before_first_0101_year")
        self.assertEqual(audit.classify_orphan("2016", "A", base)[0], "internal_0101_gap")
        self.assertEqual(audit.classify_orphan("2018", "A", base)[0], "after_last_0101_year")
        self.assertEqual(audit.classify_orphan("2018", "B", base)[0], "never_in_0101")

    def test_candidate_dimensions_exclude_measure_suffixes(self):
        fields = ["조사년도", "개방ID", "본분교명", "학과명", "재적학생수", "등록금액", "학교구분명"]
        self.assertEqual(audit.candidate_dimension_fields(fields), ["학교구분명", "본분교명", "학과명"])

    def test_audit_panel_recomputes_integrity_and_join_risk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "panel.csv.gz"
            header = audit.META_FIELDS + ["조사년도", "개방ID", "학과명"]
            rows = [
                self.make_row("2020", "A", "학과1", 2),
                self.make_row("2020", "A", "학과2", 3),
                self.make_row("2020", "B", "학과1", 4),
                self.make_row("2020", "", "학과1", 5),
            ]
            with gzip.open(output, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
            checksum = audit.sha256_file(output)
            catalog_row = {
                "source": "대학정보공시",
                "catalog_code": "0301",
                "dataset": "테스트",
                "access_tier": "public",
                "row_count": "4",
                "output_path": "panel.csv.gz",
                "output_sha256": checksum,
            }
            base_counts = audit.Counter({("2020", "A"): 2})
            result = audit.audit_panel(
                catalog_row,
                root,
                root,
                sample_target=100,
                base_key_counts=base_counts,
                base_years_by_id={"A": {"2020"}},
            )
            self.assertEqual(result["row_count"], 4)
            self.assertEqual(result["open_id_missing_count"], 1)
            self.assertEqual(result["school_year_base_duplicate_rows"], 1)
            self.assertEqual(result["orphan_row_count"], 1)
            self.assertEqual(result["join_expansion_extra_rows"], 2)
            self.assertEqual(result["source_row_hash_canonical_match_count"], 4)
            self.assertEqual(result["source_row_hash_not_reconstructable_count"], 0)
            self.assertEqual(result["row_id_mismatch_count"], 0)

    def test_upgrade_cache_does_not_treat_union_field_hash_difference_as_corruption(self):
        cached = {
            "audit_version": "1",
            "row_count": 10,
            "row_hash_mismatch_count": 7,
            "exact_original_row_duplicate_count": 0,
            "width_mismatch_count": 0,
            "row_id_mismatch_count": 0,
            "invalid_row_id_format_count": 0,
            "duplicate_row_id_count": 0,
            "missing_panel_year_count": 0,
            "invalid_panel_year_count": 0,
            "raw_year_mismatch_count": 0,
            "provenance_missing_counts": {},
            "open_id_missing_count": 0,
            "open_id_column_present": True,
            "orphan_row_count": 0,
            "school_year_base_duplicate_rows": 0,
            "join_expansion_extra_rows": 0,
            "open_id_normalization_collision_count": 0,
            "open_id_whitespace_count": 0,
            "grain_status": "school_year_open_id_unique",
            "severity": "critical",
            "status": "review_required",
        }
        result = audit.upgrade_cache(cached)
        self.assertEqual(result["source_row_hash_canonical_match_count"], 3)
        self.assertEqual(result["source_row_hash_not_reconstructable_count"], 7)
        self.assertEqual(result["severity"], "pass")
        self.assertEqual(result["status"], "pass")

    @staticmethod
    def make_row(year, open_id, department, source_row_number):
        raw = [year, open_id, department]
        row_hash = hashlib.sha256("\x1f".join(raw).encode("utf-8")).hexdigest()
        archive_sha = "a" * 64
        identity = f"1|{archive_sha}|member.csv|{source_row_number}"
        row_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return [
            "provider",
            "대학정보공시",
            "0301",
            "테스트",
            "1",
            "archive.zip",
            archive_sha,
            "member.csv",
            str(source_row_number),
            row_id,
            row_hash,
            year,
            *raw,
        ]


if __name__ == "__main__":
    unittest.main()
