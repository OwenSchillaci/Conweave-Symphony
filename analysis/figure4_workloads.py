"""Dedicated-rail workload generation and capacity accounting for Figure 4."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "config/symphony_figure4_scenario.txt"
TOPOLOGY = ROOT / "config/symphony_figure4_dedicated_topology.txt"
PROFILES = ("A", "B", "C", "D", "E")
MTU = 1000
DATA_HEADER = 48
ACK_BYTES = 60


@dataclass(frozen=True)
class Transfer:
    src: int
    dst: int
    rail: int
    step: int
    phase: str
    size: int
    dependencies: tuple = ()
    issue_after: int = -1
    sport: int = 0
    dport: int = 0
    offset: int = 0


def original_rings():
    return [tuple(map(int, line.split()[1:-1])) for line in SCENARIO.read_text().splitlines()
            if line.startswith("RING_JOB ")]


def rack(host):
    return 32 + host // 8


def ring_orders(profile):
    rings = original_rings()
    if profile == "B":
        return [tuple(sorted(order, key=rack)) for order in rings]
    return rings


def workload(profile, chunk):
    """Return application bytes per host and transfers indexed by stable flow ID."""
    if profile not in PROFILES or chunk <= 0 or chunk > (2**32 - 1) // 4:
        raise ValueError("invalid profile or chunk outside supported size")
    if profile == "C" and chunk % 4:
        raise ValueError("profile C requires a chunk divisible by four")
    if profile != "D":
        size = chunk // 4 if profile == "C" else chunk
        return 4 * 32 * size, [
            Transfer(src, order[(rank + 1) % 32], rail, step,
                     "reduce-scatter" if step < 31 else "all-gather", size)
            for rail, order in enumerate(ring_orders(profile))
            for rank, src in enumerate(order)
            for step in range(62)
        ]

    # Coordinates x=h%8, y=h//8. Within a rank, each phase waits for
    # both its own final send and its final receive in the preceding phase.
    across = [tuple(x + 8*y for y in range(4)) for x in range(8)]
    within = [tuple(8*y + x for x in range(8)) for y in range(4)]
    stages = (("local-rs", within, 0, 7, 4*chunk),
              ("fabric-rs", across, 1, 3, chunk),
              ("fabric-ag", across, 1, 3, chunk),
              ("local-ag", within, 0, 7, 4*chunk))
    transfers = []
    sources, destinations = Counter(), Counter()
    previous = {}
    logical_step = 0
    for phase, rings, rail, rounds, size in stages:
        phase_previous = previous
        last_issued = {}
        for iteration in range(rounds):
            completed = {}
            for order in rings:
                for rank, src in enumerate(order):
                    dst = order[(rank + 1) % len(order)]
                    if iteration:
                        dependencies = (previous[src][1],)
                    else:
                        dependencies = phase_previous.get(src, ())
                    shard = (rank - iteration + phase.endswith("-ag")) % len(order)
                    if rail == 0:
                        offset = shard * 4 * chunk
                    else:
                        offset = ((src % 8 + 1) % 8 * 4 + shard) * chunk
                    transfer = Transfer(src, dst, rail, logical_step, phase, size,
                                        dependencies, last_issued.get(src, -1),
                                        10000 + sources[src], 100 + destinations[dst], offset)
                    sources[src] += 1
                    destinations[dst] += 1
                    send, receive = completed.get(src, (None, None))
                    completed[src] = len(transfers), receive
                    send, receive = completed.get(dst, (None, None))
                    completed[dst] = send, len(transfers)
                    last_issued[src] = len(transfers)
                    transfers.append(transfer)
            previous = completed
            logical_step += 1
    return 32*chunk, transfers


def manifest_text(transfers):
    rows = [str(len(transfers))]
    for t in transfers:
        rows.append(" ".join(map(str, (t.src, t.dst, t.rail, t.step, t.size, t.sport,
                                       t.dport, t.issue_after, len(t.dependencies),
                                       *t.dependencies))))
    return "\n".join(rows) + "\n"


def topology(profile, uplink_gbps):
    """Port numbers follow QbbHelper installation order in the topology file."""
    header, switches, *links = TOPOLOGY.read_text().splitlines()
    ports = Counter()
    edges = {}
    lines = [header, switches]
    for line in links:
        left, right, _, delay, error = line.split()
        a, b = int(left), int(right)
        rate = uplink_gbps if profile == "E" and a >= 32 and b >= 32 else 10
        ports[a] += 1
        ports[b] += 1
        edges[a, ports[a]] = (b, rate)
        edges[b, ports[b]] = (a, rate)
        lines.append(f"{a} {b} {rate}Gbps {delay} {error}")
    return "\n".join(lines) + "\n", edges


def wire_data_bytes(payload):
    return payload + (payload + MTU - 1) // MTU * DATA_HEADER


def analyze(profile, chunk, uplink_gbps=40, routes=None, observed_ports=None):
    per_host, transfers = workload(profile, chunk)
    _, edges = topology(profile, uplink_gbps)
    total = Counter()
    racks, rails, phases, nics, pairs = (Counter() for _ in range(5))
    rack_phase, port_phase = Counter(), Counter()
    port_payload, port_wire, step_wire = Counter(), Counter(), Counter()
    for i, t in enumerate(transfers):
        is_cross = rack(t.src) != rack(t.dst)
        total["cross_rack" if is_cross else "intra_rack"] += t.size
        racks[rack(t.src)] += t.size
        pairs[rack(t.src), rack(t.dst)] += t.size
        rails[t.rail] += t.size
        phases[t.phase] += t.size
        rack_phase[rack(t.src), t.phase] += t.size
        nics[t.src, t.rail] += t.size
        if routes is None:
            continue
        path = {int(switch): int(port) for switch, port in routes[str(i)].items()}
        if len(path) != (3 if is_cross else 1):
            raise ValueError(f"Flow {i} has unexpected path length")
        node = rack(t.src)
        traversed = [(t.src, t.rail + 1)]
        while node in path:
            port = path.pop(node)
            traversed.append((node, port))
            node = edges[node, port][0]
        if path or node != t.dst or traversed[-1] != (rack(t.dst), (t.dst % 8)*4+t.rail+1):
            raise ValueError(f"Flow {i} has an invalid destination path or rail")
        for key in traversed:
            port_payload[key] += t.size
            port_phase[key + (t.phase,)] += t.size
            port_wire[key] += wire_data_bytes(t.size)
            step_wire[t.step, key] += wire_data_bytes(t.size)

    def keyed(counter):
        return {":".join(map(str, k)) if isinstance(k, tuple) else str(k): v
                for k, v in sorted(counter.items())}

    def seconds(key, size):
        return size * 8 / (1e9 * edges[key][1])

    capacity = sum(rate for (src, _), (dst, rate) in edges.items() if 32 <= src < 36 <= dst)
    bounds = dict(nic_payload_seconds=max(seconds((host, rail+1), size)
                                          for (host, rail), size in nics.items()),
                  fabric_payload_seconds=8 * total["cross_rack"] / (capacity * 1e9))
    for r in racks:
        uplink_rate = sum(rate for (src, _), (dst, rate) in edges.items() if src == r and dst >= 36)
        incoming = sum(v for (source, dst), v in pairs.items() if dst == r and source != r)
        outgoing = sum(v for (source, dst), v in pairs.items() if source == r and dst != r)
        bounds["fabric_payload_seconds"] = max(bounds["fabric_payload_seconds"],
                                              8 * max(incoming, outgoing) / (uplink_rate * 1e9))
    if routes is not None:
        bounds["fixed_route_data_seconds"] = max(seconds(k, v) for k, v in port_wire.items())
        per_step = defaultdict(float)
        for (step, key), size in step_wire.items():
            per_step[step] = max(per_step[step], seconds(key, size))
        bounds["strict_global_data_seconds"] = sum(per_step.values())
    if observed_ports:
        observed = {(r["node"], r["port"]): r["bytes"] for r in observed_ports}
        fabric_wire = sum(size for key, size in observed.items()
                          if 32 <= key[0] < 36 <= edges[key][0])
        bounds["fabric_observed_wire_seconds"] = 8 * fabric_wire / (capacity * 1e9)
        bounds["fixed_route_observed_wire_seconds"] = max(seconds(k, v) for k, v in observed.items())
    else:
        fabric_wire = None
    return dict(profile=profile, chunk_bytes=chunk, uplink_gbps=(uplink_gbps if profile == "E" else 10),
                ranks=32, transfer_count=len(transfers), input_per_host_bytes=per_host,
                input_all_hosts_bytes=32*per_host, network_payload_bytes=sum(total.values()),
                cross_rack_payload_bytes=total["cross_rack"], intra_rack_payload_bytes=total["intra_rack"],
                fabric_observed_wire_bytes=fabric_wire, payload_by_rack=keyed(racks),
                payload_by_rack_pair=keyed(pairs), payload_by_rail=keyed(rails),
                payload_by_phase=keyed(phases), payload_by_rack_phase=keyed(rack_phase),
                payload_by_host_nic=keyed(nics), payload_by_physical_port=keyed(port_payload),
                payload_by_port_phase=keyed(port_phase), bounds=bounds)
