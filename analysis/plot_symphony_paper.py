#!/usr/bin/env python3
"""Plot processed local Symphony data and retain the plotted points."""

import argparse
from collections import Counter, defaultdict
import csv
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from run_symphony_paper import write_csv


def read(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def save(fig, folder, name):
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(folder / f"{name}.{ext}", dpi=180)
    plt.close(fig)


def cdf(ax, values, label):
    values = sorted(values)
    ys = [(i + 1) / len(values) for i in range(len(values))]
    ax.step([values[0], *values], [0, *ys], where="post", label=label)
    ax.set_ylabel("Empirical CDF")
    ax.set_ylim(-0.02, 1.03)
    return list(zip(values, ys))


def normalized(rows, baseline, dimension):
    off = {(r["variant"], r["kib"], r["seed"], r["job"], r["iteration"]): int(r["cct_ns"])
           for r in baseline if r["mode"] == "off"}
    groups = defaultdict(list)
    for row in rows:
        key = tuple(row[k] for k in ("variant", "kib", "seed", "job", "iteration"))
        groups[row["variant"], row["mode"], float(row[dimension])].append(
            (int(row["cct_ns"]), off[key]))
    return [
        dict(variant=v, mode=m, x=x, cct_ms=mean(a for a, _ in samples) / 1e6,
             matched_baseline_ms=mean(b for _, b in samples) / 1e6,
             ratio=mean(a / b for a, b in samples), samples=len(samples))
        for (v, m, x), samples in sorted(groups.items())
    ]


def figure_9(root, folder):
    cases = [("DCQCN only", root / "figure9/0.01/baseline_throughput.csv")] + [
        (f"Fixed alpha, k = {k}", root / f"figure9/{k}/symphony_throughput.csv")
        for k in ("0.001", "0.003", "0.01")]
    fig, axes = plt.subplots(4, 1, sharex=True, sharey=True, figsize=(8.8, 8))
    summaries = []
    for ax, (label, path) in zip(axes, cases):
        data = read(path)
        x = [float(r["bin_start_ms"]) for r in data]
        for key, name in (("lagging_gbps", "Lagging"), ("leading_gbps", "Leading")):
            ax.stairs([float(r[key]) for r in data], x + [x[-1] + .1], label=name)
        role = "baseline" if label == "DCQCN only" else "symphony"
        starts, ends = {}, {}
        with (path.parent / role / "collective.log").open() as log:
            for line in log:
                kind, _, fields = line.partition(" ")
                if kind in ("TWO_FLOW_START", "TWO_FLOW_COMPLETE"):
                    f = dict(field.split("=") for field in fields.split())
                    target = starts if kind == "TWO_FLOW_START" else ends
                    target[int(f["step"])] = int(f["time_ns"])
        origin = starts[1]
        for step, color in enumerate(("C0", "C1")):
            for time, dash in ((starts[step], ":"), (ends[step], "--")):
                ax.axvline((time-origin)/1e6, color=color, linestyle=dash, alpha=.6)
        summaries.append(dict(case=label, seed=1, lagging_ms=(ends[0]-starts[0])/1e6,
                              leading_ms=(ends[1]-starts[1])/1e6,
                              overlap_ms=(min(ends.values())-max(starts.values()))/1e6,
                              both_finished_ms=(max(ends.values())-origin)/1e6))
        ax.set_title(label, loc="left")
        ax.set_ylabel("Payload Gb/s")
        ax.legend(loc="upper right")
    write_csv(root / "figure9_completion.csv", summaries[0].keys(), summaries)
    axes[-1].set_xlabel("Time from leading start (ms) · dotted: start, dashed: completion")
    fig.suptitle("Fig. 9 local comparison · ns-3 two-flow shared egress")
    save(fig, folder, "figure9")


def figure_8(root, folder, chunks):
    for panel, dim, source, xlabel in (
        ("c", "kib", chunks, "Chunk size (KiB)"),
        ("b", "gain", read(root / "gain_collectives.csv"), "Gain k"),
    ):
        rows = normalized(source, chunks, dim)
        write_csv(root / f"figure8{panel}_points.csv", rows[0].keys(), rows)
        fig, ax = plt.subplots(figsize=(7, 4))
        for variant, mode in sorted({(r["variant"], r["mode"]) for r in rows}):
            subset = [r for r in rows if r["variant"] == variant and r["mode"] == mode]
            if variant == "aligned":
                ax.plot([r["x"] for r in subset], [r["ratio"] for r in subset], "x",
                        label=f"Aligned control ({mode})")
            else:
                ax.plot([r["x"] for r in subset], [r["ratio"] for r in subset], "o-",
                        label=mode.capitalize())
        ax.axhline(1, color="0.55", linestyle="--", linewidth=1)
        ax.set_xscale("log", base=2 if panel == "c" else 10)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("CCT / matched DCQCN CCT")
        ax.set_title(f"Fig. 8({panel}) local comparison · eight-rank host-skew ring")
        ax.legend()
        save(fig, folder, f"figure8{panel}")

    jobs = read(root / "contention_collectives.csv")
    delivered = defaultdict(Counter)
    for row in read(root / "contention_egress.csv"):
        delivered[row["variant"], row["mode"], row["seed"], row["dst"], row["bin"]][row["job"]] += int(row["bytes"])
    off = {(r["variant"], r["seed"], r["job"]): int(r["jct_ns"])
           for r in jobs if r["mode"] == "off" and r["iteration"] == "0"}
    points = []
    for row in jobs:
        if row["iteration"] != "0":
            continue
        v, m, s = (row[k] for k in ("variant", "mode", "seed"))
        shared = {key[-1]: counts for key, counts in delivered.items()
                  if key[:4] == (v, m, s, "0") and len(counts) >= 2}
        rate = mean(sum(c.values()) * 8 / 100_000 for c in shared.values())
        other = mean(delivered.get((v, m, s, str(dst), bin_id), {}).get("0", 0)
                     * 8 / 100_000 for dst in range(16, 128, 16) for bin_id in shared)
        points.append(dict(variant=v, mode=m, seed=s, job=row["job"], shared_port_bins=len(shared),
                           mean_shared_payload_gbps=rate, other_primary_payload_gbps=other,
                           measured_payload_ratio=rate / other, jct_ns=row["jct_ns"],
                           ratio=int(row["jct_ns"]) / off[v, s, row["job"]]))
    write_csv(root / "figure8a_points.csv", points[0].keys(), points)
    fig, ax = plt.subplots(figsize=(7, 4))
    for variant in ("hotspot-2", "hotspot-3"):
        cdf(ax, [r["ratio"] for r in points if r["variant"] == variant and r["mode"] == "adaptive"],
            f'{variant[-1]} co-located jobs')
    ax.axvline(1, color="0.55", linestyle="--")
    ax.set_xlabel("Job time / matched DCQCN job time")
    ax.set_title("Fig. 8(a) local comparison · measured shared-egress contention")
    ax.legend()
    save(fig, folder, "figure8a")


def figure_4_5_2(root, folder):
    collective = read(root / "overlap_collectives.csv")
    timeline = read(root / "overlap_overlap.csv")
    steps = read(root / "overlap_steps.csv")
    fig, ax = plt.subplots(figsize=(7, 4))
    for mode in ("off", "adaptive"):
        points = [r for r in timeline if (r["mode"], r["seed"], r["iteration"]) == (mode, "1", "0")]
        ax.step([int(r["time_ns"]) / 1e6 for r in points],
                [int(r["active_steps"]) for r in points], where="post", label=mode)
    ax.set(xlabel="Time from collective start (ms)", ylabel="Active logical steps",
           title="Fig. 4(a) local comparison · seed 1, iteration 0")
    ax.legend()
    save(fig, folder, "figure4a")
    for name, key, scale, units in (
        ("figure4b", "max_overlap", 1, "Maximum active steps"),
        ("figure5", "cct_ns", 1e6, "Collective completion time (ms)"),
    ):
        fig, ax = plt.subplots(figsize=(7, 4))
        plotted = []
        for mode in ("off", "adaptive"):
            pts = cdf(ax, [int(r[key]) / scale for r in collective if r["mode"] == mode], mode)
            plotted += [dict(mode=mode, x=x, cdf=y) for x, y in pts]
        write_csv(root / f"{name}_points.csv", plotted[0].keys(), plotted)
        ax.set_xlabel(units)
        ax.set_title(f"{name.replace('figure', 'Fig. ')} local comparison · 20 seeds × 2 collectives")
        ax.legend()
        save(fig, folder, name)

    fig, axes = plt.subplots(2, sharex=True, figsize=(7, 6))
    aligned = read(root / "motivation_steps.csv")
    reference = [int(r["completion_ns"]) for r in aligned if r["iteration"] == "0"]
    points = []
    for variant, trace, completions in (
        ("Aligned control", read(root / "motivation_overlap.csv"), aligned),
        ("Host skew", timeline, steps),
        ("Two jobs, shared receiver", [r for r in read(root / "contention_overlap.csv")
                                 if r["variant"] == "hotspot-2" and r["job"] == "0"],
                                [r for r in read(root / "contention_steps.csv")
                                 if r["variant"] == "hotspot-2" and r["job"] == "0"]),
    ):
        points_in_case = [r for r in trace if (r["mode"], r["seed"], r["iteration"]) == ("off", "1", "0")]
        axes[0].step([int(r["time_ns"]) / 1e6 for r in points_in_case],
                     [int(r["active_steps"]) for r in points_in_case], where="post", label=variant)
        ends = [int(r["completion_ns"]) for r in completions
                if (r["mode"], r["seed"], r["iteration"]) == ("off", "1", "0")]
        rates = [(reference[i] - reference[i-1]) / (ends[i] - ends[i-1])
                 for i in range(1, len(ends))]
        axes[1].plot([t / 1e6 for t in ends[1:]], rates, "o-", label=variant)
        points += [dict(variant=variant, time_ns=t, normalized_rate=rate)
                   for t, rate in zip(ends[1:], rates)]
    write_csv(root / "figure2_rate_points.csv", points[0].keys(), points)
    axes[0].set_ylabel("Active logical steps")
    axes[1].set_ylabel("Step rate / aligned step rate")
    axes[1].set_xlabel("Time from collective start (ms)")
    axes[0].legend()
    fig.suptitle("Fig. 2 local comparison · aligned, host-skew and co-located rings")
    save(fig, folder, "figure2")


def figure_7(root, folder):
    trace = read(root / "tenants_egress.csv")
    collectives = read(root / "tenants_collectives.csv")
    fig, axes = plt.subplots(2, sharex=True, sharey=True, figsize=(8, 6))
    points = []
    for mode, ax in zip(("off", "adaptive"), axes):
        series = defaultdict(Counter)
        for r in trace:
            if r["mode"] == mode and r["seed"] == "1":
                series[int(r["job"])][int(r["bin"]) - 20_000] += int(r["bytes"])
        bins = list(range(max(max(s) for s in series.values()) + 1))
        for job, counts in sorted(series.items()):
            ax.step([b / 10 for b in bins], [counts[b] * 8 / 100_000 for b in bins],
                    where="post", label=f"Job {job}")
        totals = [sum(counts[b] for counts in series.values()) * 8 / 100_000 for b in bins]
        ax.step([b / 10 for b in bins], totals, where="post", label="Total", color="black")
        points += [dict(mode=mode, time_ms=b/10, job0_gbps=series[0][b]*8/100_000,
                        job1_gbps=series[1][b]*8/100_000, total_gbps=total)
                   for b, total in zip(bins, totals)]
        ax.set(title=mode, ylabel="Receiver-egress payload Gb/s")
        ax.legend()
    write_csv(root / "figure7a_points.csv", points[0].keys(), points)
    axes[-1].set_xlabel("Time from job 0 start (ms)")
    fig.suptitle("Fig. 7(a) local comparison · staggered co-located jobs")
    save(fig, folder, "figure7a")
    fig, ax = plt.subplots(figsize=(7, 4))
    points = []
    for mode in ("off", "adaptive"):
        pts = cdf(ax, [int(r["final_span_ns"]) / 1e6 for r in collectives if r["mode"] == mode], mode)
        points += [dict(mode=mode, span_ms=x, cdf=y) for x, y in pts]
    write_csv(root / "figure7b_points.csv", points[0].keys(), points)
    ax.set(xlabel="Last-step ACK completion span (ms)",
           title="Fig. 7(b) local comparison · 3 seeds × 2 jobs × 2 collectives")
    ax.legend()
    save(fig, folder, "figure7b")


def figure_7c(root, folder):
    jobs = [r for r in read(root / "scales_collectives.csv") if r["iteration"] == "0"]
    baselines = {(r["seed"], r["ranks"]): int(r["jct_ns"]) for r in jobs if r["mode"] == "off"}
    ratios = [dict(seed=r["seed"], ranks=r["ranks"], jct_ns=r["jct_ns"],
                   baseline_ns=baselines[r["seed"], r["ranks"]],
                   ratio=int(r["jct_ns"]) / baselines[r["seed"], r["ranks"]])
              for r in jobs if r["mode"] == "adaptive"]
    write_csv(root / "figure7c_points.csv", ratios[0].keys(), ratios)
    fig, ax = plt.subplots(figsize=(7, 4))
    for ranks in ("16", "32", "64"):
        cdf(ax, [r["ratio"] for r in ratios if r["ranks"] == ranks], f"{ranks} ranks")
    ax.axvline(1, color="0.55", linestyle="--")
    ax.set(xlabel="Job time / matched DCQCN job time",
           title="Fig. 7(c) local comparison · three overlapping ring sizes, 3 seeds")
    ax.legend()
    save(fig, folder, "figure7c")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    folder = args.output / "figures"
    folder.mkdir(exist_ok=True)
    figure_9(args.output, folder)
    figure_8(args.output, folder, read(args.output / "chunks_collectives.csv"))
    figure_4_5_2(args.output, folder)
    figure_7(args.output, folder)
    figure_7c(args.output, folder)


if __name__ == "__main__":
    main()
