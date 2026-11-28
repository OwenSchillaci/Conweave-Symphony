#!/usr/bin/env python3
"""Run matched workload diagnostics, or analyze archived Figure 4 routes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from collections import Counter

from figure4_workloads import (PROFILES, ROOT, SCENARIO, analyze, manifest_text,
                              ring_orders, topology, workload)
from run_ring_suite import OUTPUT_KEYS
from run_symphony_figure4 import fields


def scenario_text(profile, chunk, seed, mode, folder, uplink_gbps, scope_trace=False):
    options = dict(TOPOLOGY_FILE=folder.parent / "topology.txt", RANDOM_SEED=seed,
                   RING_CHUNK_BYTES=chunk // 4 if profile == "C" else chunk,
                   RING_FIGURE4=2, SYMPHONY_MODE={"off": 0, "on": 2, "egress": 3}[mode])
    if profile == "E":
        for key, base in (("KMIN_MAP", 50), ("KMAX_MAP", 100), ("PMAX_MAP", 0.2)):
            options[key] = f"2 10000000000 {base} {uplink_gbps*10**9} {base}"
    orders = iter(ring_orders(profile))
    lines = ["RING_DEDICATED_RAILS 1", "RING_FIGURE4_WORKLOAD_TELEMETRY 1"]
    if scope_trace:
        lines.append("SYMPHONY_SCOPE_TRACE 1")
    if profile == "E":
        lines.append("RING_FIGURE4_FIXED_WINDOW 1")
    if profile == "D":
        lines.append(f"RING_FIGURE4_MANIFEST {folder.parent / 'transfers.txt'}")
    for line in SCENARIO.read_text().splitlines():
        key = line.split(" ", 1)[0]
        if key == "RING_JOB":
            if profile != "D":
                lines.append("RING_JOB " + " ".join(map(str, next(orders))) + " 1")
        elif key in options:
            lines.append(f"{key} {options[key]}")
        elif key in OUTPUT_KEYS:
            lines.append(f"{key} {folder / Path(line.split()[-1]).name}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def inspect(folder, profile, chunk, uplink_gbps):
    _, transfers = workload(profile, chunk)
    completed, routes, port_samples, ports = {}, {}, {}, []
    with gzip.open(folder / "flow-rates.csv.gz", "wt") as output, (folder / "collective.log").open() as log:
        writer = csv.writer(output)
        writer.writerow(("flow_id", "bin_ms", "wire_bytes", "packets", "rate_sum_bps",
                         "min_rate_bps", "max_rate_bps"))
        for line in log:
            kind = line.split(" ", 1)[0]
            if kind in ("RING_TRANSFER", "FIGURE4_DAG_TRANSFER"):
                row = fields(line)
                i = int(row["flow_id"])
                t = transfers[i]
                step = int(row["step"]) + 31 * (row.get("phase") == "all-gather")
                if i in completed or (int(row["src"]), int(row["dst"]), step, int(row["bytes"])) != (
                        t.src, t.dst, t.step, t.size):
                    raise ValueError(f"unexpected transfer {i}")
                if profile == "D" and (int(row["sport"]), int(row["dport"])) != (t.sport, t.dport):
                    raise ValueError(f"unexpected tuple on transfer {i}")
                completed[i] = tuple(int(row[k]) for k in
                                     ("start_ns", "completion_ns", "sport", "dport"))
            elif kind == "FIGURE4_ROUTE":
                row = fields(line)
                path = routes.setdefault(row["flow_id"], {})
                if path.setdefault(row["switch"], row["port"]) != row["port"]:
                    raise ValueError("path changed during a flow")
            elif kind == "FIGURE4_RAIL_PORT":
                ports.append({k: int(v) for k, v in fields(line).items()})
            elif kind == "FIGURE4_PORT_SAMPLE":
                row = {k: int(v) for k, v in fields(line).items()}
                port_samples[row["node"], row["port"]] = row
            elif kind == "FIGURE4_FLOW_SAMPLE":
                row = fields(line)
                writer.writerow(row[key] for key in ("flow_id", "bin_ms", "bytes", "packets",
                                                      "rate_sum", "min_rate", "max_rate"))

    expected = set(range(len(transfers)))
    if set(completed) != expected or set(map(int, routes)) != expected:
        raise ValueError("incomplete transfer or route trace")
    origin = min(row[0] for row in completed.values())
    cct = max(row[1] for row in completed.values()) - origin
    changes = {}
    flow_by_step = {(t.src, t.rail, t.step): i for i, t in enumerate(transfers)}
    predecessors = {(t.dst, t.rail, t.step): i for i, t in enumerate(transfers)}
    for i, t in enumerate(transfers):
        start, end, *_ = completed[i]
        if profile == "D":
            ready = [completed[j][1] for j in t.dependencies]
            if t.issue_after >= 0:
                ready.append(completed[t.issue_after][0])
        elif t.step:
            ready = [completed[predecessors[t.src, t.rail, t.step-1]][1],
                     completed[flow_by_step[t.src, t.rail, t.step-1]][0]]
        else:
            ready = []
        if start != max(ready, default=origin) or end <= start:
            raise ValueError(f"transfer {i} violates rank-local readiness")
        for when, delta in ((start, 1), (end, -1)):
            changes.setdefault(when-origin, Counter())[t.step] += delta

    timeline, active, peak, area, previous = [], Counter(), 0, 0, 0
    for when, deltas in sorted(changes.items()):
        area += (when-previous) * sum(v > 0 for v in active.values())
        active.update(deltas)
        n = sum(v > 0 for v in active.values())
        peak = max(peak, n)
        if not timeline or timeline[-1][1] != n:
            timeline.append((when, n))
        previous = when

    _, edges = topology(profile, uplink_gbps)
    active_switch_ports = {(p["node"], p["port"]) for p in ports
                           if p["node"] >= 32 and p["bytes"] > 0}
    if set(port_samples) != active_switch_ports:
        raise ValueError("missing switch egress telemetry")
    expected_nics = {(t.src, t.rail+1) for t in transfers}
    actual_nics = {(p["node"], p["port"]) for p in ports
                   if p["node"] < 32 and p["data"] and p["feedback"]}
    if actual_nics != expected_nics:
        raise ValueError("used NICs differ from the workload")
    for port in ports:
        key = port["node"], port["port"]
        port["utilization"] = port["bytes"]*8 / (edges[key][1]*cct)
        if key in port_samples:
            port.update(port_samples[key])
            port["queue_mean_at_tx_bytes"] = port["queue_sum"] / max(1, port["packets"])
    accounting = analyze(profile, chunk, uplink_gbps, routes, ports)
    return dict(cct_ns=cct, peak_steps=peak, mean_steps=area/cct, timeline=timeline,
                queue_marks=sum(p["queue_marks"] for p in port_samples.values()),
                symphony_marks=sum(p["symphony_marks"] for p in port_samples.values()),
                both_marks=sum(p["both_marks"] for p in port_samples.values()),
                ports=ports, routes=routes,
                flow_tuples={str(i): list(row[2:]) for i,row in completed.items()}, workload=accounting)


def run_pair(args, profile, seed):
    name = f"E-{args.uplink_gbps}G" if profile == "E" else profile
    root = args.output / f"bytes-{args.chunk}" / name / f"seed-{seed}"
    root.mkdir(parents=True, exist_ok=True)
    topology_source, _ = topology(profile, args.uplink_gbps)
    (root / "topology.txt").write_text(topology_source)
    transfer_source = manifest_text(workload(profile, args.chunk)[1]) if profile == "D" else ""
    if transfer_source:
        (root / "transfers.txt").write_text(transfer_source)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        x for x in (str(args.build), env.get("LD_LIBRARY_PATH")) if x)
    summaries = []
    modes = ("off", "on", "egress") if args.symphony_scope else ("off", "on")
    for mode in modes:
        folder = root / mode
        folder.mkdir(exist_ok=True)
        config = folder / "scenario.txt"
        effective = scenario_text(profile, args.chunk, seed, mode, folder, args.uplink_gbps,
                                  args.symphony_scope)
        digest = hashlib.sha256((effective + topology_source + transfer_source).encode()).hexdigest()
        summary = folder / "summary.json"
        if summary.exists():
            result = json.loads(summary.read_text())
            if result["input_hash"] != digest:
                raise ValueError(f"cached inputs differ in {folder}")
        else:
            config.write_text(effective)
            command = [str(args.build / "scratch/network-load-balance"), str(config)]
            log = folder / "collective.log"
            (folder / "rerun.sh").write_text(
                f"#!/bin/sh\ncd {shlex.quote(str(ROOT))}\n"
                f"LD_LIBRARY_PATH={shlex.quote(env['LD_LIBRARY_PATH'])} {shlex.join(command)}"
                f" > {shlex.quote(str(log))} 2>&1\n")
            start = time.monotonic()
            with log.open("w") as stream:
                try:
                    subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, check=True)
                except subprocess.CalledProcessError as error:
                    raise RuntimeError(f"see {log}") from error
            result = inspect(folder, profile, args.chunk, args.uplink_gbps)
            result.update(input_hash=digest, profile=name, seed=seed, mode=mode,
                          wall_seconds=time.monotonic()-start)
            temporary = summary.with_suffix(".tmp")
            temporary.write_text(json.dumps(result))
            temporary.replace(summary)
        summaries.append(result)
        print(profile, seed, mode, f"CCT {result['cct_ns']/1e6:.3f} ms",
              f"peak {result['peak_steps']}", flush=True)
    a, *others = summaries
    if any(a["routes"] != b["routes"] or a["flow_tuples"] != b["flow_tuples"] for b in others):
        raise ValueError(f"matched routes or tuples differ in {root}")
    return summaries


def plot_and_report(folder, results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from statistics import mean

    results.sort(key=lambda r: (r["profile"], r["seed"], r["mode"]))
    rows = []
    for r in results:
        w = r["workload"]
        row = {key: r[key] for key in ("profile", "seed", "mode", "cct_ns", "peak_steps",
                                       "mean_steps", "queue_marks", "symphony_marks", "both_marks")}
        row.update(w["bounds"])
        row["cross_rack_payload_bytes"] = w["cross_rack_payload_bytes"]
        row["cct_to_fixed_wire_floor"] = r["cct_ns"]/1e9 / w["bounds"]["fixed_route_observed_wire_seconds"]
        fabric_floor = w["bounds"]["fabric_observed_wire_seconds"]
        row["cct_to_fabric_wire_floor"] = (r["cct_ns"]/1e9) / fabric_floor if fabric_floor else None
        row["fabric_wire_utilization"] = fabric_floor / (r["cct_ns"]/1e9)
        row["max_device_egress_queue_bytes"] = max(p.get("queue_max", 0) for p in r["ports"])
        rows.append(row)
    with (folder / "comparison.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    profiles = sorted({r["profile"] for r in results})
    treatments = [("off", "DCQCN"), ("on", "Original Symphony")]
    if any(r["mode"] == "egress" for r in results):
        treatments.append(("egress", "Egress-aware Symphony"))
    width = .75 / len(treatments)
    figure, axes = plt.subplots(1, 3, figsize=(12, 4))
    metrics = (("cct_ns", 1e6, "CCT (ms)"), ("mean_steps", 1, "Mean active steps"),
               ("fabric_wire_utilization", 0.01, "Mean fabric utilization (%)"))
    for axis, (key, scale, label) in zip(axes, metrics):
        axis.set_xticks(range(len(profiles)), profiles)
        axis.set_ylabel(label)
        for index, (mode, legend) in enumerate(treatments):
            offset = (index - (len(treatments)-1)/2) * width
            selected = [[r[key]/scale for r in rows if r["profile"] == p and r["mode"] == mode]
                        for p in profiles]
            positions = [i+offset for i in range(len(profiles))]
            axis.bar(positions, [mean(values) for values in selected], width=width, label=legend)
            for position, values in zip(positions, selected):
                axis.scatter([position]*len(values), values, s=12, c="black", zorder=4)
    axes[0].legend()
    figure.tight_layout()
    for extension in ("png", "pdf"):
        figure.savefig(folder / f"comparison.{extension}", dpi=150)
    plt.close(figure)

    first_seed = min(r["seed"] for r in results)
    figure, axes = plt.subplots((len(profiles)+2)//3, 3, figsize=(13, 6), squeeze=False)
    for axis, profile in zip(axes.flat, profiles):
        for mode, label in treatments:
            row = next((r for r in results if r["profile"] == profile and
                        r["seed"] == first_seed and r["mode"] == mode), None)
            if row:
                time_ns, steps = zip(*row["timeline"])
                axis.step([t/1e6 for t in time_ns], steps, where="post", label=label)
        axis.set(title=profile, xlabel="Time (ms)", ylabel="Active logical steps", ylim=(0, None))
    for axis in list(axes.flat)[len(profiles):]:
        axis.set_visible(False)
    axes.flat[0].legend()
    figure.suptitle(f"Step overlap by workload, seed {first_seed}")
    figure.tight_layout()
    for extension in ("png", "pdf"):
        figure.savefig(folder / f"step-overlap.{extension}", dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk", type=int, default=256*1024)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    parser.add_argument("--seeds", nargs="+", type=int, default=[1])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--symphony-scope", action="store_true",
                        help="compare original and egress-aware marking with detailed scope telemetry")
    parser.add_argument("--uplink-gbps", type=int, choices=(20, 40, 80), default=40)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--analyze-only", action="store_true")
    action.add_argument("--plot-only", action="store_true")
    action.add_argument("--archived-summary", type=Path)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if (any(c.isspace() for c in str(args.output)) or not 0 < args.chunk or args.workers < 1 or
            any(s <= 0 for s in args.seeds) or len(set(args.seeds)) != len(args.seeds) or
            len(set(args.profiles)) != len(args.profiles)):
        parser.error("use distinct profiles, positive distinct seeds and a whitespace-free output path")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.archived_summary:
        saved = json.loads(args.archived_summary.read_text())
        (args.output / "archived-accounting.json").write_text(
            json.dumps(analyze("A", args.chunk, routes=saved["routes"],
                               observed_ports=saved["rail_ports"]), indent=2) + "\n")
    elif args.plot_only:
        folder = args.output / f"bytes-{args.chunk}"
        results = []
        for off in sorted(folder.glob("*/seed-*/off/summary.json")):
            paths = [off.parent.parent / mode / "summary.json" for mode in
                     (("off", "on", "egress") if args.symphony_scope else ("off", "on"))]
            if all(path.exists() for path in paths):
                a, *others = [json.loads(path.read_text()) for path in paths]
                if any(a["routes"] != b["routes"] or a["flow_tuples"] != b["flow_tuples"]
                       for b in others):
                    raise ValueError(f"matched routes or tuples differ in {off.parent.parent}")
                results.extend([a, *others])
        if not results:
            parser.error("no matched runs to plot")
        plot_and_report(folder, results)
    elif args.analyze_only:
        for profile in args.profiles:
            (args.output / f"{profile}-accounting.json").write_text(
                json.dumps(analyze(profile, args.chunk, args.uplink_gbps), indent=2) + "\n")
    else:
        if args.build is None:
            parser.error("simulations require --build")
        if args.symphony_scope and args.profiles != ["D"]:
            parser.error("use --profiles D for the marking-scope diagnostic")
        args.build = args.build.resolve()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = [pool.submit(run_pair, args, p, seed) for p in args.profiles for seed in args.seeds]
            results = [r for job in jobs for r in job.result()]
        plot_and_report(args.output / f"bytes-{args.chunk}", results)


if __name__ == "__main__":
    main()
