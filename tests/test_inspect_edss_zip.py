import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inspect_edss_zip


class InspectEdssZipTests(unittest.TestCase):
    def test_recovers_cp949_zip_member_name(self):
        original = "0101. 고등교육학교개황(09).csv"
        mojibake = original.encode("cp949").decode("cp437")
        self.assertEqual(inspect_edss_zip.recover_name(mojibake), original)

    def test_detects_cp949_csv(self):
        text, encoding = inspect_edss_zip.decode_csv("조사년도,학교명\n2009,연세대학교\n".encode("cp949"))
        self.assertEqual(encoding, "cp949")
        self.assertIn("연세대학교", text)


if __name__ == "__main__":
    unittest.main()
