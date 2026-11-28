import unittest
from collections import Counter

from figure4_workloads import analyze, manifest_text, rack, topology, workload


class Figure4WorkloadTest(unittest.TestCase):
    def test_one_factor_controls(self):
        chunk = 256 * 1024
        input_a, a = workload("A", chunk)
        input_b, b = workload("B", chunk)
        input_c, c = workload("C", chunk)
        self.assertEqual(len(a), 7936)
        self.assertEqual(a, workload("E", chunk)[1])
        self.assertEqual(input_a, input_b)
        self.assertEqual(input_c * 4, input_a)
        self.assertEqual(sum(x.size for x in a), sum(x.size for x in b))
        self.assertEqual({(x.src, x.rail) for x in a}, {(x.src, x.rail) for x in b})
        self.assertEqual(sum(x.size for x in b if rack(x.src) != rack(x.dst))*8,
                         sum(x.size for x in a))
        self.assertEqual([(t.src, t.dst, t.rail, t.step, t.size//4) for t in a],
                         [(t.src, t.dst, t.rail, t.step, t.size) for t in c])

    def test_dimension_dependencies_and_byte_conservation(self):
        chunk = 256 * 1024
        application_bytes, d = workload("D", chunk)
        self.assertEqual(application_bytes, 32*chunk)
        self.assertEqual(len(d), 640)
        self.assertEqual(sum(t.size for t in d), 32*62*chunk)
        self.assertEqual(sum(t.size for t in d if rack(t.src) != rack(t.dst)), 32*6*chunk)
        self.assertEqual(32*chunk//8//4*4*8, application_bytes)
        starts = {0, 7, 10, 13}
        for i, t in enumerate(d):
            self.assertEqual(not t.dependencies, t.step == 0)
            self.assertTrue(all(0 <= p < i for p in t.dependencies))
            if t.step in starts - {0}:
                self.assertEqual(len(t.dependencies), 2)
                self.assertTrue(any(d[p].src == t.src for p in t.dependencies))
                self.assertTrue(any(d[p].dst == t.src for p in t.dependencies))
                self.assertTrue(all(d[p].step == t.step-1 for p in t.dependencies))
            if t.step not in starts:
                self.assertEqual(len(t.dependencies), 1)
                self.assertEqual(d[t.dependencies[0]].dst, t.src)
                self.assertEqual(d[t.issue_after].src, t.src)
            if t.rail == 0:
                self.assertEqual(rack(t.src), rack(t.dst))
            else:
                self.assertEqual(t.src % 8, t.dst % 8)
        self.assertEqual(len(manifest_text(d).splitlines()), 641)
        self.assertEqual(len({(t.src, t.dst, t.sport, t.dport) for t in d}), len(d))

    def test_dimension_ring_shards_include_every_host_exactly_once(self):
        # Symbolically execute the declared byte ranges. Use a step snapshot
        # to prove the dataflow independently of packet timings.
        chunk = 8
        _, transfers = workload("D", chunk)
        state = [{i: {rank} for i in range(32)} for rank in range(32)]
        for step in range(20):
            incoming = {}
            for t in (t for t in transfers if t.step == step):
                for shard in range(t.offset//chunk, (t.offset+t.size)//chunk):
                    incoming[t.dst, shard] = set(state[t.src][shard])
            for (rank, shard), senders in incoming.items():
                if step < 10:
                    self.assertFalse(state[rank][shard] & senders)
                    state[rank][shard] |= senders
                else:
                    state[rank][shard] = senders
        for rank in state:
            self.assertEqual(list(rank.values()), [set(range(32))] * 32)

    def test_bounds_against_workload_and_balanced_synthetic_paths(self):
        chunk = 8_000_000
        _, a = workload("A", chunk)
        _, edges = topology("A", 40)
        routes, observed = {}, Counter()
        for i, t in enumerate(a):
            source, dest = rack(t.src), rack(t.dst)
            uplink = 33 + (t.src % 4)
            spine = edges[source, uplink][0]
            downlink = dest - 31
            access = (t.dst % 8)*4 + t.rail + 1
            routes[str(i)] = {source: uplink, spine: downlink, dest: access}
            # Synthetic balanced offered data plus one ACK per 1,000-byte packet.
            observed[source, uplink] += 8_864_000
        counters = [dict(node=s, port=p, bytes=b) for (s, p), b in observed.items()]
        report = analyze("A", chunk, routes=routes, observed_ports=counters)
        expected = dict(nic_payload_seconds=.3968, fabric_payload_seconds=3.1744,
                        fixed_route_data_seconds=3.3267712, strict_global_data_seconds=3.3267712,
                        fabric_observed_wire_seconds=3.5172352,
                        fixed_route_observed_wire_seconds=3.5172352)
        for key, value in expected.items():
            self.assertAlmostEqual(report["bounds"][key], value)
        self.assertAlmostEqual(analyze("E", chunk, 40)["bounds"]["fabric_payload_seconds"],
                               .7936)
        self.assertEqual(report["cross_rack_payload_bytes"], 63_488_000_000)
        routes["0"][rack(a[0].dst)] += 1
        with self.assertRaisesRegex(ValueError, "invalid destination"):
            analyze("A", chunk, routes=routes)


if __name__ == "__main__":
    unittest.main()
