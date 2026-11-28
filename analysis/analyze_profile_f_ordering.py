#!/usr/bin/env python3
"""Audit Profile F completion, retransmissions, and ConWeave timer-expiry replays."""

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from analyze_profile_f_pilot import read_csv, records, same_behavior
from run_profile_f import RING, STEPS, validate_transfers, write_csv

REPLAYS = (
    (2, "hotspot", "conweave"),
    (2, "hotspot", "conweave-symphony-separated"),
    (5, "both", "conweave-symphony"),
    (5, "hotspot", "conweave-symphony"),
    (10, "hotspot", "conweave-symphony"),
)


def flow_key(row):
    return ((row["sport"] << 16 | row["src"]) << 32) | (row["dport"] << 16) | row["dst"]


def archived_completion(folder, chunk):
    transfers = read_csv(folder / "transfers.csv")
    coords = {(r["rank"], r["step"] + (7 if r["phase"] == "all-gather" else 0)): r
              for r in transfers}
    setup = dict(line.split(None, 1) for line in (folder / "scenario.txt").read_text().splitlines())
    delay = list(map(int, setup["RING_PROCESSING_DELAY_NS"].split()))
    complete, = [r for _, r in records(folder / "collective.log", {"RING_COMPLETE"})]
    waits = validate_transfers(coords, complete, delay, chunk, complete["start_ns"])
    if complete["transfers"] != len(RING) * STEPS or len(waits) != 104:
        raise ValueError(f"invalid ring completion: {folder}")

    fcts = {}
    for line in (folder / "fct.txt").read_text().splitlines():
        src, dst, sport, dport, size, start, duration, _ = map(int, line.split())
        key = src, dst, sport, dport
        if key in fcts:
            raise ValueError(f"duplicate FCT: {folder} {key}")
        fcts[key] = size, start, start + duration
    for row in transfers:
        key = tuple(row[k] for k in ("src", "dst", "sport", "dport"))
        if fcts.pop(key) != (row["bytes"], row["start_ns"], row["completion_ns"]):
            raise ValueError(f"ring FCT mismatch: {folder} {key}")
    background = []
    for index, line in enumerate((folder.parent / "background.txt").read_text().splitlines()):
        src, _, _, _, dst, sport, dport, size, start = map(int, line.split())
        key = src, dst, sport, dport
        completed_size, began, end = fcts.pop(key)
        if (completed_size, began) != (size, start):
            raise ValueError(f"background completion mismatch: {folder} {key}")
        background.append({"flow_id": 1_000_000 + index, "bytes": size, "completion_ns": end})
    if fcts:
        raise ValueError(f"unexpected completions: {folder}: {fcts.keys()}")
    return transfers, background, complete


def timer_flushes(folder):
    arms, flushes = defaultdict(list), {}
    for kind, row in records(folder / "collective.log", {"CW_PILOT_VOQ_ARM", "PROFILE_F_VOQ"}):
        key = row["flow_key"]
        if kind == "CW_PILOT_VOQ_ARM":
            arms[key].append(row)
        else:
            group = key, row["time_ns"]
            if group not in flushes:
                history = arms.pop(key)
                last = history[-1]
                if row["time_ns"] != last["time_ns"] + last["delay_ns"]:
                    raise ValueError(f"VOQ flush is inconsistent with scheduled timer: {folder}")
                flushes[group] = {"arms": history, "packets": []}
            flushes[group]["packets"].append(row)
    if arms:
        raise ValueError(f"undrained VOQs: {folder}")
    return {key: v for key, v in flushes.items() if not v["arms"][-1]["tail"]}


def archive_wire_bytes(folder, transfers):
    wire = Counter()
    for row in read_csv(folder / "flow_paths.csv"):
        wire[row["flow_id"]] += row["wire_bytes"]
    extra = {}
    for row in transfers:
        size, flow = row["bytes"], row["flow_id"]
        minimum = size + ((size + 999) // 1000) * 48
        extra[flow] = wire[flow] - minimum
        if extra[flow] < 0:
            raise ValueError(f"wire bytes below a single transmission: {folder}")
    return extra


def validate_replay(events, transfers):
    order = defaultdict(list)
    for name, row in events:
        order[row["flow_id"]].append((name, row))
    audit = {}
    for row in transfers:
        flow, size, end = row["flow_id"], row["bytes"], row["completion_ns"]
        cursor, acked = 0, 0
        uid_to_data, uid_to_feedback, uid_to_rx = {}, {}, {}
        sends = Counter()
        final_acks = []
        rewinds = Counter()
        for kind, r in order[flow]:
            if kind == "ORDER_TX":
                uid_to_data[r["uid"]] = (r["seq"], r["bytes"])
                sends[r["seq"], r["bytes"]] += 1
            elif kind == "ORDER_RX":
                assert uid_to_data[r["uid"]] == (r["seq"], r["bytes"]), r
                assert r["expected"] == cursor and r["seq"] + r["bytes"] <= size, r
                expected_next = max(cursor, r["seq"] + r["bytes"]) if r["seq"] <= cursor else cursor
                assert r["next"] == expected_next, r
                if r["gap_cnp"]:
                    assert r["seq"] > cursor and r["action"] == 2, r
                uid_to_rx[r["uid"]] = r
                cursor = r["next"]
            elif kind == "ORDER_ACK_TX":
                assert r["ack"] == uid_to_rx[r["data_uid"]]["next"], r
                uid_to_feedback[r["uid"]] = r
            elif kind == "ORDER_ACK_RX":
                assert r["ack"] == uid_to_feedback[r["uid"]]["ack"] <= cursor, r
                acked = max(acked, r["ack"])
                assert r["snd_una"] == acked, r
                if acked == size:
                    final_acks.append(r)
            elif kind == "ORDER_REWIND":
                assert r["after"] == acked <= r["before"], r
                rewinds[r["reason"]] += 1
        assert cursor == acked == size and len(final_acks) == 1 and final_acks[0]["time_ns"] == end, (
            flow, cursor, acked, final_acks, end)
        assert set(sends) == {(seq, min(1000, size - seq)) for seq in range(0, size, 1000)}, flow
        audit[flow] = {"flow_id": flow, "expected_payload_bytes": size, "accepted_payload_bytes": cursor,
                      "final_ack_ns": end, "final_ack_uid": final_acks[0]["uid"], "final_ack": acked,
                      "retransmitted_packets": sum(n - 1 for n in sends.values()),
                      "retransmitted_wire_bytes": sum((n - 1) * (length + 48) for (_, length), n in sends.items()),
                      "nack_rewinds": rewinds["nack"], "transport_rtos": rewinds["rto"]}
    return audit


def release_chain(transfers, delay):
    by_rank = {(r["rank"], r["step"] + (7 if r["phase"] == "all-gather" else 0)): r for r in transfers}
    chain = []
    current = max(transfers, key=lambda r: r["completion_ns"])
    while current is not None:
        chain.append(current["flow_id"])
        rank = current["rank"]
        step = current["step"] + (7 if current["phase"] == "all-gather" else 0)
        predecessor = by_rank[(rank - 1) % len(RING), step - 1] if step else None
        if predecessor is not None and predecessor["completion_ns"] + delay[rank] != current["start_ns"]:
            break  # local issue ordering, rather than predecessor ACK, gated this release
        current = predecessor
    return chain


def replay_timeline(folder, expires, transfers, background):
    names = {"ORDER_TOR_RX", "ORDER_TAIL", "ORDER_TX", "ORDER_RX", "ORDER_ACK_TX",
             "ORDER_ACK_RX", "ORDER_REWIND"}
    events = list(records(folder / "collective.log", names))
    audit = validate_replay(events, transfers + background)
    by_uid = defaultdict(dict)
    by_flow = defaultdict(lambda: defaultdict(list))
    for kind, r in events:
        if "uid" in r:
            by_uid[kind][r["uid"]] = r
        by_flow[r["flow_id"]][kind].append(r)
    rows = []
    for (key, expired), detail in expires.items():
        first = detail["packets"][0]
        buffered = by_uid["ORDER_TOR_RX"][first["uid"]]
        flow, epoch = buffered["flow_id"], buffered["epoch"]
        tails = [r for r in by_flow[flow]["ORDER_TAIL"] if r["epoch"] == epoch]
        tail, = tails
        tail_arrival = by_uid["ORDER_TOR_RX"].get(tail["uid"])
        assert tail_arrival and tail_arrival["time_ns"] > expired, (folder, tail, expired)
        last_arm = detail["arms"][-1]["time_ns"]
        ref = [r for r in by_flow[flow]["ORDER_TOR_RX"] if r["phase"] == 0 and
               r["epoch"] == epoch and r["time_ns"] <= last_arm][-1]
        tail_delay = tail_arrival["time_ns"] - tail["time_ns"]
        ref_delay = ref["time_ns"] - ref["source_time_ns"]
        nominal = ref["time_ns"] + (tail["time_ns"] - ref["source_time_ns"])
        assert expired == nominal + 16_000 and expired >= last_arm, (ref, tail, expired)
        arrival = by_uid["ORDER_RX"][first["uid"]]
        assert arrival["gap_cnp"] and arrival["expected"] == tail["seq"], (folder, arrival, tail)
        nack = next(r for r in by_flow[flow]["ORDER_ACK_TX"] if r["data_uid"] == first["uid"])
        got_nack = by_uid["ORDER_ACK_RX"][nack["uid"]]
        assert got_nack["nack"] and got_nack["cnp"]
        repair = next(r for r in by_flow[flow]["ORDER_RX"]
                      if r["time_ns"] >= arrival["time_ns"] and r["next"] > arrival["expected"])
        # Track when every packet released by this flush has finally been delivered in sequence.
        to_receive = max(by_uid["ORDER_TOR_RX"][r["uid"]]["seq"] +
                         by_uid["ORDER_TX"][r["uid"]]["bytes"] for r in detail["packets"])
        caught_up = next(r for r in by_flow[flow]["ORDER_RX"]
                         if r["time_ns"] >= arrival["time_ns"] and r["next"] >= to_receive)
        rows.append(dict(flow_id=flow, flow_key=key, epoch=epoch, old_path=tail["path"],
                         new_path=buffered["path"], tail_seq=tail["seq"], tail_uid=tail["uid"],
                         tail_source_tor_ns=tail["time_ns"], first_buffered_uid=first["uid"],
                         first_buffered_seq=buffered["seq"], first_buffered_tor_ns=buffered["time_ns"],
                         first_timer_arm_ns=detail["arms"][0]["time_ns"],
                         last_timer_arm_ns=detail["arms"][-1]["time_ns"], timer_arms=len(detail["arms"]),
                         expiry_ns=expired, released_packets=len(detail["packets"]),
                         gap_receiver_ns=arrival["time_ns"], gap_nack_uid=nack["uid"],
                         gap_nack_sender_ns=got_nack["time_ns"], tail_dst_tor_ns=tail_arrival["time_ns"],
                         tail_late_ns=tail_arrival["time_ns"]-expired,
                         timer_reference_uid=ref["uid"], timer_reference_seq=ref["seq"],
                         timer_reference_source_ns=ref["source_time_ns"],
                         timer_reference_dest_ns=ref["time_ns"], timer_reference_delay_ns=ref_delay,
                         tail_delay_ns=tail_delay, estimator_error_before_margin_ns=tail_delay-ref_delay,
                         missing_prefix_repaired_ns=repair["time_ns"], repair_uid=repair["uid"],
                         flushed_range_accepted_ns=caught_up["time_ns"],
                         gap_to_flushed_range_accepted_ns=caught_up["time_ns"]-arrival["time_ns"],
                         retransmitted_packets=audit[flow]["retransmitted_packets"],
                         rtos=audit[flow]["transport_rtos"], final_ack_ns=audit[flow]["final_ack_ns"]))
    return rows, audit, events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", type=Path, required=True)
    parser.add_argument("--seed-one", type=Path, required=True)
    parser.add_argument("--probes", type=Path, required=True)
    args = parser.parse_args()
    all_runs = read_csv(args.sweep / "run-summary.csv")
    matrix = {(r["seed"], r["scenario"], r["treatment"]): r for r in all_runs}
    affected, archived_cases, timelines = [], [], []
    for r in all_runs:
        identity = {k: r[k] for k in ("seed", "scenario", "treatment")}
        subdir = Path(f"seed-{r['seed']}") / r["scenario"] / r["treatment"]
        folder = args.sweep / subdir if r["seed"] != 1 else args.seed_one / subdir.relative_to("seed-1")
        transfers, background, _ = archived_completion(folder, r["chunk"])
        if not (r["receiver_ooo_notifications"] or r["voq_timer_flushes"]):
            continue
        expires = timer_flushes(folder)
        extra = archive_wire_bytes(folder, transfers)
        by_key = {flow_key(row): row for row in transfers}
        flows = {by_key[key]["flow_id"] for key, _ in expires}
        cfg = dict(line.split(None, 1) for line in (folder / "scenario.txt").read_text().splitlines())
        chain = release_chain(transfers, list(map(int, cfg["RING_PROCESSING_DELAY_NS"].split())))
        affected.append(dict(**identity, cct_ns=r["cct_ns"], gap_cnps=r["receiver_ooo_notifications"],
                             expiry_flushes=len(expires), collective_payload_bytes=sum(t["bytes"] for t in transfers),
                             background_payload_bytes=sum(t["bytes"] for t in background),
                             extra_collective_wire_bytes=sum(extra.values()),
                             extra_wire_bytes_on_expiry_flows=sum(extra[f] for f in flows),
                             final_flow=chain[0], expiry_flows_on_release_chain=" ".join(map(str, flows & set(chain)))))
        assert len(expires) == r["voq_timer_flushes"]
        for (key, t), a in expires.items():
            transfer = by_key[key]
            archived_cases.append(dict(**identity, flow_id=transfer["flow_id"], flow_key=key,
                                       expiry_ns=t, first_arm_ns=a["arms"][0]["time_ns"],
                                       last_arm_ns=a["arms"][-1]["time_ns"], releases=len(a["packets"]),
                                       flow_ct_ns=transfer["completion_ns"]-transfer["start_ns"],
                                       final_ack_ns=transfer["completion_ns"],
                                       extra_wire_bytes=extra[transfer["flow_id"]]))
        if tuple(r[k] for k in ("seed", "scenario", "treatment")) not in REPLAYS:
            continue
        probe = args.probes / subdir
        if (r["seed"], r["scenario"]) == (5, "hotspot"):
            probe = args.probes / "seed-5-final" / r["scenario"] / r["treatment"]
        same_behavior(probe, folder)
        rows, audit, events = replay_timeline(probe, expires, transfers, background)
        assert sum(e["gap_cnp"] for k, e in events if k == "ORDER_RX") == r["receiver_ooo_notifications"]
        assert all(audit[f]["retransmitted_wire_bytes"] == value for f, value in extra.items())
        timelines.extend(dict(**identity, **row) for row in rows)
        write_csv(probe / "payload-audit.csv", list(audit.values()))
        hot_flows = {row["flow_id"] for row in rows}
        with (probe / "affected-packet-events.csv").open("w") as stream:
            writer = csv.writer(stream)
            writer.writerow(["event", "time_ns", "flow_id", "fields"])
            for kind, e in events:
                if e["flow_id"] in hot_flows:
                    writer.writerow([kind, e["time_ns"], e["flow_id"],
                                     " ".join(f"{k}={v}" for k, v in e.items())])
    write_csv(args.probes / "affected-runs.csv", affected)
    write_csv(args.probes / "timer-flushes.csv", archived_cases)
    write_csv(args.probes / "packet-timelines.csv", timelines)

    sensitivity = []
    a, b = "conweave-symphony", "conweave-symphony-separated"
    for case in ("hotspot", "both"):
        for variant, reference in ((a, "conweave"), (b, "conweave"), (b, a)):
            gains, no_gaps = [], []
            for seed in range(1, 11):
                first, second = (matrix[seed, case, mode] for mode in (variant, reference))
                gain = 100 * (1 - first["cct_ns"] / second["cct_ns"])
                gains.append(gain)
                if not (first["receiver_ooo_notifications"] or second["receiver_ooo_notifications"]):
                    no_gaps.append(gain)
            sensitivity.append(dict(scenario=case, variant=variant, reference=reference,
                                    all_n=len(gains), all_median_pct=median(gains),
                                    no_gaps_n=len(no_gaps), no_gaps_median_pct=median(no_gaps)))
    write_csv(args.probes / "cct-sensitivity.csv", sensitivity)
    print(f"Checked {len(all_runs)} completion ledgers, {len(affected)} affected runs "
          f"and {len(archived_cases)} timer flushes. "
          f"Reconstructed {len(timelines)} replay timelines.")


if __name__ == "__main__":
    main()
