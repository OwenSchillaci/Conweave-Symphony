#!/usr/bin/env python3
"""Run and plot the matched Symphony two-flow bottleneck experiment."""

import argparse
from collections import Counter
import csv
import os
from pathlib import Path
import subprocess

import matplotlib.pyplot as plt

from run_ring_suite import OUTPUT_KEYS, ROOT


BIN_NS = 100_000
FLOW_BYTES = 16 * 1024 * 1024
LABELS = {0: "Lagging (step 0)", 1: "Leading (step 1)"}


def run_case(build, output, mode, gain):
    output.mkdir(parents=True, exist_ok=True)
    config = output / "scenario.txt"
    lines = []
    for line in (ROOT / "config/symphony_two_flow_scenario.txt").read_text().splitlines():
        key, _, value = line.partition(" ")
        if key in OUTPUT_KEYS:
            line = f"{key} {output / Path(value).name}"
        elif key == "SYMPHONY_MODE":
            line = f"{key} {mode}"
        elif key == "SYMPHONY_GAIN":
            line = f"{key} {gain}"
        lines.append(line)
    config.write_text("\n".join(lines) + "\n")
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        path for path in (str(build), env.get("LD_LIBRARY_PATH")) if path
    )
    log = output / "collective.log"
    with log.open("w") as stream:
        result = subprocess.run([build / "scratch/network-load-balance", config],
                                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f"Simulator failed for mode {mode}. See {log}")
    return log


def measure(log, mode):
    starts, finishes = {}, {}
    bins = {step: Counter() for step in LABELS}
    marks, acknowledgments = set(), set()
    feedback = {}
    for line in log.open():
        kind, _, fields = line.partition(" ")
        if not (kind.startswith("TWO_FLOW_") or kind in (
            "SYMPHONY_CONFIG", "SYMPHONY_DEQUEUE", "SYMPHONY_FEEDBACK", "SYMPHONY_CNP"
        )):
            continue
        data = dict(field.split("=", 1) for field in fields.split())
        if kind == "SYMPHONY_CONFIG" and (int(data["mode"]) != mode or data["adaptive_alpha"] != "0"):
            raise ValueError(f"Unexpected Symphony configuration in {log}")
        if kind == "SYMPHONY_DEQUEUE":
            if data["alpha"] != "1":
                raise ValueError(f"Unexpected adaptive alpha in {log}")
            if data["mark"] in ("symphony", "both"):
                if mode == 0 or data["step"] != "1":
                    raise ValueError(f"Unexpected Symphony mark in {log}")
                if data["mark"] == "symphony":
                    marks.add(data["packet_uid"])
        elif kind == "SYMPHONY_FEEDBACK":
            feedback[data["packet_uid"]] = data["ack_uid"]
        elif kind == "SYMPHONY_CNP":
            acknowledgments.add(data["ack_uid"])
        elif kind.startswith("TWO_FLOW_"):
            step, time = int(data["step"]), int(data["time_ns"])
            if kind == "TWO_FLOW_START":
                starts[step] = time
            elif kind == "TWO_FLOW_COMPLETE":
                if int(data["bytes"]) != FLOW_BYTES:
                    raise ValueError(f"Unexpected completed byte count in {log}")
                finishes[step] = time
            elif kind == "TWO_FLOW_EGRESS":
                bins[step][(time - starts[1]) // BIN_NS] += int(data["bytes"])

    if starts.keys() != LABELS.keys() or finishes.keys() != LABELS.keys():
        raise ValueError(f"Missing start or completion in {log}")
    if not bins[0].keys() & bins[1].keys() or any(sum(b.values()) < FLOW_BYTES for b in bins.values()):
        raise ValueError(f"Missing payload or shared-egress overlap in {log}")
    chains = sum(feedback.get(packet) in acknowledgments for packet in marks)
    return starts, finishes, bins, chains


def report(output, cases, gain):
    fig, axes = plt.subplots(2, 1, sharex=True, sharey=True, figsize=(9, 6))
    metrics = []
    for ax, (label, (starts, finishes, bins, chains)) in zip(axes, cases):
        origin = starts[1]
        count = 1 + (max(finishes.values()) - origin) // BIN_NS
        edges = [index * BIN_NS / 1e6 for index in range(count + 1)]
        rates = {step: [bins[step][i] * 8 / BIN_NS for i in range(count)] for step in LABELS}
        with (output / f"{label}_throughput.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("bin_start_ms", "lagging_gbps", "leading_gbps"))
            writer.writerows(zip(edges[:-1], rates[0], rates[1]))
        for step, color in enumerate(("tab:blue", "tab:orange")):
            ax.stairs(rates[step], edges, color=color, label=LABELS[step])
            for time, style in ((starts[step], ":"), (finishes[step], "--")):
                ax.axvline((time - origin) / 1e6, color=color, linestyle=style, alpha=0.7)
        ax.set_title("DCQCN alone" if label == "baseline" else f"DCQCN + fixed-alpha Symphony (k={gain:g})")
        ax.set_ylabel("Payload Gb/s")
        ax.legend(loc="upper right")
        metrics.append((
            (finishes[0] - starts[0]) / 1e6,
            (finishes[1] - starts[1]) / 1e6,
            (min(finishes.values()) - max(starts.values())) / 1e6,
            (max(finishes.values()) - origin) / 1e6,
        ))
        print(f"{label}: {chains} Symphony-only marks matched to sender CNPs")
    axes[-1].set_xlabel("Time from leading flow start (ms) · dotted = start, dashed = completion")
    fig.tight_layout()
    fig.savefig(output / "throughput.pdf")
    for name, baseline, treatment in zip(
        ("Lagging FCT", "Leading FCT", "In-flight overlap", "Both finished"),
        *metrics,
    ):
        delta = treatment - baseline
        print(f"{name}: {baseline:.3f} → {treatment:.3f} ms ({delta:+.3f} ms, {100*delta/baseline:+.1f}%)")
    if not cases[1][1][3]:
        raise SystemExit("Treatment produced no verified Symphony-only mark-to-CNP chain")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "mix/output/symphony_two_flow")
    parser.add_argument("--gain", type=float, default=0.01, help="Symphony marking gain (default 0.01)")
    args = parser.parse_args()
    if not 0 < args.gain <= 1:
        parser.error("--gain must be in (0, 1]")
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    if any(c.isspace() for c in str(output)):
        parser.error("--output-dir cannot contain whitespace in simulator configs")
    cases = [(name, measure(run_case(build, output / name, mode, args.gain), mode))
             for name, mode in (("baseline", 0), ("symphony", 2))]
    report(output, cases, args.gain)


if __name__ == "__main__":
    main()
