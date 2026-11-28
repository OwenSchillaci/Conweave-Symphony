#!/usr/bin/env python3
"""Analyze the fixed Profile F seed 1–10 matrix and matched trace follow-up."""

import argparse
from collections import defaultdict
from pathlib import Path
from statistics import median, stdev

import numpy as np

from analyze_profile_f_pilot import analyze_case, read_csv, records, same_behavior
from run_profile_f import BIN_NS, SCENARIOS, TREATMENTS, write_csv
from run_ring_suite import OUTPUT_KEYS

SHARED = "conweave-symphony"
SEPARATED = "conweave-symphony-separated"
MODES = tuple(TREATMENTS)
SEEDS = range(1, 11)
CONTRASTS = [(v, r) for v in (SHARED, SEPARATED) for r in ("dcqcn", "symphony", "conweave")]
CONTRASTS.append((SEPARATED, SHARED))
WINDOW_NS = 500_000
LABELS = dict(zip(MODES, ("DCQCN", "Symphony", "ConWeave", "Shared CE", "Separated")))
COLORS = dict(zip(MODES, ("#657384", "#8064a2", "#0072b2", "#d55e00", "#009e73")))
METRICS = (
    "cct_ns", "peak_steps", "mean_steps", "notify", "notify_queue_only", "notify_symphony_only",
    "notify_both", "exclusions", "forwarded_reroutes", "hot_to_alternate",
    "hot_uplink_approx_wire_utilization", "alternate_uplink_approx_wire_utilization",
    "queue_marks", "hot_marks", "hot_queue_peak_bytes", "symphony_marks",
    "suppressed_symphony_ce", "voq_packets", "voq_median_residence_ns", "voq_max_ns",
    "voq_timer_flushes", "reply_timeouts", "forwarded_timeout_reroutes", "timeout_reroute_fraction",
    "prior_epoch_packets", "receiver_ooo_notifications",
    "pfc_events", "collective_cnps", "collective_rate_cuts", "collective_recovery_events",
    "median_last_cnp_to_ack_ns", "flows_with_post_cnp_recovery",
)


def numeric_summary(values):
    q1, q3 = np.percentile(values, [25, 75])
    return {"n": len(values), "mean": sum(values) / len(values), "median": median(values),
            "sd": stdev(values), "q1": float(q1), "q3": float(q3),
            "min": min(values), "max": max(values)}


def paired_results(runs):
    rng = np.random.default_rng(20261127)
    samples, estimates = [], []
    # Same seed-index resamples across contrasts preserve each paired workload.
    draws = rng.integers(0, len(SEEDS), size=(20_000, len(SEEDS)))
    for scenario in SCENARIOS:
        for variant, baseline in CONTRASTS:
            improvements = []
            for seed in SEEDS:
                a, b = (runs[scenario, seed, mode]["cct_ns"] for mode in (variant, baseline))
                improvement = 100 * (1 - a / b)
                improvements.append(improvement)
                samples.append(dict(scenario=scenario, seed=seed, variant=variant, baseline=baseline,
                                    variant_cct_ms=a / 1e6, baseline_cct_ms=b / 1e6,
                                    improvement_pct=improvement))
            distribution = np.median(np.asarray(improvements)[draws], axis=1)
            low, high = np.percentile(distribution, [2.5, 97.5])
            estimates.append(dict(scenario=scenario, variant=variant, baseline=baseline,
                                  **numeric_summary(improvements), median_ci95_low=float(low),
                                  median_ci95_high=float(high),
                                  wins=sum(v > 0 for v in improvements),
                                  losses=sum(v < 0 for v in improvements)))
    return samples, estimates


def check_config(folder, reference):
    def setup(run):
        return {k: v for k, v in (line.split(None, 1) for line in
                                  (run / "scenario.txt").read_text().splitlines())
                if k not in OUTPUT_KEYS | {"RANDOM_SEED", "PROFILE_F_BACKGROUND"}}
    if setup(folder) != setup(reference):
        raise ValueError(f"settings differ from seed 1: {folder}")
    if (folder.parent / "background.txt").read_bytes() != (reference.parent / "background.txt").read_bytes():
        raise ValueError(f"background offers differ from seed 1: {folder}")


def suppressed_epochs(folder):
    epochs = defaultdict(int)
    for kind, r in records(folder / "collective.log", {"PROFILE_F_COMPACT_SYNC", "CW_PROV_RX"}):
        if kind == "CW_PROV_RX":
            if r["ecn"] == 3 and r["causes"] == 2 and not r["old_epoch"]:
                epochs[r["time_ns"] // WINDOW_NS * WINDOW_NS, r["switch"], r["path"]] += 1
        else:
            epochs[r["start_ns"] // WINDOW_NS * WINDOW_NS, r["destination_tor"], r["path"]] += r["packets"]
    return epochs


def route_windows(separated_folder, shared_folder, separated, shared):
    bins, reroutes_by_mode = [], []
    peers = {}
    for folder in (separated_folder, shared_folder):
        rows = read_csv(folder / "ports.csv")
        bins.append({(r["node"], r["port"], r["start_ns"]): r for r in rows})
        peers.update({(r["node"], r["port"]): r["peer"] for r in rows})
        by_path = defaultdict(list)
        for row in read_csv(folder / "forwarded_reroutes.csv"):
            by_path[row["switch"], row["old"]].append(row["first_new_tx_ns"])
        reroutes_by_mode.append(by_path)

    epochs = suppressed_epochs(separated_folder)
    if sum(epochs.values()) != separated["suppressed_symphony_ce"]:
        raise ValueError(f"suppressed counts disagree: {separated_folder}")
    first = separated["start_ns"]
    last = first + min(separated["cct_ns"], shared["cct_ns"])
    results = []
    for (time, dst, path), count in sorted(epochs.items()):
        if time - WINDOW_NS < first or time + 2 * WINDOW_NS > last:
            continue
        tor = 25 - dst  # Profile F uses ToRs 12 and 13
        port = path & 255
        hops = ((tor, port), (peers[tor, port], (path >> 8) & 255))
        result = dict(scenario=separated["scenario"], seed=separated["seed"],
                      epoch_ns=time, source_tor=tor, destination_tor=dst, path=path,
                      suppressed_packets=count)
        for label, port_bins, reroutes in zip(("separated", "shared"), bins, reroutes_by_mode):
            for when, left in (("before", time - WINDOW_NS), ("after", time + WINDOW_NS)):
                right = left + WINDOW_NS
                samples = [port_bins.get((node, link, t), {}) for node, link in hops
                           for t in range(left, right, BIN_NS)]
                prefix = f"{label}_{when}"
                result[f"{prefix}_red"] = sum(r.get("queue_marks", 0) for r in samples)
                result[f"{prefix}_peak_queue_bytes"] = max(r.get("queue_max", 0) for r in samples)
                result[f"{prefix}_peak_bin_util"] = max(float(r.get("wire_utilization", 0)) for r in samples)
                result[f"{prefix}_reroutes_away"] = sum(
                    left <= t < right for t in reroutes[tor, path])
        results.append(result)
    return results


def physical_followups(folder, scenario):
    """Find ordered suppression, later queue mark, exclusion, and forwarded reroute."""
    events = defaultdict(list)
    names = {"CW_PROV_RX", "CW_PROV_NOTIFY", "CW_PROV_EXCLUDE", "CW_PROV_CANDIDATES",
             "CW_PROV_PATH", "CW_PROV_TX", "ECN_PROV_MARK"}
    for kind, row in records(folder / "collective.log", names):
        events[kind].append(row)
    notified = {r["notify_uid"]: r for r in events["CW_PROV_NOTIFY"]}
    excluded = {r["notify_uid"]: r for r in events["CW_PROV_EXCLUDE"]}
    paths = {r["decision_uid"]: r for r in events["CW_PROV_PATH"]}
    sends = {(r["switch"], r["flow_id"], r["epoch"], r["phase"], r["port"]): r
             for r in events["CW_PROV_TX"]}
    queue_marks, suppressed = defaultdict(list), defaultdict(list)
    for r in events["ECN_PROV_MARK"]:
        if r["local"] & 1:
            queue_marks[r["data_uid"]].append(r["time_ns"])
    for r in events["CW_PROV_RX"]:
        if r["ecn"] == 3 and r["causes"] == 2 and not r["old_epoch"]:
            suppressed[r["switch"], r["path"]].append(r)
    output = []
    for sample in events["CW_PROV_CANDIDATES"]:
        path = paths[sample["decision_uid"]]
        if sample["first_usable"] or not sample["second_usable"] or not path["changed"]:
            continue
        if path["old"] != sample["first"]:
            continue
        notify_uid = sample["first_notify_uid"]
        notification = notified[notify_uid]
        if not notification["causes"] & 1:
            continue
        sent = sends.get(tuple(path[k] for k in ("switch", "flow_id", "epoch", "phase", "new_port")))
        if sent is None or sent["path"] != path["new"] or sent["time_ns"] < path["time_ns"]:
            continue
        mark = min(t for t in queue_marks[notification["data_uid"]] if t <= notification["time_ns"])
        earlier = [r for r in suppressed[notification["switch"], notification["path"]]
                   if 0 < mark - r["time_ns"] <= WINDOW_NS]
        if earlier:
            sync = earlier[-1]
            output.append(dict(scenario=scenario, seed=5, source_tor=path["switch"],
                               old_path=path["old"], new_path=path["new"],
                               suppressed_data_uid=sync["data_uid"], suppressed_ns=sync["time_ns"],
                               queue_mark_data_uid=notification["data_uid"], queue_mark_ns=mark,
                               notify_uid=notify_uid, exclusion_ns=excluded[notify_uid]["time_ns"],
                               decision_uid=path["decision_uid"], decision_ns=path["time_ns"],
                               transmitted_uid=sent["data_uid"], transmitted_ns=sent["time_ns"]))
    return output


def figures(output, runs, effects):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "axes.spines.right": False,
                         "axes.spines.top": False, "pdf.fonttype": 42,
                         "figure.dpi": 160, "savefig.dpi": 300})
    fig, axes = plt.subplots(2, 2, figsize=(8.4, 6.4), constrained_layout=True, sharey=True)
    for scenario, ax in zip(SCENARIOS, axes.flat):
        vals = [[runs[scenario, seed, mode]["cct_ns"] / 1e6 for seed in SEEDS] for mode in MODES]
        for points in zip(*vals):
            ax.plot(range(len(MODES)), points, color="#8a96a3", linewidth=0.65, alpha=0.35)
        for i, (mode, points) in enumerate(zip(MODES, vals)):
            ax.scatter([i] * len(points), points, s=17, color=COLORS[mode], alpha=0.68)
            ax.scatter(i, median(points), s=53, marker="D", color=COLORS[mode],
                       edgecolor="white", linewidth=0.8, zorder=4)
        ax.set_title(scenario.capitalize(), loc="left", fontweight="bold")
        ax.set_xticks(range(len(MODES)), [LABELS[m] for m in MODES], rotation=18, ha="right")
        ax.grid(axis="y", alpha=0.2)
    fig.supylabel("Collective completion time (ms)")
    fig.savefig(output / "cct-by-seed.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(9.0, 6.5), constrained_layout=True, sharex=True)
    effects_labels = [f"{LABELS[v]} / {LABELS[b]}" for v, b in CONTRASTS]
    for scenario, ax in zip(SCENARIOS, axes.flat):
        group = [r for r in effects if r["scenario"] == scenario]
        for i, r in enumerate(group):
            ax.hlines(i, r["median_ci95_low"], r["median_ci95_high"], color=COLORS[r["variant"]])
            ax.scatter(r["median"], i, color=COLORS[r["variant"]], s=27, zorder=3)
        ax.axvline(0, color="#667078", linewidth=0.8, linestyle="--")
        ax.set_yticks(range(len(group)), effects_labels)
        ax.invert_yaxis()
        ax.set_title(scenario.capitalize(), loc="left", fontweight="bold")
        ax.grid(axis="x", alpha=0.2)
    fig.supxlabel("Median paired CCT improvement (%) with 95% bootstrap intervals")
    fig.savefig(output / "paired-cct-effects.pdf")
    plt.close(fig)

    panels = (("mean_steps", "Time-weighted active steps"),
              ("notify", "Path notifications"),
              ("forwarded_reroutes", "Transmitted reroutes"),
              ("voq_packets", "VOQ-buffered packets"),
              ("queue_marks", "Ordinary queue marks"),
              ("collective_rate_cuts", "Collective QP rate cuts"))
    chosen = ("conweave", SHARED, SEPARATED)
    fig, axes = plt.subplots(2, 3, figsize=(10.2, 6.0), constrained_layout=True)
    for ax, (metric, label) in zip(axes.flat, panels):
        for i, mode in enumerate(chosen):
            for j, scenario in enumerate(SCENARIOS):
                x = j + (i - 1) * 0.21
                y = [runs[scenario, seed, mode][metric] for seed in SEEDS]
                ax.scatter([x] * len(y), y, s=9, color=COLORS[mode], alpha=0.42)
                ax.scatter(x, median(y), marker="D", s=32, color=COLORS[mode],
                           edgecolor="white", linewidth=0.6, label=LABELS[mode] if j == 0 else None)
        ax.set_ylabel(label)
        ax.set_xticks(range(len(SCENARIOS)), [s.capitalize() for s in SCENARIOS], rotation=16, ha="right")
        ax.grid(axis="y", alpha=0.2)
    axes.flat[0].legend(frameon=False, fontsize=8)
    fig.savefig(output / "mechanism-comparison.pdf")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--seed-one", required=True, type=Path)
    parser.add_argument("--trace-seed-five", required=True, type=Path)
    args = parser.parse_args()
    runs, paired_paths, fabric, folders = {}, [], [], {}
    for seed in SEEDS:
        root = args.seed_one if seed == 1 else args.results / f"seed-{seed}"
        for scenario in SCENARIOS:
            for mode in MODES:
                folder = root / scenario / mode
                check_config(folder, args.seed_one / scenario / mode)
                metric, _, ports = analyze_case(folder, emit_artifacts=False)
                if (metric["scenario"], metric["treatment"], metric["seed"], metric["chunk"]) != (
                        scenario, mode, seed, 524288):
                    raise ValueError(f"unexpected run identity: {folder}")
                if mode == SEPARATED and (metric["notify_symphony_only"] or metric["exclusion_symphony_only"]):
                    raise ValueError(f"Symphony-only CE leaked to separated routing: {folder}")
                key = scenario, seed, mode
                runs[key], folders[key] = metric, folder
                fabric.extend(dict(scenario=scenario, seed=seed, treatment=mode, **r) for r in ports)
            a, b = (runs[scenario, seed, m] for m in (SHARED, SEPARATED))
            paired_paths.extend(route_windows(folders[scenario, seed, SEPARATED],
                                              folders[scenario, seed, SHARED], b, a))

    trace_rows, followups = [], []
    for scenario in ("hotspot", "both"):
        for mode in (SHARED, SEPARATED):
            folder = args.trace_seed_five / scenario / mode
            same_behavior(folder, folders[scenario, 5, mode])
            observed, cases, _ = analyze_case(folder)
            for name, expected in runs[scenario, 5, mode].items():
                if expected is not None and observed[name] != expected:
                    raise ValueError(f"compact and full tracing disagree on {name}: {folder}")
            trace_rows.extend(dict(scenario=scenario, seed=5, treatment=mode, **r) for r in cases)
            if mode == SEPARATED:
                followups.extend(physical_followups(folder, scenario))

    aggregate = []
    for scenario in SCENARIOS:
        for mode in MODES:
            for field in METRICS:
                values = [runs[scenario, seed, mode][field] for seed in SEEDS
                          if runs[scenario, seed, mode][field] is not None]
                if len(values) >= 2:
                    aggregate.append(dict(scenario=scenario, treatment=mode, metric=field,
                                          **numeric_summary(values)))
    paired, effects = paired_results(runs)
    for name, rows in (("run-summary.csv", list(runs.values())), ("aggregate.csv", aggregate),
                       ("paired-cct.csv", paired), ("paired-cct-summary.csv", effects),
                       ("fabric-ports.csv", fabric), ("suppression-windows.csv", paired_paths),
                       ("seed-5-exclusion-decisions.csv", trace_rows),
                       ("seed-5-physical-feedback-chains.csv", followups)):
        write_csv(args.results / name, rows)
    figures(args.results, runs, effects)
    print(f"Validated {len(runs)} runs, {len(paired)} paired comparisons, "
          f"{len(paired_paths)} matched suppression windows and four detailed replays")


if __name__ == "__main__":
    main()
