#!/usr/bin/env python3
"""Run matched local Symphony comparisons and retain their measurement inputs."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import os
import random
from pathlib import Path
import shlex
import shutil
import subprocess

from analyze_ring import read_log, active_intervals
from run_ring_suite import OUTPUT_KEYS, ROOT

HOSTS = "0 16 32 48 64 80 96 112"
SEEDS = (1, 2, 3)
CHUNKS = (64, 128, 512, 1024, 2048, 8192, 32768)
GAINS = (0.00001, 0.0001, 0.001, 0.003, 0.01, 0.1, 1)


def matrix(stage):
    if stage == "chunks":
        for size in CHUNKS:
            for seed in SEEDS:
                for mode in ("off", "adaptive"):
                    yield "skew", size, mode, 0.01, seed
        for seed in SEEDS:
            for mode in ("off", "adaptive"):
                yield "aligned", 64, mode, 0.01, seed
    elif stage == "gain":
        for seed in SEEDS:
            for mode in ("fixed", "adaptive"):
                for gain in GAINS:
                    yield "skew", 1024, mode, gain, seed
    elif stage == "contention":
        for seed in SEEDS:
            for mode in ("off", "adaptive"):
                for variant in ("hotspot-2", "hotspot-3"):
                    yield variant, 1024, mode, 0.01, seed
    elif stage == "overlap":
        for seed in range(1, 21):
            for mode in ("off", "adaptive"):
                yield "skew", 1024, mode, 0.01, seed
    elif stage == "motivation":
        yield "aligned", 1024, "off", 0.01, 1
    elif stage == "scales":
        for seed in SEEDS:
            for mode in ("off", "adaptive"):
                yield "scales", 64, mode, 0.01, seed
    elif stage == "tenants":
        for seed in SEEDS:
            for mode in ("off", "adaptive"):
                yield "staggered", 1024, mode, 0.01, seed


def write_csv(path, fields, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def overlap_timeline(operations, ranks, origin, step_count=None):
    # Take the union within each step before counting simultaneous steps.
    change = Counter()
    step_ends = []
    for step in range(2 * (ranks - 1) if step_count is None else step_count):
        intervals = sorted(operations[step, rank] for rank in range(ranks))
        for start, end in active_intervals(intervals):
            change[start - origin] += 1
            change[end - origin] -= 1
        step_ends.append(max(end for _, end in intervals) - origin)
    count, timeline = 0, []
    for time, delta in sorted(change.items()):
        count += delta
        if not timeline or timeline[-1][1] != count:
            timeline.append((time, count))
    assert count == 0
    return timeline, step_ends


def run(args, spec):
    variant, size, mode, gain, seed = spec
    folder = args.output / "runs" / variant / f"{size}KiB" / f"{mode}-{gain:g}" / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "scenario.txt"
    lines = []
    replacements = dict(RING_CHUNK_BYTES=size * 1024, SYMPHONY_MODE=0 if mode == "off" else 2,
                        FLOWGEN_STOP_TIME=2.75)
    for line in (ROOT / "config/ring_eight_rank.txt").read_text().splitlines():
        key, _, value = line.partition(" ")
        if key in OUTPUT_KEYS:
            line = f"{key} {folder / Path(value).name}"
        elif key in replacements:
            line = f"{key} {replacements[key]}"
        lines.append(line)
    lines += [f"RANDOM_SEED {seed}", f"SYMPHONY_ADAPTIVE_ALPHA {int(mode == 'adaptive')}",
              f"SYMPHONY_GAIN {gain}"]
    job_ranks = [8]
    if variant == "skew":
        lines.append("RING_JOB_DELAY_NS 0 0 120000 0 0 0 0 0 0")
    elif variant.startswith("hotspot") or variant == "staggered":
        job_ranks = [8] * (3 if variant == "hotspot-3" else 2)
        for job in range(1, len(job_ranks)):
            hosts = (" ".join(["0"] + [str(16 * leaf + job) for leaf in range(1, 8)])
                     if variant.startswith("hotspot") else HOSTS)
            lines.append(f"RING_JOB {hosts} 2")
        lines.append("RING_EGRESS_TRACE 1")
        if variant == "staggered":
            lines.append("RING_JOB_START_OFFSET_NS 1 500000")
    elif variant == "scales":
        job_ranks = [16, 32, 64]
        lines = [line for line in lines if not line.startswith("RING_JOB ")]
        rng = random.Random(seed)
        for job, ranks in enumerate(job_ranks):
            # Interleave hosts across the eight leaves.
            hosts = " ".join(str(16 * (rank % 8) + rank // 8) for rank in range(ranks))
            lines.append(f"RING_JOB {hosts} 2")
            lines.append(f"RING_JOB_START_OFFSET_NS {job} {rng.randrange(100_000)}")
    log = folder / "collective.log"
    archive = log.with_suffix(".log.gz")
    effective_config = "\n".join(lines) + "\n"
    if archive.exists() and config.read_text() != effective_config:
        raise RuntimeError(f"Cached run used a different scenario. See {config}")
    config.write_text(effective_config)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        path for path in (str(args.build), env.get("LD_LIBRARY_PATH")) if path
    )
    command = [str(args.build / "scratch/network-load-balance"), str(config)]
    (folder / "rerun.sh").write_text(
        "#!/bin/sh\n" + f"cd {shlex.quote(str(ROOT))}\n" +
        f"LD_LIBRARY_PATH={shlex.quote(env['LD_LIBRARY_PATH'])} {shlex.join(command)}"
        f" > {shlex.quote(str(log))} 2>&1 && gzip -f {shlex.quote(str(log))}\n"
    )
    if archive.exists():
        with gzip.open(archive, "rb") as src, log.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        with log.open("w") as stream:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Simulator failed. See {log}")
    transfers, completions, ranks = read_log(log)
    expected = {(job, iteration) for job in range(len(job_ranks)) for iteration in range(2)}
    if (set(completions) != expected or set(transfers) != expected or
            ranks != dict(enumerate(job_ranks)) or
            any(len(ops) != 2 * ranks[job] * (ranks[job] - 1)
                for (job, _), ops in transfers.items())):
        raise RuntimeError(f"Incomplete ring collectives in {log}")
    trace = Counter()
    with log.open() as stream:
        for line in stream:
            if line.startswith("RING_EGRESS "):
                f = dict(field.split("=", 1) for field in line.split()[1:])
                trace[int(f["bin"]), int(f["job"]), int(f["dst"])] += int(f["bytes"])
    if variant in ("hotspot-2", "hotspot-3", "staggered") and (not trace or
            {job for _, job, _ in trace} != set(range(len(job_ranks)))):
        raise RuntimeError(f"Missing job egress telemetry in {log}")
    if not archive.exists():
        with log.open("rb") as src, gzip.open(archive, "wb", compresslevel=2) as dst:
            shutil.copyfileobj(src, dst)
    log.unlink()
    common = dict(variant=variant, kib=size, mode=mode, gain=gain, seed=seed)
    rows, timeline, steps = [], [], []
    for job, iteration in sorted(expected):
        origin, end = completions[job, iteration]
        rank_count = ranks[job]
        events, ends = overlap_timeline(transfers[job, iteration], rank_count, origin)
        final_step = 2 * (rank_count - 1) - 1
        final = [transfers[job, iteration][final_step, rank][1] for rank in range(rank_count)]
        rows.append(dict(common, job=job, iteration=iteration, ranks=rank_count, cct_ns=end-origin,
                         max_overlap=max(count for _, count in events),
                         final_span_ns=max(final)-min(final),
                         jct_ns=completions[job, 1][1]-completions[job, 0][0]))
        timeline += [dict(common, job=job, iteration=iteration, time_ns=t, active_steps=n)
                     for t, n in events]
        steps += [dict(common, job=job, iteration=iteration, step=i, completion_ns=t)
                  for i, t in enumerate(ends)]
    egress = [dict(common, bin=b, job=j, dst=h, bytes=v)
              for (b, j, h), v in sorted(trace.items())]
    print(f"{variant} {size} KiB {mode} k={gain:g} seed={seed}", flush=True)
    return rows, timeline, steps, egress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("chunks", "gain", "contention", "overlap", "motivation", "tenants", "scales"))
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    args.build = args.build.resolve()
    args.output = args.output.resolve()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda spec: run(args, spec), matrix(args.stage)))
    for suffix, groups in zip(("collectives", "overlap", "steps", "egress"), zip(*results)):
        rows = [row for group in groups for row in group]
        if rows:
            write_csv(args.output / f"{args.stage}_{suffix}.csv", rows[0].keys(), rows)


if __name__ == "__main__":
    main()
