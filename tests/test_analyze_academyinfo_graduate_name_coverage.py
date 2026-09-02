import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_academyinfo_graduate_name_coverage as analysis


class AcademyInfoGraduateNameCoverageTests(unittest.TestCase):
    def test_parenthetical_campus_suffix_matches_unique_school(self):
        api = [
            {
                "svyYr": "2025",
                "schlNm": "가야대학교 보건대학원(김해)",
                "schlKndNm": "특수대학원",
                "mjrAreaNm": "경상남도",
                "korMjrNm": "간호학전공",
            }
        ]
        identities = [
            {
                "_panel_year": "2023",
                "_school_identity_key": "school-1",
                "학교명": "가야대학교 보건대학원",
                "학교종류명": "특수대학원",
                "시도명": "경남",
                "source_row_count": "1",
                "department_count": "1",
            }
        ]
        employment = [
            {
                "_school_identity_key": "school-1",
                "학교종류명": "특수대학원",
                "학과명": "간호학전공",
            }
        ]
        row = analysis.analyze(api, identities, employment)[0]
        self.assertEqual(row["match_status"], "candidate_unique_name_context")
        self.assertEqual(row["name_match_method"], "terminal_parenthetical_normalized_name_kind_region")
        self.assertEqual(row["department_jaccard"], "1.000000")
        self.assertEqual(row["candidate_open_id"], "")

    def test_school_kind_prevents_false_match(self):
        api = [
            {
                "svyYr": "2025",
                "schlNm": "같은대학교 대학원",
                "schlKndNm": "일반대학원",
                "mjrAreaNm": "서울특별시",
                "korMjrNm": "행정학과",
            }
        ]
        identities = [
            {
                "_panel_year": "2024",
                "_school_identity_key": "school-2",
                "학교명": "같은대학교 대학원",
                "학교종류명": "특수대학원",
                "시도명": "서울",
                "source_row_count": "1",
                "department_count": "1",
            }
        ]
        row = analysis.analyze(api, identities, [])[0]
        self.assertEqual(row["match_status"], "unmatched_name_context")


if __name__ == "__main__":
    unittest.main()
