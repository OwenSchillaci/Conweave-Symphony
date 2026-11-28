"""Fold Profile F packet trace events into compact counters during simulation."""

from collections import Counter


DROPPED = {"CW_PROV_TX", "CW_PROV_CANDIDATES", "CW_PROV_PATH", "SYMPHONY_FEEDBACK"}


def compact_log(source, output, separated, bin_ns):
    counts = Counter()
    eligible = Counter()
    suppressed = Counter()
    last_marks = {}
    notify_causes = {}
    for line in source:
        kind, _, content = line.partition(" ")
        if kind in DROPPED:
            continue
        if kind == "CW_PROV_RX" and " ecn=3 " not in line and " old_epoch=1" not in line:
            continue
        if kind == "SYMPHONY_DEQUEUE" and " eligible=1 " not in line:
            continue
        if kind not in {"CW_PROV_RX", "SYMPHONY_DEQUEUE", "ECN_PROV_MARK",
                        "CW_PROV_NOTIFY", "CW_PROV_EXCLUDE"}:
            output.write(line)
            continue
        r = dict(field.split("=", 1) for field in content.split())
        if kind == "CW_PROV_RX":
            old, ce = r["old_epoch"] == "1", r["ecn"] == "3"
            counts["prior_epoch_packets"] += old
            counts["prior_epoch_ce_packets"] += old and ce
            if ce:
                cause = r["causes"]
                counts[f"dst_ce_{cause}"] += 1
                if separated and cause == "2" and not old:
                    counts["suppressed"] += 1
                    suppressed[int(r["time_ns"]) // bin_ns * bin_ns,
                               int(r["switch"]), int(r["path"])] += 1
        elif kind == "SYMPHONY_DEQUEUE":
            eligible[int(r["switch"]), int(r["time_ns"]) // bin_ns * bin_ns] += 1
        elif kind == "ECN_PROV_MARK":
            counts[f"mark_events_{r['local']}"] += 1
            last_marks[r["data_uid"]] = r["causes"]
        elif kind == "CW_PROV_NOTIFY":
            counts[f"notify_{r['causes']}"] += 1
            notify_causes[r["notify_uid"]] = r["causes"]
        else:
            counts[f"exclusion_{notify_causes.pop(r['notify_uid'])}"] += 1
    for cause in last_marks.values():
        counts[f"marked_packets_{cause}"] += 1
    counts["eligible"] = sum(eligible.values())
    output.write("PROFILE_F_COMPACT " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())) + "\n")
    for (switch, start), packets in sorted(eligible.items()):
        output.write(f"PROFILE_F_COMPACT_ELIGIBLE switch={switch} start_ns={start} packets={packets}\n")
    for (start, switch, path), packets in sorted(suppressed.items()):
        output.write(f"PROFILE_F_COMPACT_SYNC start_ns={start} destination_tor={switch} "
                     f"path={path} packets={packets}\n")
