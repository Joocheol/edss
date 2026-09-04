import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import match_edss_employment_2022_2023_school_counts as matcher


class DepartmentCountMatcherTests(unittest.TestCase):
    def test_rank_filters_to_count_tolerance_and_prefers_overlap(self):
        profiles = {
            "A": {"a", "b", "c"},
            "B": {"a", "x", "y", "z"},
            "C": {"a", "b", "c", "d", "e", "f"},
        }
        ranked = matcher.rank_candidate_pairs({"a", "b", "c", "d"}, profiles, profiles, tolerance=2)
        self.assertEqual([row["candidate_open_id"] for row in ranked], ["C", "A", "B"])
        self.assertEqual(ranked[0]["department_overlap_count"], 4)

    def test_exact_set_is_review_only_unique_exact(self):
        profiles = {"A": {"a", "b", "c"}, "B": {"a", "b"}}
        pairs = matcher.rank_candidate_pairs({"a", "b", "c"}, profiles, profiles, tolerance=2)
        self.assertEqual(
            matcher.preliminary_status(pairs, {"a", "b", "c"}, profiles),
            "unique_exact_department_set",
        )

    def test_equal_top_scores_are_ambiguous(self):
        profiles = {"A": {"a", "b"}, "B": {"a", "c"}}
        pairs = matcher.rank_candidate_pairs({"a", "x"}, profiles, profiles, tolerance=0)
        self.assertEqual(
            matcher.preliminary_status(pairs, {"a", "x"}, profiles),
            "ambiguous_top_score_tie",
        )

    def test_normalization_ignores_spacing_and_width(self):
        self.assertEqual(matcher.normalize_text("Ａ 학 과"), "a학과")


if __name__ == "__main__":
    unittest.main()
