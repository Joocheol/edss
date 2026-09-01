import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_edss_priority_school_history as validation


class ValidateEdssPrioritySchoolHistoryTests(unittest.TestCase):
    def test_priority_target_set_contains_six_school_year_ids(self):
        self.assertEqual(len(validation.TARGETS), 6)
        self.assertIn(("2015", "5831784427"), validation.TARGETS)

    def test_comparable_signature_ignores_only_open_id(self):
        left = {"조사년도": "2009", "개방ID": "111", "학과한글명": "세무학과", "값": "0"}
        right = {"조사년도": "2009", "개방ID": "222", "학과한글명": "세무학과", "값": "0"}
        changed = {**right, "값": "1"}
        self.assertEqual(validation.comparable_signature(left), validation.comparable_signature(right))
        self.assertNotEqual(validation.comparable_signature(left), validation.comparable_signature(changed))


if __name__ == "__main__":
    unittest.main()
