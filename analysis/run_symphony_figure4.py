#!/usr/bin/env python3
"""Run an additive reconstruction of Symphony Figure 4."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import gzip
import json
import os
from pathlib import Path
import shlex
import shutil
from statistics import median
import subprocess
import time

from analyze_ring import read_log
from run_ring_suite import OUTPUT_KEYS, ROOT
from run_symphony_paper import overlap_timeline


def fields(line):
    return dict(item.split("=", 1) for item in line.split()[1:])


def contention_witness(record, operations):
    if record["queue_bytes"] < 50_000:
        return None
    a, b = (record[k] for k in ("flow_a", "flow_b"))
    a, b = sorted((a, b), key=lambda flow_id: flow_id % 62)
    step_a, step_b = a % 62, b % 62
    channel, rank = a // 1984, (a % 1984) // 62
    global_rank_a = 32 * channel + rank
    global_rank_b = 32 * (b // 1984) + (b % 1984) // 62
    next_rank = (rank + 1) % 32
    successor = operations.get((step_a + 1, 32 * channel + next_rank))
    first, second = operations[step_a, global_rank_a], operations[step_b, global_rank_b]
    if (step_a < step_b and successor and successor[0] == first[1] and
            max(first[0], second[0]) < min(first[1], second[1])):
        return dict(bin=record, earlier=str(a), later=str(b), dependent_step=step_a+1,
                    dependent_rank=next_rank, dependent_start_ns=successor[0])
    return None


def inspect(log, chunk, mode):
    transfers, completions, ranks = read_log(log)
    if (set(completions) != {(ch, 0) for ch in range(4)} or
            ranks != dict.fromkeys(range(4), 32) or
            any(len(transfers[ch, 0]) != 1984 for ch in range(4))):
        raise ValueError(f"Incomplete collective in {log}")
    origin = min(t[0] for t in completions.values())
    end = max(t[1] for t in completions.values())
    operations = {(step, ch * 32 + rank): times
                  for (ch, _), steps in transfers.items()
                  for (step, rank), times in steps.items()}
    # All four channels share the same 62 logical steps, each with 128 transfers.
    events, _ = overlap_timeline(operations, 128, origin, step_count=62)
    routes, flow, last = defaultdict(dict), {}, {}
    rail_ports = []
    packet_marks, feedback, cnps = set(), {}, set()
    corrections, first, activation = [], [], []
    marks = peak_egress = 0
    witness = None
    with log.open() as stream:
        for line in stream:
            kind = line.split(" ", 1)[0]
            if kind == "RING_TRANSFER":
                f = fields(line)
                assert int(f["bytes"]) == chunk and f["collective_id"] == "0"
                f["logical_step"] = int(f["step"]) + (31 if f["phase"] == "all-gather" else 0)
                flow[f["flow_id"]] = f
            elif kind == "FIGURE4_RAIL_PORT":
                rail_ports.append({k: int(v) for k, v in fields(line).items()})
            elif kind == "FIGURE4_ROUTE":
                f = fields(line)
                path = routes[f["flow_id"]]
                assert path.setdefault(f["switch"], f["port"]) == f["port"]
            elif kind == "FIGURE4_EGRESS":
                record = {k: int(v) for k, v in fields(line).items()}
                peak_egress = max(peak_egress, bin(record["steps"]).count("1"))
                if witness is None:
                    witness = contention_witness(record, operations)
            elif kind == "FIGURE4_ACTIVATE":
                activation.append(fields(line))
            elif kind == "FIGURE4_FIRST_MARK":
                first.append(int(fields(line)["time_ns"]))
            elif kind == "FIGURE4_MARKS":
                marks += int(fields(line)["count"])
            elif kind == "SYMPHONY_DEQUEUE":
                f = fields(line)
                assert f["collective_id"] == "0"
                if f["mark"] in ("symphony", "both"):
                    packet_marks.add(f["packet_uid"])
                    assert mode != "late" or int(f["time_ns"]) >= origin + 500_000_000
                switch = f["switch"]
                if f["last"] == "1":
                    last[switch] = f
                elif (int(f["step"]) < int(f["previous_step_min"]) and switch in last and
                      f["step"] == f["step_min"] and last[switch]["step_min"] == f["previous_step_min"] and
                      int(last[switch]["flow_id"]) // 1984 != int(f["flow_id"]) // 1984 and
                      int(f["psn_rec"]) == int(f["psn"])):
                    if not corrections:
                        corrections.append({"advance": last[switch], "correction": f})
            elif kind == "SYMPHONY_FEEDBACK":
                f = fields(line)
                feedback[f["packet_uid"]] = f["ack_uid"]
            elif kind == "SYMPHONY_CNP":
                cnps.add(fields(line)["ack_uid"])

    if routes and (set(routes) != set(flow) or any(len(path) != 3 for path in routes.values())):
        raise ValueError("Representative route trace does not cover every flow")
    if mode == "off" and (marks or first):
        raise ValueError("Symphony marked baseline traffic")
    if mode == "late" and (len(activation) != 1 or
                            int(activation[0]["time_ns"]) != origin + 500_000_000 or
                            int(activation[0]["active_channels"]) == 0 or
                            (first and min(first) < origin + 500_000_000)):
        raise ValueError("Invalid late-start activation")
    if witness:
        for name in ("earlier", "later"):
            witness[name] = flow[witness[name]]

    return dict(cct_ns=end-origin, peak_steps=max(n for _, n in events),
                peak_egress_steps=(peak_egress or 1) if routes else None,
                marks=marks, traced_feedback=sum(feedback.get(uid) in cnps for uid in packet_marks),
                first_mark_ns=min(first)-origin if first else None,
                activation=activation, correction=corrections, witness=witness,
                routes=routes, timeline=events, rail_ports=rail_ports)


def run(args, seed, mode):
    folder = args.output / f"bytes-{args.chunk}" / f"seed-{seed}" / mode
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "scenario.txt"
    detailed = seed == 1 or args.detailed
    options = dict(RING_CHUNK_BYTES=args.chunk, RANDOM_SEED=seed,
                   RING_FIGURE4=2 if detailed else 1, RING_FIGURE4_LATE=int(mode == "late"),
                   SYMPHONY_MODE=0 if mode == "off" else 2)
    if args.dedicated_rails:
        options["TOPOLOGY_FILE"] = "config/symphony_figure4_dedicated_topology.txt"
    lines = ["RING_DEDICATED_RAILS 1"] if args.dedicated_rails else []
    for line in (ROOT / "config/symphony_figure4_scenario.txt").read_text().splitlines():
        key, _, value = line.partition(" ")
        if key in options:
            line = f"{key} {options[key]}"
        elif key in OUTPUT_KEYS:
            line = f"{key} {folder / Path(value).name}"
        lines.append(line)
    effective = "\n".join(lines) + "\n"
    summary = folder / "summary.json"
    if summary.exists():
        if config.read_text() != effective:
            raise ValueError(f"Cached configuration differs at {config}")
        return json.loads(summary.read_text())
    config.write_text(effective)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        x for x in (str(args.build), env.get("LD_LIBRARY_PATH")) if x)
    command = [str(args.build / "scratch/network-load-balance"), str(config)]
    log = folder / "collective.log"
    archive = log.with_suffix(".log.gz")
    (folder / "rerun.sh").write_text(
        f"#!/bin/sh\ncd {shlex.quote(str(ROOT))}\n"
        f"LD_LIBRARY_PATH={shlex.quote(env['LD_LIBRARY_PATH'])} {shlex.join(command)}"
        f" > {shlex.quote(str(log))} 2>&1 && gzip -f {shlex.quote(str(log))}\n")
    start = time.perf_counter()
    with log.open("w") as stream:
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
    resource = dict(wall_seconds=time.perf_counter()-start, peak_rss_kib=usage.ru_maxrss,
                    cpu_seconds=usage.ru_utime + usage.ru_stime, log_bytes=log.stat().st_size)
    (folder / "resources.json").write_text(json.dumps(resource, indent=2) + "\n")
    if proc.returncode:
        raise RuntimeError(f"Simulator failed. See {log}")
    result = inspect(log, args.chunk, mode)
    if args.dedicated_rails:
        active = [port for port in result["rail_ports"] if port["data"] and port["feedback"]]
        if len(active) != 256:
            raise ValueError("Expected data and feedback on all 256 access-link endpoints")
    for port in result["rail_ports"]:
        port["utilization"] = port["bytes"] * 8 / (10 * result["cct_ns"])
    result["rail_layout"] = "dedicated" if args.dedicated_rails else "shared"
    with log.open("rb") as src, gzip.open(archive, "wb", compresslevel=2) as dst:
        shutil.copyfileobj(src, dst)
    resource["gzip_bytes"] = archive.stat().st_size
    result.update(resource, seed=seed, mode=mode)
    pending = summary.with_suffix(".tmp")
    pending.write_text(json.dumps(result, indent=2) + "\n")
    pending.replace(summary)
    log.unlink()
    print(f"seed={seed} mode={mode} CCT={result['cct_ns']/1e6:.3f} ms "
          f"peak={result['peak_steps']} wall={resource['wall_seconds']:.1f} s", flush=True)
    return result


def plot(output, chunk, dedicated_rails=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder = output / f"bytes-{chunk}"
    runs = {(r["seed"], r["mode"]): r for filename in folder.glob("seed-*/*/summary.json")
            for r in [json.loads(filename.read_text())]}
    paired = sorted(s for s, m in runs if m == "off" and (s, "on") in runs)
    if (1, "off") in runs:
        for mode in ("on", "late"):
            if (1, mode) in runs and runs[1, "off"]["routes"] != runs[1, mode]["routes"]:
                raise ValueError(f"Seed 1 routes differ between off and {mode}")
    with (folder / "collectives.csv").open("w", newline="") as stream:
        columns = ("seed", "mode", "cct_ns", "peak_steps", "peak_egress_steps", "marks",
                   "first_mark_ns", "traced_feedback", "wall_seconds", "peak_rss_kib", "log_bytes",
                   "gzip_bytes", "cpu_seconds")
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(runs[k] for k in sorted(runs))
    labels = {"off": "DCQCN", "on": "Adaptive Symphony", "late": "Late start"}
    if (1, "off") in runs:
        fig, ax = plt.subplots(figsize=(7, 4))
        with (folder / "figure4a_points.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("mode", "time_ns", "active_steps"))
            for mode in labels:
                if (1, mode) in runs:
                    t, steps = zip(*runs[1, mode]["timeline"])
                    writer.writerows((mode, x, y) for x, y in zip(t, steps))
                    ax.step([x / 1e6 for x in t], steps, where="post", label=labels[mode])
            floor_ns = round((1 if dedicated_rails else 4) * 62 * chunk * 8 / 10_000_000_000 * 1e9)
            writer.writerows([("payload_floor", 0, 1), ("payload_floor", floor_ns, 1)])
            ax.plot([0, floor_ns / 1e6], [1, 1], "--", label="Dedicated NIC payload floor" if dedicated_rails
                    else "Shared NIC payload floor")
        ax.set(xlabel="Time from collective start (ms)", ylabel="Active logical steps",
               title="Figure 4(a) reconstruction · seed 1")
        ax.legend()
        save(fig, folder / "figure4a")
    if paired:
        with (folder / "paired_cct.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("seed", "off_ns", "on_ns", "on_to_off"))
            for seed in paired:
                off, on = (runs[seed, mode]["cct_ns"] for mode in ("off", "on"))
                writer.writerow((seed, off, on, on / off))
        fig, ax = plt.subplots(figsize=(7, 4))
        with (folder / "figure4b_points.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("mode", "seed", "max_steps", "cdf"))
            for mode in ("off", "on"):
                data = sorted((runs[s, mode]["peak_steps"], s) for s in paired)
                x, _ = zip(*data)
                cdf = [(i + 1) / len(data) for i in range(len(data))]
                writer.writerows((mode, s, n, y) for (n, s), y in zip(data, cdf))
                ax.step([x[0]-0.25, *x, x[-1]+0.25], [0, *cdf, 1], where="post", label=labels[mode])
                print(f"{mode}: median CCT {median(runs[s, mode]['cct_ns'] for s in paired)/1e6:.3f} ms")
        ax.set(xlabel="Maximum active logical steps", ylabel="Empirical CDF",
               title=f"Figure 4(b) reconstruction · {len(paired)} matched seeds")
        ax.legend()
        save(fig, folder / "figure4b")


def save(fig, path):
    import matplotlib.pyplot as plt

    fig.tight_layout()
    for ext in (".pdf", ".png"):
        fig.savefig(path.with_suffix(ext), dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk", type=int, default=8_000_000)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1])
    parser.add_argument("--modes", nargs="+", choices=("off", "on", "late"), default=["off", "on"])
    parser.add_argument("--dedicated-rails", action="store_true")
    parser.add_argument("--detailed", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--plot-only", action="store_true")
    action.add_argument("--run-only", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.dedicated_rails:
        args.output /= "dedicated-rails"
    if not 0 < args.chunk <= 2**32 - 1 or any(seed <= 0 for seed in args.seeds):
        parser.error("chunk and seeds must be positive; chunk is a 32-bit byte count")
    if len(set(args.seeds)) != len(args.seeds) or len(set(args.modes)) != len(args.modes):
        parser.error("seeds and modes must each be unique")
    if args.workers < 1:
        parser.error("workers must be positive")
    if any(char.isspace() for char in str(args.output)):
        parser.error("output path cannot contain whitespace")
    if args.plot_only:
        plot(args.output, args.chunk, args.dedicated_rails)
        return
    if args.build is None:
        parser.error("--build is required unless using --plot-only")
    args.build = args.build.resolve()
    routes = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(run, args, seed, mode) for seed in args.seeds for mode in args.modes]
        for task in as_completed(pending):
            result = task.result()
            if result["routes"]:
                expected = routes.setdefault(result["seed"], result["routes"])
                if result["routes"] != expected:
                    raise ValueError("Matched flows changed paths")
            if not args.run_only:
                plot(args.output, args.chunk, args.dedicated_rails)

if __name__ == "__main__":
    main()
