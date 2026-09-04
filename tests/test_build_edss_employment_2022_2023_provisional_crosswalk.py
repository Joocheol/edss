import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_edss_employment_2022_2023_provisional_crosswalk as crosswalk


class ProvisionalCrosswalkTests(unittest.TestCase):
    def test_comparable_profiles_keep_undergraduate_and_general_graduate_only(self):
        profiles = {
            ("서울", "가대학교", "대학"): {
                "departments": {"a"}, "branches": {"본교"}, "school_kinds": {"대학"}
            },
            ("서울", "가대학교 일반대학원", "대학원"): {
                "departments": {"a"}, "branches": {"본교"}, "school_kinds": {"일반대학원"}
            },
            ("서울", "가대학교 교육대학원", "대학원"): {
                "departments": {"a"}, "branches": {"본교"}, "school_kinds": {"특수대학원"}
            },
        }
        selected = crosswalk.select_comparable_2023_profiles(profiles)
        self.assertEqual(len(selected), 2)
        self.assertNotIn(("서울", "가대학교 교육대학원", "대학원"), selected)

    def test_small_exact_signature_is_not_accepted(self):
        pair = {
            "department_overlap_count": 2,
            "department_jaccard": 1.0,
            "smaller_set_coverage": 1.0,
        }
        self.assertEqual(crosswalk.evidence_class(pair, exact_set=True), "small_exact")

    def test_unique_reciprocal_exact_match_is_accepted(self):
        profiles_2022 = {"A": {"a", "b", "c"}, "B": {"x", "y", "z"}}
        profiles_2023 = {
            ("서울", "가대학교", "대학"): {
                "departments": {"a", "b", "c"},
                "branches": {"본교"},
                "school_kinds": {"대학"},
            },
            ("서울", "나대학교", "대학"): {
                "departments": {"x", "y", "z"},
                "branches": {"본교"},
                "school_kinds": {"대학"},
            },
        }
        bridge = {
            "A": {"_0101_provinces": "서울", "_0101_school_types": "대학"},
            "B": {"_0101_provinces": "서울", "_0101_school_types": "대학"},
        }
        review, accepted, _regions, summary = crosswalk.build_reciprocal_matches(
            profiles_2022, profiles_2023, bridge, {}, tolerance=2
        )
        self.assertEqual(len(review), 2)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(summary["status_counts"], {"accepted_reciprocal_exact": 2})

    def test_parent_resolution_handles_campus_alias_and_standalone_school(self):
        profiles = {
            ("서울", "명지대학교 인문캠퍼스", "대학"): {
                "departments": {"a"}, "branches": {"본교"}, "school_kinds": {"대학"}
            }
        }
        aliases = crosswalk.undergrad_aliases(profiles)
        self.assertEqual(
            crosswalk.resolve_parent_school("명지대학교 교육대학원", aliases),
            ("명지대학교", "longest_undergraduate_name_prefix"),
        )
        self.assertEqual(
            crosswalk.resolve_parent_school("개신대학원대학교", aliases),
            ("개신대학원대학교", "standalone_graduate_university"),
        )


if __name__ == "__main__":
    unittest.main()
