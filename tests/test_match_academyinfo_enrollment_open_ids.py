import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import match_academyinfo_enrollment_open_ids as matcher


class AcademyInfoEnrollmentMatchTests(unittest.TestCase):
    def test_region_group_is_limited_to_gwangju_and_jeonnam(self):
        self.assertEqual(matcher.compatible_provinces("전남광주"), {"전남", "광주"})

    def test_two_year_same_open_id_is_required(self):
        public = {
            ("0001", "2023"): {
                "svyYr": "2023",
                "indctVal1": "100",
                "schlDivNm": "대학",
                "znNm": "서울",
                "clgcpDivNm": "본교",
            },
            ("0001", "2024"): {
                "svyYr": "2024",
                "indctVal1": "101",
                "schlDivNm": "대학",
                "znNm": "서울",
                "clgcpDivNm": "본교",
            },
        }
        index = {
            ("2023", "대학", "서울", "본교", "100"): {"A"},
            ("2024", "대학", "서울", "본교", "101"): {"B"},
        }
        result = matcher.public_resolution("0001", public, index)
        self.assertEqual(result["resolution_status"], "conflict_two_year_exact_open_id_changed")
        self.assertEqual(result["candidate_open_id"], "")

    def test_two_year_unique_same_open_id_becomes_candidate(self):
        public = {
            ("0001", "2023"): {
                "svyYr": "2023",
                "indctVal1": "100",
                "schlDivNm": "대학",
                "znNm": "서울",
                "clgcpDivNm": "본교",
            },
            ("0001", "2024"): {
                "svyYr": "2024",
                "indctVal1": "101",
                "schlDivNm": "대학",
                "znNm": "서울",
                "clgcpDivNm": "본교",
            },
        }
        index = {
            ("2023", "대학", "서울", "본교", "100"): {"A"},
            ("2024", "대학", "서울", "본교", "101"): {"A"},
        }
        result = matcher.public_resolution("0001", public, index)
        self.assertEqual(result["resolution_status"], "candidate_two_year_exact_enrollment")
        self.assertEqual(result["candidate_open_id"], "A")

    def test_name_aliases_do_not_remove_branch_context(self):
        self.assertEqual(matcher.canonical_school_name("한양대학교(ERICA)"), matcher.canonical_school_name("한양대학교"))
        self.assertEqual(matcher.normalize_branch("분교"), "분교")
        self.assertNotEqual(matcher.normalize_branch("분교"), matcher.normalize_branch("본교"))

    def test_public_school_kind_maps_to_employment_kind(self):
        self.assertEqual(matcher.expected_employment_school_kind({"schlKndNm": "교육대학"}), "교육대학")
        self.assertEqual(matcher.expected_employment_school_kind({"schlDivNm": "전문대학"}), "전문대학")
        self.assertEqual(matcher.expected_employment_school_kind({"schlDivNm": "대학"}), "대학")

    def test_reverse_duplicate_open_id_is_rejected(self):
        rows = [
            {
                "academyinfo_school_id": "0001",
                "candidate_open_id": "A",
                "candidate_method": "method",
                "resolution_status": "candidate_two_year_exact_enrollment",
            },
            {
                "academyinfo_school_id": "0002",
                "candidate_open_id": "A",
                "candidate_method": "method",
                "resolution_status": "candidate_two_year_exact_enrollment",
            },
        ]
        self.assertEqual(matcher.enforce_reverse_unique(rows), 1)
        self.assertTrue(all(row["candidate_open_id"] == "" for row in rows))
        self.assertTrue(all(row["resolution_status"] == "conflict_reverse_open_id_not_unique" for row in rows))

    def test_multirow_open_id_keeps_context_specific_enrollment(self):
        fields = [
            "_panel_year",
            "개방ID",
            "학교구분명",
            "시도명",
            "본분교명",
            "고등교육학교_재적학생수",
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "0101.csv.gz"
            with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerow(
                    {
                        "_panel_year": "2023",
                        "개방ID": "A",
                        "학교구분명": "대학",
                        "시도명": "경남",
                        "본분교명": "본교",
                        "고등교육학교_재적학생수": "2153",
                    }
                )
                writer.writerow(
                    {
                        "_panel_year": "2023",
                        "개방ID": "A",
                        "학교구분명": "대학",
                        "시도명": "경북",
                        "본분교명": "제2캠퍼스",
                        "고등교육학교_재적학생수": "0",
                    }
                )
            index = matcher.build_edss_index(path)
            self.assertEqual(index[("2023", "대학", "경남", "본교", "2153")], {"A"})
            self.assertEqual(index[("2023", "대학", "경북", "분교", "0")], {"A"})


if __name__ == "__main__":
    unittest.main()
