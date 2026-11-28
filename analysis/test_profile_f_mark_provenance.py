"""Regression checks for a matched traced and untraced Profile F smoke run."""

import csv
import os
import unittest
from collections import defaultdict
from pathlib import Path

TRACED = os.environ.get("PROFILE_F_PROVENANCE_RESULTS")
CONTROL = os.environ.get("PROFILE_F_PROVENANCE_CONTROL")


@unittest.skipUnless(TRACED and CONTROL, "set PROFILE_F_PROVENANCE_RESULTS and PROFILE_F_PROVENANCE_CONTROL")
class MarkProvenanceSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traced = Path(TRACED)
        cls.control = Path(CONTROL)
        cls.events = defaultdict(list)
        with (cls.traced / "collective.log").open() as log:
            for line in log:
                name, _, body = line.partition(" ")
                if name.startswith(("CW_PROV_", "ECN_PROV_")):
                    cls.events[name].append(dict(field.split("=", 1) for field in body.split()))

    def test_observation_preserves_simulated_behavior(self):
        # Compare existing event timing, packet IDs, random draws, and control/data events.
        def original_events(folder):
            with (folder / "collective.log").open() as log:
                return [line for line in log if line.startswith(
                    ("RING_", "SYMPHONY_", "PROFILE_F_", "FIGURE4_FLOW_SAMPLE "))]
        self.assertEqual(original_events(self.traced), original_events(self.control))
        for name in ("summary.json", "fct.txt", "ports.csv", "rates.csv",
                     "transfers.csv", "dependency_waits.csv", "flow_paths.csv",
                     "notifications.csv", "path_exclusions.csv", "path_updates.csv",
                     "forwarded_reroutes.csv", "voq_residence.csv", "cnp.txt", "pfc.txt"):
            self.assertEqual((self.traced / name).read_bytes(), (self.control / name).read_bytes(),
                             name)

    def test_marks_reach_conweave_with_original_ce(self):
        marks = defaultdict(list)
        for event in self.events["ECN_PROV_MARK"]:
            marks[event["data_uid"]].append(event)
        cumulative = {}
        for uid, events in marks.items():
            causes = 0
            for mark in events:
                causes |= int(mark["local"])
                self.assertEqual(int(mark["causes"]), causes)
                cumulative[uid, int(mark["time_ns"])] = causes

        eligible = {}
        seen_causes = set()
        for rx in self.events["CW_PROV_RX"]:
            uid = rx["data_uid"]
            received = int(rx["time_ns"])
            upstream = [int(m["time_ns"]) for m in marks[uid] if int(m["time_ns"]) < received]
            expected = cumulative[uid, max(upstream)] if upstream else 0
            self.assertEqual(int(rx["causes"]), expected)
            self.assertEqual(int(rx["ecn"]), 3 if expected else 0)
            seen_causes.add(expected)
            if expected and rx["old_epoch"] == "0":
                eligible[uid] = (rx["path"], rx["causes"], rx["switch"])
        self.assertTrue({0, 1, 2} <= seen_causes)
        actual = {n["data_uid"]: (n["path"], n["causes"], n["switch"])
                  for n in self.events["CW_PROV_NOTIFY"]}
        self.assertEqual(actual, eligible)

    def test_follow_symphony_only_notify_to_transmitted_path(self):
        sent = {r["notify_uid"]: r for r in self.events["CW_PROV_NOTIFY"]}
        received = {r["notify_uid"]: r for r in self.events["CW_PROV_EXCLUDE"]}
        for uid, exclude in received.items():
            self.assertEqual(exclude["path"], sent[uid]["path"])
            self.assertGreaterEqual(int(exclude["time_ns"]), int(sent[uid]["time_ns"]))
        self.assertEqual(len(received), len(sent))

        decisions = {r["decision_uid"]: r for r in self.events["CW_PROV_PATH"]}
        transmitted = {(r["switch"], r["flow_id"], r["epoch"], r["phase"], r["port"]): r
                       for r in self.events["CW_PROV_TX"]}
        forwarded_count = 0
        for path in decisions.values():
            key = tuple(path[k] for k in ("switch", "flow_id", "epoch", "phase", "new_port"))
            if path["changed"] == "1" and key in transmitted:
                tx = transmitted[key]
                self.assertEqual(tx["path"], path["new"])
                self.assertGreaterEqual(int(tx["time_ns"]), int(path["time_ns"]))
                forwarded_count += 1
        with (self.traced / "forwarded_reroutes.csv").open() as file:
            self.assertEqual(forwarded_count, len(list(csv.DictReader(file))))

        chains = 0
        for candidate in self.events["CW_PROV_CANDIDATES"]:
            path = decisions[candidate["decision_uid"]]
            for slot in ("first", "second"):
                uid = candidate[f"{slot}_notify_uid"]
                if candidate[f"{slot}_usable"] == "1":
                    self.assertEqual(uid, "none")
                else:
                    exclude = received[uid]
                    self.assertEqual((exclude["switch"], exclude["path"]),
                                     (candidate["switch"], candidate[slot]))
                    self.assertLessEqual(int(exclude["time_ns"]), int(candidate["time_ns"]))
                    self.assertLess(int(candidate["time_ns"]), int(exclude["until_ns"]))
            uid = candidate["first_notify_uid"]
            key = tuple(path[k] for k in ("switch", "flow_id", "epoch", "phase", "new_port"))
            if (uid != "none" and sent[uid]["causes"] == "2" and
                    candidate["second_usable"] == "1" and path["changed"] == "1" and
                    key in transmitted):
                self.assertEqual(path["new"], candidate["second"])
                chains += 1
        self.assertGreater(chains, 0, "smoke run should contain a Symphony-only feedback chain")


if __name__ == "__main__":
    unittest.main()
