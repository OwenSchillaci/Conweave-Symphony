"""Check packet-level evidence from the documented Profile F shared-CE smoke run."""

import csv
import json
import os
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from run_profile_f import RING, STEPS

RESULTS = os.environ.get("PROFILE_F_SHARED_CE_RESULTS")


@unittest.skipUnless(RESULTS, "set PROFILE_F_SHARED_CE_RESULTS to the combined run directory")
class SharedCeSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(RESULTS)
        cls.summary = json.loads((cls.folder / "summary.json").read_text())
        cls.events = defaultdict(list)
        names = {
            "RING_COMPLETE", "PROFILE_F_CW_END", "PROFILE_F_VOQ",
            "SYMPHONY_DEQUEUE", "SYMPHONY_FEEDBACK", "SYMPHONY_CNP", "SYMPHONY_RATE",
        }
        with (cls.folder / "collective.log").open() as log:
            for line in log:
                name, _, rest = line.partition(" ")
                if name in names:
                    fields = dict(field.split("=", 1) for field in rest.split())
                    cls.events[name].append({
                        k: int(v) if v.lstrip("-").isdigit() else v for k, v in fields.items()
                    })

    def test_completion_order_and_wire_bytes(self):
        complete, = self.events["RING_COMPLETE"]
        self.assertEqual(complete["transfers"], len(RING) * STEPS)
        self.assertEqual(self.summary["receiver_ooo_notifications"], 0)
        self.assertEqual({r["switch"]: r["voqs"] for r in self.events["PROFILE_F_CW_END"]},
                         {12: 0, 13: 0})
        # The profile sends 1,000 payload bytes per full data packet, plus
        # 48 bytes of simulated headers per packet, including the short tail.
        chunk = self.summary["chunk"]
        expected_wire_bytes = chunk + ((chunk + 999) // 1000) * 48
        sent = Counter()
        with (self.folder / "flow_paths.csv").open() as path_file:
            for row in csv.DictReader(path_file):
                sent[int(row["flow_id"])] += int(row["wire_bytes"])
        self.assertEqual(sent, {flow: expected_wire_bytes for flow in range(len(RING) * STEPS)})

    def test_metadata_after_reroute_and_voq(self):
        progress = self.events["SYMPHONY_DEQUEUE"]
        releases = {r["uid"]: r["time_ns"] for r in self.events["PROFILE_F_VOQ"]}
        flushed = [r for r in progress if r["packet_uid"] in releases and
                   r["time_ns"] >= releases[r["packet_uid"]]]
        self.assertTrue(flushed, "need Symphony progress after a real VOQ release")
        with (self.folder / "forwarded_reroutes.csv").open() as route_file:
            reroutes = list(csv.DictReader(route_file))
        self.assertTrue(reroutes, "need a confirmed path change")
        # Port order in Profile F is host ports followed by the two spine ports.
        # Accept metadata observed on the selected spine after the path change.
        ports = {}
        with (self.folder / "ports.csv").open() as port_file:
            for row in csv.DictReader(port_file):
                ports[int(row["node"]), int(row["port"])] = int(row["peer"])
        rerouted = {(int(r["flow_id"]), ports[int(r["switch"]), int(r["new"]) & 255]):
                    int(r["first_new_tx_ns"]) for r in reversed(reroutes)}
        observed = [r for r in progress if (r["flow_id"], r["switch"]) in rerouted and
                    r["time_ns"] >= rerouted[r["flow_id"], r["switch"]]]
        self.assertTrue(observed, "need Symphony progress on the new spine")
        for r in flushed + observed:
            self.assertEqual((r["collective_id"], r["step"]), (0, r["flow_id"] % STEPS))
            self.assertEqual(r["psn"], r["seq_bytes"] // 1000)

    def test_each_ce_cause_reaches_dcqcn(self):
        feedback = {r["packet_uid"]: r for r in self.events["SYMPHONY_FEEDBACK"]}
        cnps = {r["ack_uid"]: r for r in self.events["SYMPHONY_CNP"]}
        cuts = defaultdict(list)
        for r in self.events["SYMPHONY_RATE"]:
            if r["after_bps"] < r["before_bps"]:
                cuts[r["flow_id"]].append(r["time_ns"])
        marks_by_packet = defaultdict(list)
        for mark in self.events["SYMPHONY_DEQUEUE"]:
            if mark["mark"] != "none":
                marks_by_packet[mark["packet_uid"]].append(mark)
        causes = Counter()
        for uid, marks in marks_by_packet.items():
            labels = {mark["mark"] for mark in marks}
            if len(labels) != 1 or "both" in labels:
                continue
            reply = feedback.get(uid)
            if reply is None:
                continue
            cnp = cnps.get(reply["ack_uid"])
            if cnp is None or cnp["finished"]:
                continue
            for mark in marks:
                self.assertEqual(mark["flow_id"], reply["flow_id"])
                self.assertLessEqual(mark["time_ns"], reply["time_ns"])
            self.assertEqual(reply["flow_id"], cnp["flow_id"])
            self.assertLessEqual(reply["time_ns"], cnp["time_ns"])
            self.assertEqual(reply["cnp"], 1)
            # The first decrease check adds 1 ns to the 4 us period.
            if any(0 <= t - cnp["time_ns"] <= 4001 for t in cuts[cnp["flow_id"]]):
                causes.update(labels)
        self.assertGreater(causes["queue"], 0, "need queue CE followed by an active QP rate cut")
        self.assertGreater(causes["symphony"], 0, "need Symphony CE followed by an active QP rate cut")


if __name__ == "__main__":
    unittest.main()
