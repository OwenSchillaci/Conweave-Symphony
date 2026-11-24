#!/usr/bin/env python3
"""Run or reanalyze the ring all-reduce report workloads."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from analyze_ring import expected_jobs, read_log
from plot_ring import plot_run


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = (
    "single_ring",
    "two_shared_leaves",
    "two_separate_leaves",
    "quarter_occupancy",
    "half_occupancy",
    "full_occupancy",
    "injected_rank_skew",
)
ROUTING = {"ecmp": 0, "conweave": 9}
OUTPUT_KEYS = {
    "FLOW_INPUT_FILE", "FCT_OUTPUT_FILE", "CNP_OUTPUT_FILE", "PFC_OUTPUT_FILE",
    "VOQ_MON_FILE", "VOQ_MON_DETAIL_FILE", "UPLINK_MON_FILE", "CONN_MON_FILE",
}


def write_config(workload, routing, output, config):
    source = Path("config/ring_report") / f"{workload}.txt"
    lines = []
    for line in source.read_text().splitlines():
        key, _, value = line.partition(" ")
        if key == "LB_MODE":
            line = f"{key} {ROUTING[routing]}"
        elif key in OUTPUT_KEYS:
            line = f"{key} {output / Path(value).name}"
        lines.append(line)
    config.write_text("\n".join(lines) + "\n")


def analyze_run(config, log, report):
    if not log.is_file():
        raise ValueError(f"missing simulator log {log}")
    with report.open("w") as stream:
        result = subprocess.run(
            [sys.executable, "analysis/analyze_ring.py", str(log), "--config", str(config)],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    if result.returncode:
        raise ValueError(f"analysis exited with status {result.returncode}; see {report}")

    _, completed = read_log(log)
    counts = expected_jobs(config)
    incomplete = []
    for job, iterations in enumerate(counts):
        finished = sum((job, iteration) in completed for iteration in range(iterations))
        if finished != iterations:
            incomplete.append(f"job {job} ({finished}/{iterations})")
    if incomplete:
        raise ValueError(f"incomplete collectives: {', '.join(incomplete)}; see {report}")
    return completed


def run_workload(workload, args):
    output = args.output_dir / workload / args.routing
    config = output / "scenario.txt"
    log = output / "collective.log"
    report = output / "analysis.txt"
    env = None

    if not args.analyze_only:
        output.mkdir(parents=True, exist_ok=True)
        write_config(workload, args.routing, output, config)
        command = ["./waf", "--run", f"scratch/network-load-balance {config}"]
        if args.built:
            command = ["build/scratch/network-load-balance", str(config)]
            env = os.environ.copy()
            library_paths = [str(ROOT / "build")]
            if env.get("LD_LIBRARY_PATH"):
                library_paths.append(env["LD_LIBRARY_PATH"])
            env["LD_LIBRARY_PATH"] = os.pathsep.join(library_paths)
        print(f"{workload}/{args.routing}: running", flush=True)
        with log.open("w") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env)
        if result.returncode:
            failure = f"simulator command exited with status {result.returncode}; see {log}"
            try:
                analyze_run(config, log, report)
            except ValueError as exc:
                failure += f"; {exc}"
            raise ValueError(failure)

    completions = analyze_run(config, log, report)
    plot_run(output, workload, args.routing, completions)
    print(f"{workload}/{args.routing}: {len(completions)} collectives complete; "
          f"report and plots in {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=(*WORKLOADS, "all"))
    parser.add_argument("--routing", choices=ROUTING, default="ecmp")
    parser.add_argument("--output-dir", type=Path, default=Path("mix/output/ring_report"),
                        help="results root, relative to the ns-3 directory or absolute")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--analyze-only", action="store_true", help="regenerate the report and plots from saved logs")
    mode.add_argument("--built", action="store_true", help="use an existing simulator build directly")
    args = parser.parse_args()
    if any(character.isspace() for character in str(args.output_dir)):
        parser.error("--output-dir cannot contain whitespace in simulator configs")

    os.chdir(ROOT)
    workloads = WORKLOADS if args.workload == "all" else (args.workload,)
    failed = False
    for workload in workloads:
        try:
            run_workload(workload, args)
        except (OSError, ValueError) as exc:
            print(f"{workload}/{args.routing}: {exc}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
