"""Regression tests for Profile F's topology, offer matching, and measurements."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from run_profile_f import (CONFIG, RING, STEPS, background_text, configure,
                           step_timeline, validate_transfers, verify_pins)


class ProfileFTests(unittest.TestCase):
    def test_topology_and_pins(self):
        nodes, switches, link_count = (CONFIG / "profile_f_topology.txt").read_text().splitlines()[0].split()
        self.assertEqual((nodes, switches), ("16", "4"))
        rows = [line.split() for line in (CONFIG / "profile_f_topology.txt").read_text().splitlines()[2:]]
        self.assertEqual(len(rows), int(link_count))
        self.assertTrue(all(r[2:] == ["10Gbps", "1000ns", "0"] for r in rows))
        links = {tuple(map(int, r[:2])) for r in rows}
        self.assertEqual({host: [b for a, b in links if a == host] for host in RING},
                         {host: [12 if host < 4 else 13] for host in RING})
        self.assertTrue(all((a < 4) != (b < 4) for a, b in zip(RING, RING[1:] + RING[:1])))
        self.assertTrue({(tor, spine) for tor in (12, 13) for spine in (14, 15)} <= links)
        for row in (CONFIG / "profile_f_hotspot.txt").read_text().splitlines():
            src, tor, spine, peer, dst, *_ = map(int, row.split())
            self.assertNotIn(src, RING)
            self.assertNotIn(dst, RING)
            self.assertTrue({(src, tor), (tor, spine), (peer, spine), (dst, peer)} <= links)

    def test_treatments_change_only_routing_and_sym_marking(self):
        args = SimpleNamespace(seed=1, chunk=524288, skew_ns=250000, skew_rank=1,
                               reply_extra_us=40, flush_extra_us=16, path_pause_us=80,
                               expiry_us=1000, default_voq_us=500,
                               hotspot_manifest=CONFIG / "profile_f_hotspot.txt",
                               background_bytes=None, background_start_ns=None, mark_provenance=False,
                               pilot_detail=False)
        with TemporaryDirectory() as tmp:
            configs = []
            for treatment, flags in (
                    ("dcqcn", ("0", "0")), ("symphony", ("0", "2")),
                    ("conweave", ("9", "0")), ("conweave-symphony", ("9", "2")),
                    ("conweave-symphony-separated", ("9", "2"))):
                text, delay = configure(args, "both", treatment, Path(tmp))
                self.assertEqual(delay, [0, 250000] + [0] * 6)
                cfg = dict(line.split(None, 1) for line in text.splitlines())
                self.assertEqual((cfg.pop("LB_MODE"), cfg.pop("SYMPHONY_MODE")), flags)
                expected_gate = "1" if treatment == "conweave-symphony-separated" else None
                self.assertEqual(cfg.pop("PROFILE_F_QUEUE_ONLY_NOTIFY", None), expected_gate)
                configs.append(cfg)
            self.assertTrue(all(cfg == configs[0] for cfg in configs))
        self.assertEqual(background_text(args, "hotspot"), background_text(args, "both"))
        self.assertEqual(background_text(args, "skew"), "")
        self.assertEqual(background_text(args, "clean"), "")

    def test_dependency_validation_and_half_open_overlap(self):
        transfers = {}
        for step in range(STEPS):
            for rank, host in enumerate(RING):
                transfers[rank, step] = dict(start_ns=10 + step * 5,
                                             completion_ns=15 + step * 5, src=host,
                                             dst=RING[(rank + 1) % len(RING)],
                                             sport=10000 + step, dport=100 + step,
                                             bytes=524288)
        complete = dict(start_ns=10, completion_ns=80)
        self.assertEqual(len(validate_transfers(transfers, complete, [0] * 8, 524288, 10)), 104)
        self.assertEqual(max(active for _, active in step_timeline(transfers)), 1)
        transfers[1, 1]["start_ns"] += 1
        with self.assertRaisesRegex(ValueError, "dependency"):
            validate_transfers(transfers, complete, [0] * 8, 524288, 10)

    def test_pin_validation_rejects_migration_and_incomplete_path(self):
        manifest = [[8, 12, 14, 13, 10, 20000, 200, 2048, 9950000]]
        hops = ((12, 14), (14, 13), (13, 10))
        rows = [dict(node=a, peer=b, background_bytes=2200, background_feedback_bytes=0)
                for a, b in hops]
        rows += [dict(node=b, peer=a, background_bytes=0, background_feedback_bytes=100)
                 for a, b in ((8, 12), (12, 14), (14, 13))]
        verify_pins(rows, manifest)
        with self.assertRaises(ValueError):
            verify_pins(rows[:-1], manifest)
        rows[0]["peer"] = 15
        with self.assertRaises(ValueError):
            verify_pins(rows, manifest)


if __name__ == "__main__":
    unittest.main()
