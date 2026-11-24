#!/usr/bin/env python3
"""Report timing and adjacent-step overlap from ConWeave ring workload logs."""

import argparse
from collections import defaultdict
from pathlib import Path


PHASES = {"reduce-scatter": 0, "all-gather": 3}
STEP_NAMES = ("RS0", "RS1", "RS2", "AG0", "AG1", "AG2")
OPERATIONS = {(step, rank) for step in range(6) for rank in range(4)}


def read_log(path):
    transfers = defaultdict(dict)
    completions = {}
    with path.open() as log:
        for line_number, line in enumerate(log, 1):
            fields = line.split()
            if not fields or fields[0] not in ("RING_TRANSFER", "RING_COMPLETE"):
                continue
            try:
                data = dict(field.split("=", 1) for field in fields[1:])
                key = int(data["job"]), int(data["iteration"])
                start, end = int(data["start_ns"]), int(data["completion_ns"])
                if min(key) < 0 or start < 0 or end < start:
                    raise ValueError("invalid job, iteration, or timestamps")
                if fields[0] == "RING_TRANSFER":
                    step, rank = int(data["step"]), int(data["rank"])
                    if not (0 <= step < 3 and 0 <= rank < 4):
                        raise ValueError("step or rank out of range")
                    operation = PHASES[data["phase"]] + step, rank
                    if operation in transfers[key]:
                        raise ValueError("duplicate operation")
                    transfers[key][operation] = (start, end)
                else:
                    if key in completions:
                        raise ValueError("duplicate collective completion")
                    if int(data["transfers"]) != len(OPERATIONS):
                        raise ValueError("unexpected transfer count in collective completion")
                    if int(data["duration_ns"]) != end - start:
                        raise ValueError("collective duration disagrees with timestamps")
                    completions[key] = (start, end)
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return transfers, completions


def expected_jobs(path):
    """Read the iteration counts from the simulator scenario, including its default."""
    enabled = False
    jobs = []
    with path.open() as config:
        for line_number, line in enumerate(config, 1):
            fields = line.partition("#")[0].split()
            if not fields:
                continue
            if fields[0] == "RING_ALL_REDUCE":
                if len(fields) != 2 or fields[1] not in ("0", "1"):
                    raise ValueError(f"{path}:{line_number}: expected RING_ALL_REDUCE 0 or 1")
                enabled = fields[1] == "1"
            elif fields[0] == "RING_JOB":
                if len(fields) != 6 or not fields[5].isdigit() or int(fields[5]) < 1:
                    raise ValueError(f"{path}:{line_number}: invalid RING_JOB iteration count")
                jobs.append(int(fields[5]))
    if not enabled:
        raise ValueError(f"{path}: ring workload is disabled")
    return jobs or [1]


def active_intervals(intervals):
    """Merge transfer intervals to count each nanosecond of step activity once."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], end)
        else:
            merged.append((start, end))
    return merged


def analyze(transfers, completions, expected):
    keys = transfers.keys() | completions.keys()
    if not keys and expected is None:
        raise ValueError("no ring records found")
    if expected is not None and any(
        job >= len(expected) or iteration >= expected[job] for job, iteration in keys
    ):
        raise ValueError("log contains a job or iteration outside the supplied scenario")

    job_iterations = defaultdict(dict)
    for job, iteration in sorted(keys):
        key = job, iteration
        operations = transfers.get(key, {})
        summary = completions.get(key)
        label = f"Job {job}, iteration {iteration}"
        if operations.keys() != OPERATIONS:
            if summary is not None:
                raise ValueError(f"{label}: completion record has missing transfer records")
            print(f"{label}  incomplete log ({len(operations)}/{len(OPERATIONS)} transfers)")
            continue

        start = min(s for s, _ in operations.values())
        end = max(e for _, e in operations.values())
        if summary is not None and summary != (start, end):
            raise ValueError(f"{label}: transfer timestamps disagree with completion record")
        evidence = "" if summary is not None else " (derived from transfers)"
        print(f"{label}  collective completion time {end - start} ns{evidence}")
        job_iterations[job][iteration] = (start, end)

        steps = [[operations[step, rank] for rank in range(4)] for step in range(6)]
        print("  Step  First start (ns)   Last ACK (ns)  Start skew (ns)")
        for name, intervals in zip(STEP_NAMES, steps):
            first = min(s for s, _ in intervals)
            last = max(e for _, e in intervals)
            skew = max(s for s, _ in intervals) - first
            print(f"  {name:4}  {first:16}  {last:14}  {skew:15}")

        print("  Adjacent steps  Time both active (ns)  Next-start lead (ns)")
        for index, (previous, following) in enumerate(zip(steps, steps[1:])):
            previous_active = active_intervals(previous)
            following_active = active_intervals(following)
            overlap = sum(
                max(0, min(e0, e1) - max(s0, s1))
                for s0, e0 in previous_active
                for s1, e1 in following_active
            )
            lead = max(0, max(e for _, e in previous) - min(s for s, _ in following))
            pair = f"{STEP_NAMES[index]} -> {STEP_NAMES[index + 1]}"
            print(f"  {pair:14}  {overlap:21}  {lead:20}")
        print()

    print("Jobs")
    observed = defaultdict(set)
    for job, iteration in keys:
        observed[job].add(iteration)
    jobs = range(len(expected)) if expected is not None else sorted(observed)
    for job in jobs:
        finished = job_iterations[job]
        count = expected[job] if expected is not None else max(observed[job]) + 1
        if len(finished) != count:
            scope = "configured" if expected is not None else "through last observed iteration"
            print(f"  Job {job}  incomplete ({len(finished)}/{count} iterations {scope})")
            continue
        start, end = finished[0][0], finished[count - 1][1]
        metric = "completion time" if expected is not None else "observed iteration span"
        unit = "iteration" if count == 1 else "iterations"
        print(f"  Job {job}  {metric} {end - start} ns ({count} {unit})")
    if expected is None:
        print("  Supply --config to confirm the expected jobs and iteration counts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="simulator stdout or collective.log")
    parser.add_argument("--config", type=Path, help="scenario used to generate the log")
    args = parser.parse_args()
    try:
        transfers, completions = read_log(args.log)
        analyze(transfers, completions, expected_jobs(args.config) if args.config else None)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
