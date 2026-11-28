#!/usr/bin/env python3
"""Join observed egress decisions, packet activity and DCQCN recovery."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from run_symphony_figure4 import fields

MODES = (("off", "DCQCN"), ("on", "Original Symphony"), ("egress", "Egress-aware"))
RECORDS = {"FIGURE4_DAG_TRANSFER", "FIGURE4_EGRESS", "SYMPHONY_DEQUEUE",
           "SYMPHONY_CNP", "SYMPHONY_RATE", "SYMPHONY_RECOVERY"}


def read_trace(path):
    records = defaultdict(list)
    with path.open() as stream:
        for line in stream:
            name = line.split(" ", 1)[0]
            if name in RECORDS:
                records[name].append(fields(line))
    return records


def inspect(folder):
    summary = json.loads((folder / "summary.json").read_text())
    trace = read_trace(folder / "collective.log")
    departures = {(r["switch"], r["port"], int(r["bin"])): int(r["steps"])
                  for r in trace["FIGURE4_EGRESS"]}
    if not departures:
        raise ValueError(f"missing detailed egress trace in {folder}")
    flow = {r["flow_id"]: {key: int(value) for key, value in r.items()}
            for r in trace["FIGURE4_DAG_TRANSFER"]}
    decisions = [r for r in trace["SYMPHONY_DEQUEUE"] if r["selected"] == "1"]
    marks = [r for r in decisions if r["mark"] in ("both", "symphony")]
    gates_closed = [r for r in decisions if r["gate"] == "0"]
    if len(marks) != summary["symphony_marks"]:
        raise ValueError(f"incomplete mark decisions in {folder}")
    if summary["mode"] == "egress" and any(int(r["earlier_queued"]) == 0 for r in marks):
        raise ValueError(f"egress gate marked without waiting earlier traffic in {folder}")

    no_queued = 0
    no_departures = 0
    for r in marks:
        no_queued += int(r["earlier_queued"]) == 0
        key = (r["switch"], r["egress"], int(r["time_ns"])//100000)
        mask = departures[key]
        if not mask & (1 << int(r["step"])):
            raise ValueError(f"mark missing from egress activity in {folder}")
        earlier_mask = (1 << int(r["step"])) - 1
        no_departures += not (mask & earlier_mask)

    last_cnp = {}
    for r in trace["SYMPHONY_CNP"]:
        last_cnp[r["flow_id"]] = (int(r["time_ns"]), int(r["tx_payload_bytes"]))
    event_rates = defaultdict(list)
    for name in ("SYMPHONY_RATE", "SYMPHONY_RECOVERY"):
        for r in trace[name]:
            event_rates[r["flow_id"]].append((int(r["time_ns"]), int(r["after_bps"])))

    flow_rows = []
    origin = min(r["start_ns"] for r in flow.values())
    for fid, r in flow.items():
        t, tx = last_cnp.get(fid, (r["completion_ns"], r["tx_payload_bytes"]))
        complete = r["completion_ns"]
        if t > complete or tx > r["tx_payload_bytes"]:
            raise ValueError(f"invalid CNP tail on flow {fid}")
        recovery = [rate for time, rate in event_rates[fid] if t < time <= complete]
        flow_rows.append(dict(seed=summary["seed"], mode=summary["mode"], flow_id=fid,
                              src=r["src"], dst=r["dst"], step=r["step"],
                              start_ns=r["start_ns"],
                              start_ms=(r["start_ns"]-origin)/1e6,
                              finish_ms=(complete-origin)/1e6,
                              duration_ms=(complete-r["start_ns"])/1e6,
                              had_cnp=fid in last_cnp, post_cnp_ms=(complete-t)/1e6,
                              post_cnp_payload_bytes=r["tx_payload_bytes"]-tx,
                              max_rate_change_after_cnp_gbps=max(recovery)/1e9 if recovery else None))
    last = max(flow_rows, key=lambda r: r["finish_ms"])
    ports = [p for p in summary["ports"] if p["node"] >= 32]
    result = dict(seed=summary["seed"], mode=summary["mode"], cct_ms=summary["cct_ns"]/1e6,
                  peak_steps=summary["peak_steps"], mean_steps=summary["mean_steps"],
                  symphony_marks=len(marks), without_earlier_queued=no_queued,
                  without_earlier_departure_in_bin=no_departures,
                  suppressed_selections=len(gates_closed), queue_marks=summary["queue_marks"],
                  both_marks=summary["both_marks"], cnp_count=len(trace["SYMPHONY_CNP"]),
                  recovery_rate_changes=len(trace["SYMPHONY_RECOVERY"]),
                  peak_egress_queue_bytes=max(p.get("queue_max", 0) for p in ports),
                  peak_switch_port_utilization=max(p["utilization"] for p in ports),
                  all_post_cnp_payload_bytes=sum(r["post_cnp_payload_bytes"] for r in flow_rows),
                  final_flow_id=last["flow_id"], final_flow_duration_ms=last["duration_ms"],
                  final_flow_post_cnp_ms=last["post_cnp_ms"],
                  final_flow_post_cnp_payload_bytes=last["post_cnp_payload_bytes"])
    return result, flow_rows, summary, event_rates


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def plot(output, rows, by_seed):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seeds = sorted(by_seed)
    figure, axes = plt.subplots(2, 3, figsize=(12, 7))
    metrics = (("cct_ms", "CCT (ms)"), ("mean_steps", "Mean active steps"),
               ("symphony_marks", "Symphony marks"), ("queue_marks", "Queue ECN marks"),
               ("peak_egress_queue_bytes", "Peak egress queue (KB)"),
               ("peak_switch_port_utilization", "Busiest port utilization (%)"))
    scales = {"peak_egress_queue_bytes": .001, "peak_switch_port_utilization": 100}
    for axis, (key, label) in zip(axes.flat, metrics):
        axis.set(xticks=range(len(seeds)), xticklabels=seeds, xlabel="Seed", ylabel=label)
        for i, (mode, title) in enumerate(MODES):
            values = [next(r[key] for r in rows if r["seed"] == seed and r["mode"] == mode)
                      * scales.get(key, 1) for seed in seeds]
            axis.bar([x + (i-1)*.24 for x in range(len(seeds))], values, width=.24, label=title)
    axes.flat[0].legend(fontsize=7)
    figure.tight_layout()
    figure.savefig(output / "scope-comparison.pdf")
    figure.savefig(output / "scope-comparison.png", dpi=150)
    plt.close(figure)

    seed = seeds[0]
    setups = by_seed[seed]
    fid = setups["on"][0]["final_flow_id"]
    figure, axis = plt.subplots(figsize=(7, 3.7))
    for mode, title in MODES:
        _, flows, _, events = setups[mode]
        target = next(r for r in flows if r["flow_id"] == fid)
        start = target["start_ns"]
        points = [(0, 10)] + [((t-start)/1e6, rate/1e9)
                              for t, rate in sorted(events[fid])]
        points = [p for p in points if 0 <= p[0] <= target["duration_ms"]]
        points.append((target["duration_ms"], points[-1][1]))
        x, y = zip(*points)
        axis.step(x, y, where="post", label=title)
        if target["had_cnp"]:
            last_cnp = target["duration_ms"] - target["post_cnp_ms"]
            axis.axvline(last_cnp, color=axis.lines[-1].get_color(), linestyle=":", linewidth=1)
    axis.set(xlabel="Time since flow launch (ms)", ylabel="Configured rate (Gb/s)",
             title=f"Seed {seed}, flow {fid}. Dotted line marks its last CNP.")
    axis.legend()
    figure.tight_layout()
    figure.savefig(output / "final-flow-recovery.pdf")
    figure.savefig(output / "final-flow-recovery.png", dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, help="path to bytes-524288/D")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    by_seed = {}
    for seed in sorted(args.runs.glob("seed-*")):
        if all((seed / mode / "summary.json").exists() for mode, _ in MODES):
            trials = {mode: inspect(seed / mode) for mode, _ in MODES}
            by_seed[trials["off"][0]["seed"]] = trials
    if not by_seed:
        parser.error("no complete three-treatment seed")
    trials = [trial for seed in by_seed.values() for trial in seed.values()]
    rows = [t[0] for t in trials]
    write_csv(args.output / "scope.csv", rows)
    write_csv(args.output / "flow-tails.csv", [r for t in trials for r in t[1]])
    (args.output / "ports.json").write_text(json.dumps([
        dict(seed=t[0]["seed"], mode=t[0]["mode"], ports=t[2]["ports"]) for t in trials]))
    plot(args.output, rows, by_seed)


if __name__ == "__main__":
    main()
