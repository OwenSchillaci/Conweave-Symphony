#!/usr/bin/env python3
"""Run Profile F perturbation experiments."""

import argparse
import concurrent.futures
import csv
import json
import os
import subprocess
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from analyze_ring import active_intervals
from profile_f_compact import compact_log
from run_ring_suite import OUTPUT_KEYS

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SCENARIOS = ("clean", "hotspot", "skew", "both")
TREATMENTS = {
    "dcqcn": (0, 0),
    "symphony": (0, 2),
    "conweave": (9, 0),
    "conweave-symphony": (9, 2),
    "conweave-symphony-separated": (9, 2),
}
STANDALONE_TREATMENTS = ("dcqcn", "symphony", "conweave")
RING = (0, 4, 1, 5, 2, 6, 3, 7)
STEPS = 14
BIN_NS = 100_000
WIRE_BYTES_PER_BIN = 125_000


def configure(args, scenario, mode, folder):
    lb, symphony = TREATMENTS[mode]
    delay = [0] * len(RING)
    if scenario in ("skew", "both"):
        delay[args.skew_rank] = args.skew_ns
    options = {
        "TOPOLOGY_FILE": CONFIG / "profile_f_topology.txt",
        "PROFILE_F_BACKGROUND": folder.parent / "background.txt",
        "RING_CHUNK_BYTES": args.chunk, "RANDOM_SEED": args.seed,
        "LB_MODE": lb, "SYMPHONY_MODE": symphony,
        "CONWEAVE_REPLY_TIMEOUT_EXTRA": args.reply_extra_us,
        "CONWEAVE_EXTRA_VOQ_FLUSH_TIME": args.flush_extra_us,
        "CONWEAVE_PATH_PAUSE_TIME": args.path_pause_us,
        "CONWEAVE_TX_EXPIRY_TIME": args.expiry_us,
        "CONWEAVE_DEFAULT_VOQ_WAITING_TIME": args.default_voq_us,
    }
    lines = ["RING_PROCESSING_DELAY_NS " + " ".join(map(str, delay))]
    if mode == "conweave-symphony-separated":
        lines.append("PROFILE_F_QUEUE_ONLY_NOTIFY 1")
    if args.mark_provenance:
        lines.append("PROFILE_F_MARK_PROVENANCE 1")
    if args.pilot_detail:
        lines.append("PROFILE_F_PILOT_DETAIL 1")
    for line in (CONFIG / "profile_f_scenario.txt").read_text().splitlines():
        if not line:
            continue
        key, value = line.split(" ", 1)
        if key in OUTPUT_KEYS:
            value = folder / Path(value).name
        lines.append(f"{key} {options.get(key, value)}")
    return "\n".join(lines) + "\n", delay


def background_text(args, scenario):
    if scenario not in ("hotspot", "both"):
        return ""
    # The manifest specifies data direction. The simulator also pins reverse ACKs.
    lines = []
    for line in args.hotspot_manifest.read_text().splitlines():
        fields = line.partition("#")[0].split()
        if fields:
            if len(fields) != 9 or any(not f.isascii() or not f.isdecimal() for f in fields):
                raise ValueError(f"invalid hotspot row: {line}")
            if args.background_bytes is not None:
                fields[7] = str(args.background_bytes)
            if args.background_start_ns is not None:
                fields[8] = str(args.background_start_ns)
            lines.append(" ".join(fields))
    if len({tuple(line.split()[1:4]) for line in lines}) != 1:
        raise ValueError("hotspot flows must share one directed ToR-spine-ToR path")
    if any(line.split()[2] not in ("14", "15") for line in lines):
        raise ValueError("hotspot must use spine 14 or 15")
    return "\n".join(lines) + "\n"


def step_timeline(transfers):
    events = defaultdict(Counter)
    for (rank, step), record in transfers.items():
        events[record["start_ns"]][step] += 1
        events[record["completion_ns"]][step] -= 1
    active = Counter()
    timeline = []
    for t in sorted(events):
        active.update(events[t])
        timeline.append((t, sum(n > 0 for n in active.values())))
    return timeline


def validate_transfers(transfers, complete, delay, chunk, expected_start):
    if set(transfers) != {(r, s) for r in range(len(RING)) for s in range(STEPS)}:
        raise ValueError("missing ring transfers")
    if complete["start_ns"] != expected_start:
        raise ValueError("unexpected collective release time")
    waits = []
    for (rank, step), row in sorted(transfers.items()):
        dst_rank = (rank + 1) % len(RING)
        if (row["src"], row["dst"], row["sport"], row["dport"], row["bytes"]) != (
                RING[rank], RING[dst_rank], 10000 + step, 100 + step, chunk):
            raise ValueError(f"unexpected ring tuple or size at rank {rank}, step {step}")
        start, end = row["start_ns"], row["completion_ns"]
        if end <= start:
            raise ValueError("nonpositive transfer duration")
        if step == 0:
            if start != expected_start:
                raise ValueError("initial release times differ")
            continue
        arrived = transfers[((rank - 1) % len(RING), step - 1)]["completion_ns"]
        previous_issue = transfers[(rank, step - 1)]["start_ns"]
        ready = arrived + delay[rank]
        if start != max(ready, previous_issue):
            raise ValueError(f"unexpected dependency release at rank {rank}, step {step}")
        waits.append({"rank": rank, "step": step, "predecessor_ack_ns": arrived,
                      "dependency_wait_ns": max(0, ready - previous_issue),
                      "processing_ns": delay[rank], "ready_to_issue_ns": start - ready})
    if max(row["completion_ns"] for row in transfers.values()) != complete["completion_ns"]:
        raise ValueError("collective completion timestamp disagrees with final ACK")
    return waits


def read_results(folder, delay, chunk):
    records = defaultdict(list)
    transfers = {}
    prefixes = {"RING_TRANSFER", "RING_COMPLETE", "SYMPHONY_DEQUEUE",
                "FIGURE4_FLOW_SAMPLE"}
    with (folder / "collective.log").open() as stream:
        for line in stream:
            kind, _, rest = line.partition(" ")
            if kind not in prefixes and not kind.startswith("PROFILE_F_"):
                continue
            row = dict(s.split("=", 1) for s in rest.split())
            row = {k: int(v) if v.lstrip("-").isdigit() else v for k, v in row.items()}
            if kind == "RING_TRANSFER":
                key = row["rank"], row["step"] + (7 if row["phase"] == "all-gather" else 0)
                if key in transfers:
                    raise ValueError(f"duplicate transfer {key}")
                transfers[key] = row
            else:
                records[kind].append(row)
    complete, = records["RING_COMPLETE"]
    config = dict(line.split(None, 1) for line in (folder / "scenario.txt").read_text().splitlines())
    expected_start = int(Decimal(config["FLOWGEN_START_TIME"]) * 1_000_000_000)
    waits = validate_transfers(transfers, complete, delay, chunk, expected_start)
    start, end = complete["start_ns"], complete["completion_ns"]
    duration = end - start
    timeline = step_timeline(transfers)
    area = sum((b - a) * steps for (a, steps), (b, _) in zip(timeline, timeline[1:]))
    overlaps = [(a, b) for (a, steps), (b, _) in zip(timeline, timeline[1:]) if steps > 1]
    overlap = sum(b - a for a, b in overlaps)
    skew_intervals = active_intervals(
        (r["predecessor_ack_ns"], r["predecessor_ack_ns"] + r["processing_ns"])
        for r in waits if r["processing_ns"])
    skew_overlap = sum(max(0, min(b, d) - max(a, c))
                       for a, b in overlaps for c, d in skew_intervals)
    port_rows = records["PROFILE_F_PORT"]
    if not port_rows or not records["FIGURE4_FLOW_SAMPLE"]:
        raise ValueError("missing Profile F telemetry")
    manifest = [list(map(int, line.split())) for line in
                (folder.parent / "background.txt").read_text().splitlines()]
    background = records["PROFILE_F_BACKGROUND_START"]
    if sorted((r["src"], r["dst"], r["sport"], r["dport"], r["bytes"], r["time_ns"], r["spine"])
              for r in background) != sorted((r[0], r[4], r[5], r[6], r[7], r[8], r[2])
                                             for r in manifest):
        raise ValueError("background runtime offer differs from manifest")
    if {r["flow_id"] for r in background} != {
            r["flow_id"] for r in records["PROFILE_F_BACKGROUND_COMPLETE"]}:
        raise ValueError("background did not complete")
    verify_pins(port_rows, manifest)
    if config["LB_MODE"] == "9" and (
            len(records["PROFILE_F_CW_END"]) != 2 or
            any(r["voqs"] for r in records["PROFILE_F_CW_END"])):
        raise ValueError("ConWeave missing ToR telemetry or has undrained VOQs")
    path_rows = records["PROFILE_F_FLOW_PATH"]
    if {r["flow_id"] for r in path_rows} != {r["flow_id"] for r in transfers.values()}:
        raise ValueError("missing observed cross-rack paths for collective transfers")
    if config["LB_MODE"] == "0" and len(path_rows) != len(transfers):
        raise ValueError("static routing moved collective traffic between paths")
    phase_tx = {(r["flow_id"], r["epoch"], r["phase"], r["port"]): r["time_ns"]
                for r in records["PROFILE_F_PHASE_TX"]}
    forwarded = []
    for r in records["PROFILE_F_PATH"]:
        tx = phase_tx.get((r["flow_id"], r["epoch"], r["new_phase"], r["new"] & 255))
        if r["changed"] and tx is not None and tx >= r["time_ns"]:
            forwarded.append(dict(r, first_new_tx_ns=tx))
    for row in port_rows:
        row["wire_utilization"] = row["wire_bytes"] / WIRE_BYTES_PER_BIN
        row["sample_mean_queue_bytes"] = row["queue_sum"] / row["samples"] if row["samples"] else 0
    for row in records["FIGURE4_FLOW_SAMPLE"]:
        row["mean_sampled_pacing_bps"] = row["rate_sum"] / row["packets"]
        row["wire_bps"] = row["bytes"] * 8000
    for filename, data in (
            ("ports.csv", port_rows), ("rates.csv", records["FIGURE4_FLOW_SAMPLE"]),
            ("dependency_waits.csv", waits), ("transfers.csv", list(transfers.values())),
            ("notifications.csv", records["PROFILE_F_NOTIFY"]),
            ("path_exclusions.csv", records["PROFILE_F_EXCLUDE"]),
            ("path_updates.csv", records["PROFILE_F_PATH"]),
            ("forwarded_reroutes.csv", forwarded),
            ("flow_paths.csv", records["PROFILE_F_FLOW_PATH"]),
            ("voq_residence.csv", records["PROFILE_F_VOQ"])):
        write_csv(folder / filename, data)
    # Compare a hot link with its alternate over the very same complete bins.
    hot_link = tuple(manifest[0][1:3]) if manifest else (12, 14)
    alternate = (hot_link[0], 15 if hot_link[1] == 14 else 14)
    hot, spare = ({r["start_ns"]: r for r in port_rows if (r["node"], r["peer"]) == pair}
                  for pair in (hot_link, alternate))
    eligible_bins = {
        r["time_ns"] // BIN_NS * BIN_NS
        for r in records["SYMPHONY_DEQUEUE"] if r["eligible"] and r["switch"] == hot_link[0]
    }
    eligible_bins.update(r["start_ns"] for r in records["PROFILE_F_COMPACT_ELIGIBLE"]
                         if r["switch"] == hot_link[0])
    coexist = [t for t, row in hot.items() if start <= t and t + BIN_NS <= end
               and (not manifest or row["background_bytes"]) and row["queue_marks"]
               and spare.get(t, {}).get("wire_bytes", 0) <
               0.9 * WIRE_BYTES_PER_BIN
               and any(a < t + BIN_NS and b > t for a, b in overlaps)]
    path_totals = defaultdict(Counter)
    for row in port_rows:
        for metric in ("wire_bytes", "collective_bytes", "background_bytes", "background_feedback_bytes"):
            path_totals[(row["node"], row["peer"])][metric] += row[metric]
    return {
        "cct_ns": duration,
        "peak_steps": max(steps for _, steps in timeline), "mean_steps": area / duration,
        "overlap_ns": overlap, "queue_marks": sum(r["queue_marks"] for r in port_rows),
        "symphony_marks": sum(r["symphony_marks"] for r in port_rows),
        "symphony_eligible": (sum(r["eligible"] for r in records["SYMPHONY_DEQUEUE"]) +
                              sum(r["eligible"] for r in records["PROFILE_F_COMPACT"])),
        "both_marks": sum(r["both_marks"] for r in port_rows),
        "hot_marks": sum(row["queue_marks"] for row in hot.values()),
        "hot_queue_peak_bytes": max((row["queue_max"] for row in hot.values()), default=0),
        "joint_bins": len(coexist), "joint_with_eligible_bins": sum(t in eligible_bins for t in coexist),
        "min_alt_util_in_joint": min((spare.get(t, {}).get("wire_bytes", 0) /
                                     WIRE_BYTES_PER_BIN for t in coexist), default=None),
        "forwarded_reroutes": len(forwarded),
        "hot_to_alternate": sum(
            r["switch"] == hot_link[0]
            and (r["old"] & 255) in {row["port"] for row in hot.values()}
            and (r["new"] & 255) in {row["port"] for row in spare.values()} for r in forwarded),
        "injected_wait_ns": sum(r["processing_ns"] for r in waits),
        "skew_overlap_ns": skew_overlap,
        "init_replies": sum(r["init_replies"] for r in records["PROFILE_F_CW_FEEDBACK"]),
        "timely_init_replies": sum(r["timely_init_replies"] for r in records["PROFILE_F_CW_FEEDBACK"]),
        "receiver_ooo_notifications": (
            sum(r["ooo"] for r in records["PROFILE_F_CNP_TAIL"]) +
            sum(int(line.split()[3]) for line in (folder / "cnp.txt").read_text().splitlines())
        ),
        "host_downlink_queue_peak_bytes": max((r["queue_max"] for r in port_rows if r["peer"] < 12),
                                               default=0),
        "hot_tor": hot_link[0], "hot_spine": hot_link[1],
        "notify": len(records["PROFILE_F_NOTIFY"]), "exclusions": len(records["PROFILE_F_EXCLUDE"]),
        "reroutes": sum(r["changed"] for r in records["PROFILE_F_PATH"]),
        "reply_timeouts": sum(r["timeout"] for r in records["PROFILE_F_PATH"]),
        "voq_packets": len(records["PROFILE_F_VOQ"]),
        "voq_max_ns": max((row["residence_ns"] for row in records["PROFILE_F_VOQ"]), default=0),
        "pfc_events": len((folder / "pfc.txt").read_text().splitlines()),
        "timeline": timeline, "start_ns": start,
        "paths": {f"{a}->{b}": dict(row) for (a, b), row in sorted(path_totals.items())},
    }


def verify_pins(rows, manifest):
    expected_links = {pair for flow in manifest for pair in zip(flow[1:4], flow[2:5])}
    measured = Counter()
    feedback = Counter()
    for row in rows:
        if row["background_bytes"]:
            measured[row["node"], row["peer"]] += row["background_bytes"]
        if row["background_feedback_bytes"]:
            feedback[row["node"], row["peer"]] += row["background_feedback_bytes"]
    expected_reverse = {pair for flow in manifest for pair in zip(flow[1:4], flow[:3])}
    if set(feedback) != expected_reverse:
        raise ValueError(f"background feedback used unintended egresses: {list(feedback)}")
    if set(measured) != expected_links:
        raise ValueError(f"background used unintended egresses: {list(measured)}")
    for _, tor, spine, peer, dst, *_ in manifest:
        if (measured[tor, spine] != measured[spine, peer] or
                feedback[peer, spine] != feedback[spine, tor]):
            raise ValueError("background byte count differs across the pinned fabric hops")


def write_csv(path, rows):
    with path.open("w") as stream:
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def run(args, scenario, mode):
    folder = args.output / scenario / mode
    folder.mkdir()
    text, delay = configure(args, scenario, mode, folder)
    config = folder / "scenario.txt"
    config.write_text(text)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [str(args.build)] + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
    command = [str(args.build / "scratch/network-load-balance"), str(config)]
    log = folder / "collective.log"
    with log.open("w") as stream:
        try:
            if args.compact:
                read_fd, write_fd = os.pipe()
                with os.fdopen(read_fd, encoding="utf-8") as source:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as reader:
                        consume = reader.submit(compact_log, source, stream,
                                                mode == "conweave-symphony-separated", BIN_NS)
                        try:
                            subprocess.run(command, cwd=ROOT, env=env, stdout=write_fd,
                                           stderr=subprocess.STDOUT, check=True, timeout=args.timeout)
                        finally:
                            os.close(write_fd)
                        consume.result()
            else:
                subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                               stderr=subprocess.STDOUT, check=True, timeout=args.timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"see {log}") from error
    result = read_results(folder, delay, args.chunk)
    result.update(scenario=scenario, treatment=mode, seed=args.seed, chunk=args.chunk)
    (folder / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f'{scenario:7} {mode:8} {result["cct_ns"]/1e6:8.3f} ms', flush=True)
    return result


def plot(output, results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (cct, overlap) = plt.subplots(1, 2, figsize=(9, 3.4), constrained_layout=True)
    modes = [mode for mode in TREATMENTS if any(r["treatment"] == mode for r in results)]
    spacing = 0.72 / len(modes)
    for i, mode in enumerate(modes):
        group = [r for r in results if r["treatment"] == mode]
        offset = (i - (len(modes) - 1) / 2) * spacing
        x = [SCENARIOS.index(r["scenario"]) + offset for r in group]
        cct.bar(x, [r["cct_ns"] / 1e6 for r in group], spacing * 0.95, label=mode)
        overlap.bar(x, [r["mean_steps"] for r in group], spacing * 0.95, label=mode)
    for ax in (cct, overlap):
        ax.set_xticks(range(len(SCENARIOS)), SCENARIOS)
        ax.grid(axis="y", alpha=0.2)
    cct.set_ylabel("Collective completion time (ms)")
    overlap.set_ylabel("Time-weighted active steps")
    overlap.legend()
    fig.savefig(output / "comparison.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(len(SCENARIOS), 1, figsize=(8, 7), constrained_layout=True)
    for ax, scenario in zip(axes, SCENARIOS):
        for r in results:
            if r["scenario"] != scenario:
                continue
            x, steps = zip(*r["timeline"])
            ax.step([(t - r["start_ns"]) / 1e6 for t in x], steps, where="post",
                    label=r["treatment"])
        ax.set_ylabel(scenario)
    next(ax for ax in axes if ax.lines).legend()
    axes[-1].set_xlabel("Time since collective start (ms)")
    fig.supylabel("Active logical steps")
    fig.savefig(output / "step-overlap.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(8, 5), sharex=True, constrained_layout=True)
    for r in results:
        if r["scenario"] != "both":
            continue
        with (output / "both" / r["treatment"] / "ports.csv").open() as stream:
            ports = list(csv.DictReader(stream))
        hot = r["hot_spine"]
        for peer, style in ((hot, "-"), (15 if hot == 14 else 14, "--")):
            rows = {int(p["start_ns"]): p for p in ports
                    if int(p["node"]) == r["hot_tor"] and int(p["peer"]) == peer}
            times = range(r["start_ns"], r["start_ns"] + r["cct_ns"], BIN_NS)
            x = [(t - r["start_ns"]) / 1e6 for t in times]
            axes[0].plot(x, [int(rows.get(t, {}).get("wire_bytes", 0)) / WIRE_BYTES_PER_BIN
                             for t in times], style, label=f"{r['treatment']} → {peer}")
            axes[1].plot(x, [int(rows.get(t, {}).get("queue_max", 0)) / 1000
                             for t in times], style)
    axes[0].set_ylabel("Wire utilization")
    axes[1].set_ylabel("Peak queued kB per bin")
    axes[1].set_xlabel("Time since collective start (ms)")
    if axes[0].lines:
        axes[0].legend(ncol=2, fontsize=8)
        fig.savefig(output / "both-fabric.pdf")
    plt.close(fig)


def check_pilot(results, check):
    """Check mechanism exposure separately from CCT."""
    if check == "quiet":
        if len(results) != 1 or (results[0]["scenario"], results[0]["treatment"]) != (
                "clean", "conweave"):
            raise ValueError("quiet check needs only clean ConWeave")
        r = results[0]
        checks = {
            "no_queue_ecn": r["queue_marks"] == 0,
            "all_init_replies_timely": r["init_replies"] == r["timely_init_replies"] > 0,
            "no_reply_timeouts": r["reply_timeouts"] == 0,
            "no_reroutes": r["reroutes"] == r["forwarded_reroutes"] == 0,
            "no_receiver_ooo_or_pfc": r["receiver_ooo_notifications"] == r["pfc_events"] == 0,
        }
    else:
        by_case = {(r["scenario"], r["treatment"]): r for r in results}
        if set(by_case) != {(c, m) for c in SCENARIOS for m in STANDALONE_TREATMENTS}:
            raise ValueError("matrix check needs all four scenarios and all three standalone treatments")
        checks = {}
        for case in ("hotspot", "both"):
            control = "clean" if case == "hotspot" else "skew"
            for mode in STANDALONE_TREATMENTS:
                r = by_case[case, mode]
                checks[f"{case}_{mode}_fabric_hotspot"] = (
                    r["hot_marks"] > by_case[control, mode]["hot_marks"]
                    and r["hot_queue_peak_bytes"] > r["host_downlink_queue_peak_bytes"]
                    and r["joint_bins"] > 0)
            r = by_case[case, "conweave"]
            checks[f"{case}_conweave_hot_path_escape"] = (
                r["notify"] > 0 and r["exclusions"] > 0 and r["hot_to_alternate"] > 0)
        for mode in STANDALONE_TREATMENTS:
            r = by_case["skew", mode]
            checks[f"skew_{mode}_independent_step_overlap"] = (
                r["injected_wait_ns"] > 0 and r["skew_overlap_ns"] > 0 and r["peak_steps"] > 1)
        checks["skew_symphony_eligible_and_marked"] = (
            by_case["skew", "symphony"]["symphony_eligible"] > 0
            and by_case["skew", "symphony"]["symphony_marks"] > 0)
        checks["both_symphony_joint_exposure"] = (
            by_case["both", "symphony"]["joint_with_eligible_bins"] > 0
            and by_case["both", "symphony"]["symphony_marks"] > 0)
        checks["no_receiver_ooo_or_pfc"] = all(
            r["receiver_ooo_notifications"] == r["pfc_events"] == 0 for r in results)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=ROOT / "build")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=SCENARIOS)
    parser.add_argument("--treatments", nargs="+", choices=TREATMENTS,
                        default=STANDALONE_TREATMENTS)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--chunk", type=int, default=512 * 1024)
    parser.add_argument("--skew-rank", type=int, default=1, choices=range(len(RING)))
    parser.add_argument("--skew-ns", type=int, default=250_000)
    parser.add_argument("--hotspot-manifest", type=Path, default=CONFIG / "profile_f_hotspot.txt")
    parser.add_argument("--background-bytes", type=int)
    parser.add_argument("--background-start-ns", type=int)
    parser.add_argument("--reply-extra-us", type=int, default=40)
    parser.add_argument("--flush-extra-us", type=int, default=16)
    parser.add_argument("--path-pause-us", type=int, default=80)
    parser.add_argument("--expiry-us", type=int, default=1000)
    parser.add_argument("--default-voq-us", type=int, default=500)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--check", choices=("matrix", "quiet"))
    parser.add_argument("--mark-provenance", action="store_true",
                        help="trace packet ECN mark causes and ConWeave feedback")
    parser.add_argument("--pilot-detail", action="store_true",
                        help="add sender recovery and VOQ timer observations")
    parser.add_argument("--compact", action="store_true",
                        help="keep provenance counters and bins instead of packet-level events")
    parser.add_argument("--no-plots", action="store_true",
                        help="skip per-seed plots when collecting a sweep")
    args = parser.parse_args()
    if args.compact and not (args.mark_provenance and args.pilot_detail):
        parser.error("--compact requires --mark-provenance and --pilot-detail")
    args.build, args.output = args.build.resolve(), args.output.resolve()
    if (args.output.exists() or any(c.isspace() for c in str(args.output)) or
        len(args.scenarios) != len(set(args.scenarios)) or
        len(args.treatments) != len(set(args.treatments)) or
        min(args.seed, args.chunk, args.workers, args.timeout) <= 0 or
        not 0 <= args.skew_ns <= 10_000_000 or
        (args.background_bytes is not None and args.background_bytes <= 0) or
        (args.background_start_ns is not None and args.background_start_ns < 0) or
        min(args.reply_extra_us, args.flush_extra_us, args.path_pause_us,
            args.expiry_us, args.default_voq_us) <= 0):
        parser.error("use a new whitespace-free output path, distinct cases, and valid parameters")
    if not (args.build / "scratch/network-load-balance").is_file():
        parser.error("build/scratch/network-load-balance is missing")
    try:
        manifests = {scenario: background_text(args, scenario) for scenario in args.scenarios}
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.output.mkdir(parents=True)
    for scenario in args.scenarios:
        folder = args.output / scenario
        folder.mkdir()
        (folder / "background.txt").write_text(manifests[scenario])
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(run, args, scenario, mode) for scenario in args.scenarios
                for mode in args.treatments]
        results = [job.result() for job in jobs]
    write_csv(args.output / "summary.csv", [
        {k: v for k, v in r.items() if k not in ("timeline", "paths")} for r in results
    ])
    if not args.no_plots:
        plot(args.output, results)
    if args.check:
        checks = check_pilot(results, args.check)
        (args.output / "validation.json").write_text(json.dumps(checks, indent=2) + "\n")
        failed = [key for key, passed in checks.items() if not passed]
        if failed:
            raise SystemExit("Pilot criteria failed: " + ", ".join(failed))
        print(f"Passed {len(checks)} pilot checks")


if __name__ == "__main__":
    main()
