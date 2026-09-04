import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import infer_edss_graduate_open_id_candidates as inference


class GraduateOpenIdInferenceTests(unittest.TestCase):
    def test_rank_candidates_uses_kind_region_and_branch(self):
        good = inference.ProfileKey("2024", "일반대학원", "0001")
        wrong_region = inference.ProfileKey("2024", "일반대학원", "0002")
        wrong_kind = inference.ProfileKey("2024", "전문대학원", "0003")
        profiles = {
            key: inference.CandidateProfile(
                {"간호학과", "보건학과", "통계학과"},
                set(),
                {"간호학과", "보건학과", "통계학과"},
                set(),
            )
            for key in (good, wrong_region, wrong_kind)
        }
        bridge = {
            ("2024", "0001"): {
                "provinces": {"서울"},
                "branches": {"본교"},
                "school_types": {"대학원"},
            },
            ("2024", "0002"): {
                "provinces": {"부산"},
                "branches": {"본교"},
                "school_types": {"대학원"},
            },
            ("2024", "0003"): {
                "provinces": {"서울"},
                "branches": {"본교"},
                "school_types": {"대학원"},
            },
        }
        ranked = inference.rank_candidates(
            "2024",
            "일반대학원",
            "서울",
            "본교",
            {"간호학과", "보건학과", "통계학과"},
            None,
            profiles,
            bridge,
        )
        self.assertEqual([row["open_id"] for row in ranked], ["0001"])
        self.assertTrue(ranked[0]["exact"])

    def test_cross_year_conflicting_selected_ids_are_excluded(self):
        rows = [
            {
                "_panel_year": "2023",
                "edss_school_kind": "일반대학원",
                "cross_year_group_key": "same",
                "top_candidate_open_id": "A",
                "candidate_open_id": "A",
                "confidence_tier": "high",
                "resolution_status": "candidate_high_exact_context",
                "score": "1.000000",
                "cross_year_consistent": "not_tested",
                "year_conflict_excluded": "false",
            },
            {
                "_panel_year": "2024",
                "edss_school_kind": "일반대학원",
                "cross_year_group_key": "same",
                "top_candidate_open_id": "B",
                "candidate_open_id": "B",
                "confidence_tier": "high",
                "resolution_status": "candidate_high_exact_context",
                "score": "1.000000",
                "cross_year_consistent": "not_tested",
                "year_conflict_excluded": "false",
            },
        ]
        inference.apply_cross_year_rules(rows, {})
        self.assertEqual([row["candidate_open_id"] for row in rows], ["", ""])
        self.assertTrue(all(row["year_conflict_excluded"] == "true" for row in rows))

    def test_normalization_preserves_code_digits_and_harmonizes_province(self):
        self.assertEqual(inference.normalize_text(" 00-01 "), "0001")
        self.assertEqual(inference.normalize_province("경상남도"), "경남")

    def test_reverse_collision_is_excluded(self):
        rows = [
            {
                "_panel_year": "2024",
                "candidate_open_id": "A",
                "cross_year_group_key": group,
                "confidence_tier": "strong",
                "resolution_status": "candidate_strong_calibrated_multisource",
                "reverse_collision_excluded": "false",
            }
            for group in ("school-1", "school-2")
        ]
        inference.apply_global_consistency_rules(rows)
        self.assertEqual([row["candidate_open_id"] for row in rows], ["", ""])
        self.assertTrue(all(row["reverse_collision_excluded"] == "true" for row in rows))


if __name__ == "__main__":
    unittest.main()
