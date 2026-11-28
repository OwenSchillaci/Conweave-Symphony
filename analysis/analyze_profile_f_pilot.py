#!/usr/bin/env python3
"""Summarize Profile F marking-provenance pilots."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from run_profile_f import BIN_NS, SCENARIOS, TREATMENTS, write_csv

CAUSES = {1: "queue_only", 2: "symphony_only", 3: "both"}


def read_csv(path):
    with path.open() as stream:
        return [{k: int(v) if v.lstrip("-").isdigit() else v for k, v in row.items()}
                for row in csv.DictReader(stream)]


def records(path, prefixes):
    with path.open() as stream:
        for line in stream:
            name, _, rest = line.partition(" ")
            if name in prefixes:
                fields = dict(item.split("=", 1) for item in rest.split())
                yield name, {k: int(v) if v.lstrip("-").isdigit() else v
                             for k, v in fields.items()}


def same_behavior(folder, reference):
    files = ("summary.json", "ports.csv", "rates.csv", "transfers.csv", "dependency_waits.csv",
             "notifications.csv", "path_exclusions.csv", "path_updates.csv",
             "forwarded_reroutes.csv", "flow_paths.csv", "voq_residence.csv", "fct.txt",
             "cnp.txt", "pfc.txt")
    mismatches = [name for name in files
                  if (folder / name).read_bytes() != (reference / name).read_bytes()]
    if mismatches:
        raise ValueError(f"{folder} differs from {reference}: {', '.join(mismatches)}")


def recoveries(events, transfers):
    cnps, cuts, recovery = (defaultdict(list) for _ in range(3))
    for name, table in (("SYMPHONY_CNP", cnps), ("SYMPHONY_RATE", cuts),
                        ("SYMPHONY_RECOVERY", recovery)):
        for row in events[name]:
            table[row["flow_id"]].append(row)
    rows = []
    for transfer in transfers:
        flow, end = transfer["flow_id"], transfer["completion_ns"]
        feedback = [r for r in cnps[flow] if not r["finished"] and r["time_ns"] < end]
        last = feedback[-1] if feedback else None
        changes = [r for r in recovery[flow] if r["time_ns"] < end]
        after = [r for r in changes if last is not None and r["time_ns"] > last["time_ns"]]
        rows.append({
            "flow_id": flow, "completion_ns": end, "cnps": len(feedback),
            "rate_cuts": sum(r["time_ns"] < end for r in cuts[flow]),
            "recovery_events": len(changes), "recovery_events_after_last_cnp": len(after),
            "last_cnp_ns": last["time_ns"] if last else None,
            "post_cnp_ns": end - last["time_ns"] if last else None,
            "post_cnp_payload_bytes": transfer["bytes"] - last["tx_payload_bytes"] if last else None,
            "first_recovery_after_last_cnp_ns": after[0]["time_ns"] - last["time_ns"] if after else None,
            "max_recovery_bps_after_last_cnp": max((r["after_bps"] for r in after), default=None),
        })
    return rows


def voq_flushes(ordered):
    pending, flushes = {}, {}
    for name, row in ordered:
        key = row["flow_key"]
        if name == "CW_PILOT_VOQ_ARM":
            pending[key] = row
        else:
            event = (key, row["time_ns"])
            if event not in flushes:
                arm = pending.pop(key)
                if arm["time_ns"] + arm["delay_ns"] != row["time_ns"]:
                    raise ValueError(f"VOQ flush did not match its last timer arm: {row}")
                flushes[event] = {"flow_key": key, "time_ns": row["time_ns"],
                                  "reason": "tail" if arm["tail"] else "timer", "packets": 0,
                                  "max_residence_ns": 0}
            record = flushes[event]
            record["packets"] += 1
            record["max_residence_ns"] = max(record["max_residence_ns"], row["residence_ns"])
    if pending:
        raise ValueError("VOQ timer arms remained at the end of the run")
    return list(flushes.values())


def exclusion_cases(events, ports):
    sent = {row["notify_uid"]: row for row in events["CW_PROV_NOTIFY"]}
    exclusions = {row["notify_uid"]: row for row in events["CW_PROV_EXCLUDE"]}
    candidate_rows = events["CW_PROV_CANDIDATES"]
    path_rows = {row["decision_uid"]: row for row in events["CW_PROV_PATH"]}
    tx = {(r["switch"], r["flow_id"], r["epoch"], r["phase"], r["port"]): r
          for r in events["CW_PROV_TX"]}
    peer = {(r["node"], r["port"]): r["peer"] for r in ports}
    samples = {(r["node"], r["port"], r["start_ns"]): r for r in ports}
    marks = defaultdict(list)
    for row in events["ECN_PROV_MARK"]:
        marks[row["data_uid"]].append(row)
    outputs = []
    for cand in candidate_rows:
        # A rejected second draw cannot affect selection if the first is usable.
        notify = sent.get(cand["first_notify_uid"])
        if cand["first_usable"] or not notify or notify["causes"] != 2:
            continue
        path = path_rows[cand["decision_uid"]]
        key = tuple(path[k] for k in ("switch", "flow_id", "epoch", "phase", "new_port"))
        sent_new = tx.get(key) if cand["second_usable"] else None
        if sent_new and sent_new["time_ns"] < path["time_ns"]:
            sent_new = None
        sw, pid, now = cand["switch"], cand["first"], cand["time_ns"]
        port1, port2 = pid & 255, (pid >> 8) & 255
        spine = peer[sw, port1]
        egresses = ((sw, port1), (spine, port2))
        first_mark = next(r for r in marks[notify["data_uid"]]
                          if r["local"] & 2 and r["time_ns"] <= notify["time_ns"])
        windows = {"prior": now // BIN_NS * BIN_NS - BIN_NS,
                   "decision": now // BIN_NS * BIN_NS,
                   "mark": first_mark["time_ns"] // BIN_NS * BIN_NS}
        output = {
            "data_uid": notify["data_uid"], "mark_switch": first_mark["switch"],
            "mark_time_ns": first_mark["time_ns"], "notify_uid": notify["notify_uid"],
            "exclusion_until_ns": exclusions[notify["notify_uid"]]["until_ns"],
            "decision_uid": cand["decision_uid"], "decision_ns": now,
            "source_tor": sw, "excluded_path": pid, "excluded_spine": spine,
            "excluded_port": port1, "excluded_spine_port": port2,
            "old_path": path["old"], "new_path": path["new"], "flow_id": path["flow_id"],
            "second_usable": cand["second_usable"], "initial": path["initial"],
            "outcome": ("no_usable_sample" if not cand["second_usable"] else
                        "same_path" if not (path["changed"] or path["initial"]) else
                        "unconfirmed_tx" if sent_new is None else
                        "initial_forwarded" if path["initial"] else "reroute_forwarded"),
            "new_tx_ns": sent_new["time_ns"] if sent_new else None,
        }
        for window, time in windows.items():
            output[f"{window}_bin_start_ns"] = time
            for hop, (node, port) in enumerate(egresses, start=1):
                row = samples.get((node, port, time), {})
                output[f"{window}_hop{hop}_queue_peak_bytes"] = row.get("queue_max", 0)
                output[f"{window}_hop{hop}_wire_utilization"] = float(row.get("wire_utilization", 0))
                output[f"{window}_hop{hop}_queue_marks"] = row.get("queue_marks", 0)
        if sent_new is not None:
            selected_port = path["new"] & 255
            selected_spine = peer[sw, selected_port]
            selected_edges = ((sw, selected_port), (selected_spine, (path["new"] >> 8) & 255))
        else:
            selected_edges = (None, None)
        for hop, edge in enumerate(selected_edges, start=1):
            row = samples.get((*edge, windows["prior"]), {}) if edge else {}
            output[f"prior_selected_hop{hop}_queue_peak_bytes"] = row.get("queue_max", 0) if edge else None
            output[f"prior_selected_hop{hop}_wire_utilization"] = (
                float(row.get("wire_utilization", 0)) if edge else None)
            output[f"prior_selected_hop{hop}_queue_marks"] = row.get("queue_marks", 0) if edge else None
        outputs.append(output)
    return outputs


def evidence(row, window):
    return (
        sum(row[f"{window}_hop{hop}_queue_marks"] for hop in (1, 2)) > 0,
        max(row[f"{window}_hop{hop}_queue_peak_bytes"] for hop in (1, 2)) >= 50_000,
        max(row[f"{window}_hop{hop}_wire_utilization"] for hop in (1, 2)) >= 0.90,
    )


def analyze_case(folder, emit_artifacts=True):
    summary = json.loads((folder / "summary.json").read_text())
    setup = dict(line.split(None, 1) for line in (folder / "scenario.txt").read_text().splitlines())
    if setup.get("PROFILE_F_PILOT_DETAIL") != "1" or setup.get("PROFILE_F_MARK_PROVENANCE") != "1":
        raise ValueError(f"{folder} needs both provenance and pilot detail")
    events, arms_and_flushes = defaultdict(list), []
    prefixes = {"PROFILE_F_COMPACT", "ECN_PROV_MARK", "CW_PROV_RX", "CW_PROV_NOTIFY", "CW_PROV_EXCLUDE",
                "CW_PROV_CANDIDATES", "CW_PROV_PATH", "CW_PROV_TX",
                "CW_PILOT_VOQ_ARM", "PROFILE_F_VOQ",
                "SYMPHONY_CNP", "SYMPHONY_RATE", "SYMPHONY_RECOVERY"}
    for name, row in records(folder / "collective.log", prefixes):
        events[name].append(row)
        if name in ("CW_PILOT_VOQ_ARM", "PROFILE_F_VOQ"):
            arms_and_flushes.append((name, row))
    ports = read_csv(folder / "ports.csv")
    transfers = read_csv(folder / "transfers.csv")
    if len(transfers) != 112:
        raise ValueError(f"{folder} did not complete all 112 transfers")
    compact = events["PROFILE_F_COMPACT"]
    if compact:
        counts, = compact

        def compact_counts(prefix):
            return Counter({cause: counts.get(f"{prefix}_{cause}", 0) for cause in CAUSES})

        local = compact_counts("mark_events")
        cumulative = compact_counts("marked_packets")
        rx = compact_counts("dst_ce")
        notify = compact_counts("notify")
        excluded = compact_counts("exclusion")
        prior_epoch = counts.get("prior_epoch_packets", 0)
        prior_epoch_ce = counts.get("prior_epoch_ce_packets", 0)
        suppressed = counts.get("suppressed", 0)
    else:
        local = Counter(row["local"] for row in events["ECN_PROV_MARK"])
        cumulative = Counter({r["data_uid"]: r["causes"] for r in events["ECN_PROV_MARK"]}.values())
        rx = Counter(r["causes"] for r in events["CW_PROV_RX"] if r["ecn"] == 3)
        sent = {r["notify_uid"]: r["causes"] for r in events["CW_PROV_NOTIFY"]}
        notify = Counter(sent.values())
        excluded = Counter(sent[r["notify_uid"]] for r in events["CW_PROV_EXCLUDE"])
        prior_epoch = sum(r["old_epoch"] for r in events["CW_PROV_RX"])
        prior_epoch_ce = sum(r["old_epoch"] and r["ecn"] == 3 for r in events["CW_PROV_RX"])
        suppressed = sum(r["ecn"] == 3 and r["causes"] == 2 and not r["old_epoch"]
                         for r in events["CW_PROV_RX"]) if setup.get("PROFILE_F_QUEUE_ONLY_NOTIFY") == "1" else 0
    if (local[1] + local[3], local[2] + local[3], local[3]) != tuple(
            summary[k] for k in ("queue_marks", "symphony_marks", "both_marks")):
        raise ValueError(f"{folder} mark counts disagree")
    if sum(notify.values()) != summary["notify"] or sum(excluded.values()) != summary["exclusions"]:
        raise ValueError(f"{folder} notification counts disagree")
    choices = exclusion_cases(events, ports)
    forwarded = [r for r in choices if r["outcome"] in ("initial_forwarded", "reroute_forwarded")]
    outcome = Counter(r["outcome"] for r in choices)
    flushes = voq_flushes(arms_and_flushes)
    flush_reason = Counter(r["reason"] for r in flushes)
    residence = [r["residence_ns"] for r in events["PROFILE_F_VOQ"]]
    by_flow = recoveries(events, transfers)
    tails = [r["post_cnp_ns"] for r in by_flow if r["post_cnp_ns"] is not None]
    final = max(by_flow, key=lambda r: r["completion_ns"])

    counters = {f"{group}_{label}": counts[cause]
                for group, counts in (("mark_events", local), ("marked_packets", cumulative),
                                      ("dst_ce", rx), ("notify", notify), ("exclusion", excluded))
                for cause, label in CAUSES.items()}
    metrics = {k: v for k, v in summary.items() if k not in ("paths", "timeline")}
    metrics.update(counters)
    path_updates = read_csv(folder / "forwarded_reroutes.csv")
    if len(path_updates) != summary["forwarded_reroutes"]:
        raise ValueError(f"{folder} forwarded reroute records disagree")
    timeouts_forwarded = sum(r["timeout"] for r in path_updates)
    metrics["forwarded_timeout_reroutes"] = timeouts_forwarded
    metrics["timeout_reroute_fraction"] = (timeouts_forwarded / summary["reply_timeouts"]
                                           if summary["reply_timeouts"] else None)
    metrics.update({f"sym_only_{key}": None if compact else outcome[key] for key in (
        "initial_forwarded", "reroute_forwarded", "no_usable_sample", "same_path", "unconfirmed_tx")})
    for window in ("prior", "decision", "mark"):
        tests = [evidence(row, window) for row in forwarded]
        for j, label in enumerate(("red", "queue_ge_50kb", "util_ge_90pct")):
            metrics[f"sym_only_forwarded_{window}_{label}"] = None if compact else sum(t[j] for t in tests)
        metrics[f"sym_only_forwarded_{window}_red_or_queue"] = (
            None if compact else sum(t[0] or t[1] for t in tests))
    metrics.update(
        prior_epoch_packets=prior_epoch, prior_epoch_ce_packets=prior_epoch_ce,
        suppressed_symphony_ce=suppressed,
        voq_tail_flushes=flush_reason["tail"], voq_timer_flushes=flush_reason["timer"],
        voq_median_residence_ns=median(residence) if residence else None,
        collective_cnps=sum(r["cnps"] for r in by_flow),
        collective_rate_cuts=sum(r["rate_cuts"] for r in by_flow),
        collective_recovery_events=sum(r["recovery_events"] for r in by_flow),
        flows_with_cnps=len(tails),
        median_last_cnp_to_ack_ns=median(tails) if tails else None,
        median_post_cnp_payload_bytes=median(r["post_cnp_payload_bytes"] for r in by_flow
                                             if r["post_cnp_payload_bytes"] is not None) if tails else None,
        flows_with_post_cnp_recovery=sum(r["recovery_events_after_last_cnp"] > 0 for r in by_flow),
        final_flow_id=final["flow_id"], final_flow_last_cnp_to_ack_ns=final["post_cnp_ns"],
        final_flow_post_cnp_payload_bytes=final["post_cnp_payload_bytes"],
        final_flow_max_recovery_bps_after_cnp=final["max_recovery_bps_after_last_cnp"],
    )
    path_ports = []
    end = summary["start_ns"] + summary["cct_ns"]
    duration = summary["cct_ns"]
    links = defaultdict(list)
    for r in ports:
        if r["peer"] >= 12 and r["start_ns"] < end and r["start_ns"] + BIN_NS > summary["start_ns"]:
            links[r["node"], r["peer"]].append(r)
    for (node, remote), rows in sorted(links.items()):
        wire_bytes = sum(r["wire_bytes"] for r in rows)
        samples = sum(r["samples"] for r in rows)
        path_ports.append({
            "node": node, "peer": remote, "port": rows[0]["port"], "wire_bytes": wire_bytes,
            "collective_wire_bytes": sum(r["collective_bytes"] for r in rows),
            "background_wire_bytes": sum(r["background_bytes"] for r in rows),
            "approx_wire_utilization": wire_bytes * 0.8 / duration,
            "queue_peak_bytes": max(r["queue_max"] for r in rows),
            "sample_mean_queue_bytes": sum(r["queue_sum"] for r in rows) / max(1, samples),
            "queue_marks": sum(r["queue_marks"] for r in rows),
        })
    hot_tor, hot_spine = summary["hot_tor"], summary["hot_spine"]
    alternate = 15 if hot_spine == 14 else 14
    by_port = {(row["node"], row["peer"]): row for row in path_ports}
    metrics["hot_uplink_approx_wire_utilization"] = by_port.get(
        (hot_tor, hot_spine), {}).get("approx_wire_utilization", 0)
    metrics["alternate_uplink_approx_wire_utilization"] = by_port.get(
        (hot_tor, alternate), {}).get("approx_wire_utilization", 0)
    labels = {k: summary[k] for k in ("scenario", "treatment", "seed", "chunk")}
    for filename, rows in (("sender_recovery.csv", by_flow), ("voq_flushes.csv", flushes),
                           ("symphony_exclusion_decisions.csv", choices), ("fabric_ports.csv", path_ports)):
        if emit_artifacts:
            write_csv(folder / filename, [dict(labels, **row) for row in rows])
    return metrics, choices, path_ports


def figures(output, summary, choices):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cw = [r for r in summary if r["treatment"].startswith("conweave")]
    names = {"conweave": "ConWeave", "conweave-symphony": "shared CE",
             "conweave-symphony-separated": "separated"}
    labels = [f"{r['scenario']}\n{names[r['treatment']]}" for r in cw]
    fig, axes = plt.subplots(2, 1, figsize=(max(9, len(cw) * 0.85), 6),
                             sharex=True, constrained_layout=True)
    totals = [0] * len(cw)
    for cause, label in CAUSES.items():
        values = [r[f"notify_{label}"] for r in cw]
        axes[0].bar(labels, values, bottom=totals, label=label.replace("_", " "))
        totals = [a + b for a, b in zip(totals, values)]
    axes[0].legend()
    axes[0].set_ylabel("NOTIFYs")
    reroutes = [r["sym_only_reroute_forwarded"] for r in cw]
    axes[1].bar(labels, reroutes, label="First sample excluded by Symphony only", color="#bc491d")
    axes[1].bar(labels, [r["forwarded_reroutes"] - x for r, x in zip(cw, reroutes)],
                bottom=reroutes, label="Other transmitted reroutes", color="#7890a8")
    axes[1].set_ylabel("Transmitted reroutes")
    axes[1].legend()
    fig.savefig(output / "conweave-feedback.pdf")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5), constrained_layout=True)
    for case in SCENARIOS:
        rows = [r for r in choices if r["scenario"] == case and
                r["outcome"] in ("initial_forwarded", "reroute_forwarded")]
        ax.scatter([max(r[f"prior_hop{j}_wire_utilization"] for j in (1, 2)) for r in rows],
                   [max(r[f"prior_hop{j}_queue_peak_bytes"] for j in (1, 2)) / 1000 for r in rows],
                   label=case, alpha=0.65)
    ax.axhline(50, color="0.5", ls="--", lw=1)
    ax.axvline(0.9, color="0.5", ls="--", lw=1)
    ax.set_xlabel("Excluded path's highest hop utilization in prior 100 µs bin")
    ax.set_ylabel("Highest sampled queue on excluded path (kB)")
    ax.legend()
    fig.savefig(output / "exclusion-congestion.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True, constrained_layout=True)
    modes = list(dict.fromkeys(r["treatment"] for r in summary))
    spacing = 0.72 / len(modes)
    for i, mode in enumerate(modes):
        group = [r for r in summary if r["treatment"] == mode]
        x = [SCENARIOS.index(r["scenario"]) + (i - (len(modes) - 1) / 2) * spacing for r in group]
        axes[0].bar(x, [r["collective_rate_cuts"] for r in group], spacing * 0.95, label=mode)
        axes[1].bar(x, [(r["median_last_cnp_to_ack_ns"] or 0) / 1e6 for r in group], spacing * 0.95)
    axes[0].legend(ncol=2)
    axes[0].set_ylabel("Collective QP rate cuts")
    axes[1].set_ylabel("Median last CNP to final ACK (ms)")
    axes[1].set_xticks(range(len(SCENARIOS)), SCENARIOS)
    fig.savefig(output / "sender-recovery.pdf")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--standalone-reference", type=Path)
    parser.add_argument("--provenance-only-reference", "--pilot-reference", type=Path,
                        help="compare original treatments against a reference matrix")
    args = parser.parse_args()
    separated = "conweave-symphony-separated"
    modes = [mode for mode in TREATMENTS
             if mode != separated or any((args.results / scenario / mode).is_dir()
                                         for scenario in SCENARIOS)]
    summary, choices, ports = [], [], []
    for scenario in SCENARIOS:
        for mode in modes:
            case = Path(scenario) / mode
            folder = args.results / case
            if args.standalone_reference and mode in ("dcqcn", "symphony", "conweave"):
                same_behavior(folder, args.standalone_reference / case)
            if args.provenance_only_reference and mode != separated:
                same_behavior(folder, args.provenance_only_reference / case)
            metrics, decisions, fabric = analyze_case(folder)
            summary.append(metrics)
            choices.extend(dict(scenario=scenario, treatment=mode, **r) for r in decisions)
            ports.extend(dict(scenario=scenario, treatment=mode, **r) for r in fabric)
    write_csv(args.results / "pilot-summary.csv", summary)
    write_csv(args.results / "symphony-exclusion-decisions.csv", choices)
    write_csv(args.results / "fabric-ports.csv", ports)
    figures(args.results, summary, choices)
    print(f"Analyzed {len(summary)} cases, {len(choices)} first-sample Symphony-only exclusions")


if __name__ == "__main__":
    main()
