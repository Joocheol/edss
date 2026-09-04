import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_pre2025_unmatched_openids.py"
SPEC = importlib.util.spec_from_file_location("review_pre2025", SCRIPT)
review = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(review)


class ApprovedIdentityProposalTests(unittest.TestCase):
    def test_project_proposals_promote_all_thirty_without_auto_merge(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "data/metadata/edss_remaining_unnamed_openid_identity_proposals.csv"
        )
        overrides = review.load_approved_identity_proposals(path)

        self.assertEqual(len(overrides), 30)
        self.assertEqual(overrides["7579953312"]["name"], "인천가톨릭대학교 조형대학원")
        self.assertEqual(
            overrides["7579953312"]["years"],
            "2009|2010|2011|2012|2013|2014|2015|2016|2017",
        )
        self.assertTrue(
            all(
                "separate" in row["safe_action"] or "no_auto_join" in row["safe_action"]
                for row in overrides.values()
            )
        )

    def test_rejects_unapproved_or_unsafe_proposal(self):
        fields = [
            "open_id",
            "proposed_entity_name",
            "decision_bucket",
            "proposed_manual_classification",
            "evidence_window",
            "kedi_match_evidence",
            "safe_join_action",
            "notes",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "proposals.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "open_id": "1234",
                        "proposed_entity_name": "테스트대학원",
                        "decision_bucket": "needs_user_judgment",
                        "proposed_manual_classification": "candidate",
                        "evidence_window": "2020-2021",
                        "kedi_match_evidence": "검토 필요",
                        "safe_join_action": "merge",
                        "notes": "",
                    }
                )
            with self.assertRaisesRegex(RuntimeError, "not approved"):
                review.load_approved_identity_proposals(path)

    def test_rejects_reversed_year_window(self):
        with self.assertRaisesRegex(RuntimeError, "reversed"):
            review.expand_year_window("2025-2024")


class FinalIdentityDecisionTests(unittest.TestCase):
    def test_final_sixteen_are_confirmed_without_auto_merge(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "data/metadata/edss_remaining_pre2025_openid_identity_decisions.csv"
        )
        overrides = review.load_final_identity_decisions(path)

        self.assertEqual(set(overrides), review.FINAL_REVIEW_IDS)
        self.assertEqual(len(overrides), 16)
        self.assertEqual(
            overrides["1139362752"]["name"], "한양대학교 도시융합개발대학원"
        )
        self.assertNotEqual(overrides["1139362752"]["name"], "한양대학교도시대학원")
        self.assertTrue(
            all(
                row["candidate_status"] == "confirmed_final_manual_review_identity"
                for row in overrides.values()
            )
        )
        self.assertTrue(
            all(
                "separate" in row["safe_action"] or "no_auto_join" in row["safe_action"]
                for row in overrides.values()
            )
        )

    def test_rejects_unconfirmed_final_decision(self):
        fields = [
            "open_id",
            "confirmed_entity_name",
            "decision_bucket",
            "manual_classification",
            "evidence_window",
            "evidence_tier",
            "kedi_statuses",
            "official_source_url",
            "safe_join_action",
            "evidence_summary",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "decisions.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "open_id": "1234",
                        "confirmed_entity_name": "테스트대학원",
                        "decision_bucket": "ready_to_record",
                        "manual_classification": "candidate",
                        "evidence_window": "2020",
                        "evidence_tier": "test",
                        "kedi_statuses": "기존",
                        "official_source_url": "",
                        "safe_join_action": "retain_open_id_separately_no_auto_join",
                        "evidence_summary": "검토 근거",
                    }
                )
            with self.assertRaisesRegex(RuntimeError, "unconfirmed final classification"):
                review.load_final_identity_decisions(path)


if __name__ == "__main__":
    unittest.main()
