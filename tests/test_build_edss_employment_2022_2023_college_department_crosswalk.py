import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_edss_employment_2022_2023_college_department_crosswalk as matcher


def profile(college, departments):
    return {
        "departments": set(departments),
        "pairs": {(college, department) for department in departments},
    }


class CollegeDepartmentCrosswalkTests(unittest.TestCase):
    def test_pair_signature_disambiguates_identical_department_sets(self):
        profiles_2022 = {
            "A": profile("college-a", {"x", "y", "z"}),
            "B": profile("college-b", {"x", "y", "z"}),
        }
        ranked = matcher.rank_open_ids(
            profile("college-b", {"x", "y", "z"}), profiles_2022, profiles_2022, tolerance=0
        )
        self.assertEqual([row["candidate_open_id"] for row in ranked], ["B", "A"])
        self.assertEqual(ranked[0]["pair_jaccard"], 1.0)
        self.assertEqual(ranked[1]["pair_jaccard"], 0.0)

    def test_small_exact_pair_signature_is_review_only(self):
        metrics = matcher.combined_metrics(profile("c", {"x", "y"}), profile("c", {"x", "y"}))
        self.assertEqual(matcher.pair_evidence_class(metrics, exact_pairs=True), "small_exact")

    def test_previous_match_comparison_states(self):
        self.assertEqual(
            matcher.compare_outcome("A", "A", True),
            "previous_match_confirmed_by_college_department",
        )
        self.assertEqual(
            matcher.compare_outcome("A", "B", True),
            "previous_match_changed_by_college_department",
        )
        self.assertEqual(
            matcher.compare_outcome("A", "A", False),
            "previous_match_downgraded_by_college_department",
        )
        self.assertEqual(
            matcher.compare_outcome("", "B", True),
            "new_match_from_college_department_disambiguation",
        )

    def test_unique_reciprocal_pair_match_is_accepted(self):
        profiles_2022 = {
            "A": profile("college-a", {"x", "y", "z"}),
            "B": profile("college-b", {"m", "n", "o"}),
        }
        profiles_2023 = {
            ("서울", "가대학교", "대학"): {
                **profile("college-a", {"x", "y", "z"}),
                "branches": {"본교"},
                "school_kinds": {"대학"},
            },
            ("서울", "나대학교", "대학"): {
                **profile("college-b", {"m", "n", "o"}),
                "branches": {"본교"},
                "school_kinds": {"대학"},
            },
        }
        bridge = {
            "A": {"_0101_provinces": "서울", "_0101_school_types": "대학"},
            "B": {"_0101_provinces": "서울", "_0101_school_types": "대학"},
        }
        review, accepted, summary = matcher.build_matches(
            profiles_2022, profiles_2023, bridge, {}, {}, tolerance=2
        )
        self.assertEqual(len(review), 2)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(
            summary["status_counts"],
            {"accepted_reciprocal_exact_college_department_set": 2},
        )


if __name__ == "__main__":
    unittest.main()
