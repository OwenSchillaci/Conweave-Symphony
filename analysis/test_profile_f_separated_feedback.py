"""Packet-level validation of the separated-feedback Profile F matrix."""

import os
import unittest
from pathlib import Path

from analyze_profile_f_pilot import records, same_behavior
from run_profile_f import SCENARIOS

RESULTS = os.environ.get("PROFILE_F_SEPARATED_RESULTS")
UNTRACED = os.environ.get("PROFILE_F_SEPARATED_UNTRACED")
TREATMENT = "conweave-symphony-separated"


@unittest.skipUnless(RESULTS, "set PROFILE_F_SEPARATED_RESULTS to the five-treatment matrix")
class SeparatedFeedbackTest(unittest.TestCase):
    def test_only_queue_ce_generates_notify_in_each_scenario(self):
        names = {"CW_PROV_RX", "CW_PROV_NOTIFY", "CW_PROV_EXCLUDE",
                 "SYMPHONY_FEEDBACK", "SYMPHONY_CNP"}
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                folder = Path(RESULTS) / scenario / TREATMENT
                observed = {}
                sent = {}
                applied = set()
                feedback = {}
                cnps = set()
                for kind, row in records(folder / "collective.log", names):
                    if kind == "CW_PROV_RX":
                        observed[row["data_uid"]] = row
                    elif kind == "CW_PROV_NOTIFY":
                        sent[row["data_uid"]] = row
                    elif kind == "CW_PROV_EXCLUDE":
                        applied.add(row["notify_uid"])
                    elif kind == "SYMPHONY_FEEDBACK":
                        feedback[row["packet_uid"]] = row["ack_uid"]
                    elif not row["finished"]:
                        cnps.add(row["ack_uid"])

                expected = {uid for uid, r in observed.items()
                            if r["ecn"] == 3 and r["causes"] & 1 and not r["old_epoch"]}
                self.assertTrue(expected)
                self.assertEqual(set(sent), expected)
                for uid, notify in sent.items():
                    self.assertEqual(notify["causes"], observed[uid]["causes"])
                self.assertEqual(applied, {r["notify_uid"] for r in sent.values()})
                for cause in (1, 2):
                    self.assertTrue(any(r["ecn"] == 3 and r["causes"] == cause and
                                        feedback.get(uid) in cnps
                                        for uid, r in observed.items()),
                                    f"cause {cause} must still reach an active DCQCN sender")

    @unittest.skipUnless(UNTRACED, "set PROFILE_F_SEPARATED_UNTRACED to the untraced 'both' run")
    def test_policy_works_without_trace_logging(self):
        same_behavior(Path(RESULTS) / "both" / TREATMENT, Path(UNTRACED))


if __name__ == "__main__":
    unittest.main()
