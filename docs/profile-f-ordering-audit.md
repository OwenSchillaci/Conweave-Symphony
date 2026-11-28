# Profile F receiver sequence gaps and VOQ timer expirations

Paths to generated CSVs below are local to `analysis/figures/profile-f-ordering/` in the research checkout. Recreate them with the replay commands below. See the [artifact guide](experiment-artifacts.md) for saved runs.

## Finding

In six timer-expiration events across five deterministic replays, the destination ToR released its VOQ before the old-path TAIL arrived. The timer fired at its calculated deadline in each case. The TAIL's old-path transit time exceeded the reference packet's transit time by **16.447–31.640 µs**, greater than the configured **16 µs** margin. All six original TAIL packets arrived. The flush was expected ConWeave fallback behavior, triggered by a premature estimate under this workload. The replays showed no new defect in timer arithmetic, FIFO release, or payload accounting. Standalone ConWeave reproduced the same mechanism, so it does not require Symphony or shared CE.

The investigation preserved the frozen run matrix and used a separate probe binary. Its extra code only logs events. For each replay, the outcome, queue traces, rates, route and VOQ records, CNP and PFC counters, and completion records matched the archived outputs byte for byte.

## What the archived runs establish

I checked completion ledgers in all 200 runs, including source and destination tuples, declared payload sizes, FCT start and completion times, and the 104 rank-local dependency releases per collective. In all 14 affected runs, the 112 collective transfers and both background QPs completed. The archived VOQ traces identify 15 expirations whose last arm was an estimate rather than a TAIL-triggered release. The 42 receiver notifications count emitted gap NACKs with the congestion bit. During the NACK suppression interval, additional out-of-order arrivals can be ignored without increasing that counter.

The 15 affected flows account for **all 191,784 excess collective wire bytes** in those 14 runs, equivalent to 183 extra 1,048-byte frames. The largest increase was 22,008 bytes in one collective, about 0.036% of its 61.54 MB single-pass collective wire volume. The archived excess is measured at the source-ToR fabric egress. The probes independently identify those bytes as repeated sender sequence ranges in the replayed cases. The archives provide exact transfer and dependency ledgers for every run. They do not contain packet-by-packet receiver cursors for the runs that were not replayed.

Completion and excess-byte audit at `affected-runs.csv` · All 15 timer-expiration records at `timer-flushes.csv`

## Timer and packet timeline

At the source, a **reply timeout** can trigger the cut. The current packet becomes TAIL on the old path, and subsequent phase-1 packets use the new path. At the destination, early phase-1 arrivals enter the VOQ. With an old-path reference packet B, the deadline is

```text
arrival(B) + [source_timestamp(TAIL) - source_timestamp(B)] + 16 µs
```

The phase-0 reference is updated on new old-path arrivals. If TAIL arrives in time while reordering is active, it arms a flush for one nanosecond later. If the estimated deadline wins, the FIFO is released and receiver-side ConWeave state moves to phase 1. The 500 µs no-history fallback was not used in the six replayed events. The 40 µs source reply slack, 1 ms connection expiry, receiver NACK suppression, and transport retransmit timeout are separate timers.

The following times are absolute simulation times in ms. Byte offsets use the sender's payload sequence, and the old and new path IDs were 519 and 520 in all six replayed events.

| Seed, scenario, treatment | Flow / epoch | VOQ expiry (ms) | TAIL late (µs) | Expected → first premature byte offset | Gap to full flushed-range acceptance (µs) | Extra data frames |
|---|---:|---:|---:|---:|---:|---:|
| 2, hotspot, ConWeave | 4 / 1 | 15.675974 | 0.447 | 14,000 → 15,000 | 391.669 | 15 |
| 2, hotspot, separated CE | 66 / 6 | 25.112726 | 4.480 | 84,000 → 85,000 | 328.843 | 6 |
| 5, hotspot, shared CE | 13 / 1 | 30.189666 | 9.977 | 14,000 → 15,000 | 419.231 | 6 |
| 5, both, shared CE | 59 / 14 | 15.336472 | 15.640 | 155,000 → 156,000 | 294.314 | 16 |
| 10, hotspot, shared CE | 3 / 4 | 14.399014 | 0.760 | 56,000 → 57,000 | 406.816 | 15 |
| 10, hotspot, shared CE | 68 / 2 | 28.008779 | 11.801 | 28,000 → 29,000 | 393.125 | 6 |

**Seed 2, standalone ConWeave, flow 4.** The source ToR assigned TAIL byte offset 14,000 to epoch 1 at 15.593781 ms. Phase-1 byte offset 15,000 arrived at the destination ToR at 15.601369 ms and armed the VOQ timer. The eighth and last arm occurred at 15.618599 ms. The estimate expired at 15.675974 ms and released 13 packets. The original TAIL reached the destination switch 447 ns later, after those packets had been queued toward the host. At 15.677812 ms the first premature packet reached the RNIC and elicited NACK 64701 with expected byte offset 14,000 and CNP set. The incoming data had no CE. The sender received the NACK at 15.682000 ms, rewound from 28,000 to 14,000, and reduced its rate in the normal DCQCN check. The original TAIL eventually filled the first missing range. Retransmissions completed the out-of-order range and the sender received ACK 524,288 at 17.285341 ms.

**Seed 5, both perturbations, shared CE, flow 59.** The 13-packet VOQ expired 15.640 µs before TAIL arrived at the destination ToR. The first premature packet carried offset 156,000 while the RNIC expected 155,000, without CE. A replayed copy filled the first missing range at 15.350989 ms, before the original TAIL reached even the destination switch at 15.352112 ms. The late original and duplicate data did not double-count accepted bytes. The sender received the final cumulative ACK at 17.245363 ms.

In every probe, the first prematurely released packet at the RNIC was unmarked. The gap NACK indicated congestion independently of any RED or Symphony mark. With IRN disabled, the receiver kept its contiguous byte cursor fixed at a gap. The sender rewound to its cumulative ACK, retransmitted, and applied the normal DCQCN response to the CNP flag. No transport retransmit timeout fired in any of the five replays. The separate 4 µs NACK suppression interval is why one VOQ release can yield several notifications and why the number of notifications need not equal the number of discarded packets.

Packet timelines including UIDs, epochs, reference transit times, and repair at `packet-timelines.csv` · Standalone flow-level event trace at `seed-2/hotspot/conweave/affected-packet-events.csv`

### Source and upstream comparison

| Topic | Local implementation |
|---|---|
| Reply timeout, TAIL path, and phase tag | [conweave-routing.cc](../src/point-to-point/model/conweave-routing.cc#L507), [TAIL tagging](../src/point-to-point/model/conweave-routing.cc#L674) |
| Old-path reference and destination timer arms | [conweave-routing.cc](../src/point-to-point/model/conweave-routing.cc#L857) |
| Timer callback, FIFO, and phase completion | [conweave-voq.cc](../src/point-to-point/model/conweave-voq.cc#L55), [routing callback](../src/point-to-point/model/conweave-routing.cc#L1203) |
| Gap detection, NACK with CNP, and resend | [rdma-hw.cc](../src/point-to-point/model/rdma-hw.cc#L639), [feedback](../src/point-to-point/model/rdma-hw.cc#L369), [sender rewind](../src/point-to-point/model/rdma-hw.cc#L574) |
| Cumulative ACK, completion, and next collective send | [rdma-queue-pair.cc](../src/point-to-point/model/rdma-queue-pair.cc#L123), [QP completion](../src/point-to-point/model/rdma-hw.cc#L540), [ring dependency release](../scratch/network-load-balance.cc#L576) |

ConWeave §3.3 and Figure 9 describe timeout release as a way to make progress when TAIL is missing. Appendix A and Figure 21 also explicitly discuss premature release when the reference packet and TAIL see different congestion delay. The paper uses different margins for different transport and topology conditions. Its upstream simulator launcher sets 16 µs for the 100 Gb/s leaf-spine preset, and 64 µs for the lossless fat-tree preset. Profile F uses a 10 Gb/s pinned hotspot with a 16 µs margin, so reuse of the numeric leaf-spine setting cannot guarantee equivalent estimator coverage. This is a workload-dependent timing risk, with the observed insufficiency attributable to the margin and the predictor's approximation. It is separate from a configuration parsing or timer scheduling error. [ConWeave paper](https://rajkiranjoshi.github.io/files/papers/sigcomm23-conweave.pdf) · [Upstream topology-specific settings](https://github.com/conweave-project/conweave-ns3/blob/main/run.py#L240)

The upstream simulator uses the same cached estimator, one-nanosecond TAIL rearm, FIFO timer callback, and receiver gap CNP behavior. A small separate transport difference exists: upstream returns a cumulative ACK for a fully stale duplicate in the non-IRN branch, while this checkout can ignore it. None of the replayed events waited for a sender RTO or failed delivery because of that difference. It is outside the observed trigger and is worth keeping distinct from premature VOQ flushes. [Upstream routing estimator](https://github.com/conweave-project/conweave-ns3/blob/main/src/point-to-point/model/conweave-routing.cc#L735) · [Upstream VOQ](https://github.com/conweave-project/conweave-ns3/blob/main/src/point-to-point/model/conweave-voq.cc#L57) · [Upstream receiver](https://github.com/conweave-project/conweave-ns3/blob/main/src/point-to-point/model/rdma-hw.cc#L612)

## Delivery and effect on CCT

The five replays directly checked **570 QPs and 377,487,360 modeled payload bytes**, including background traffic. For each packet UID, the probe matched the transmitted and received offset and length. It independently reconstructed the contiguous prefix, confirmed that gaps did not advance it, and verified the generated and received ACKs. Each final ACK matched the exact offered payload length and the corresponding FCT and dependency ledger. This validates modeled delivery and accounting. Profile F sends synthetic byte lengths, so there is no application data-value checksum or collective arithmetic to verify.

The recovery affects individual flows. In the replays it took 294–419 µs from the first receiver gap to contiguous acceptance of the whole prematurely released range. That is an observed recovery interval, which includes time that would also be needed to send the original packets. It is not a counterfactual CCT penalty. Across the archives, three affected flows were the final flow or lay on the final flow's observed ACK-gated release chain.

In the seed-5 hotspot shared-CE run, affected flow 13 was itself the final collective transfer. It emitted two gap CNPs and six repeat frames, and finished 128.801 µs after the next-to-last transfer. The pacing rate had already hit the 100 Mb/s floor before the first gap. The additional CNPs did not lower it further, but the recovery timer still restarted. The observed first fast-recovery tick was at 30.501808 ms, 60 µs later than the deadline implied by the last pre-gap decrease check under the same fixed send history. That difference illustrates a mechanism that matters on a completion path. It cannot be directly subtracted from CCT because feedback and path progression would then differ.

The all-run results remain the primary evaluation. In a post hoc sensitivity check, dropping a seed pair if either treatment reported a sequence gap left five to seven pairs per hotspot-bearing contrast. The median separated-vs-shared improvement was +0.19% with the hotspot in both analyses, and shifted from −0.10% to +0.47% in the joint scenario. Both Symphony combinations still had negative median gains against ConWeave in those two scenarios. This supports the existing qualified conclusion of no consistent separation advantage. The smaller sensitivity subsets are not a replacement for the prespecified matrix.

Per-QP verification for the critical final-flow replay at `seed-5-final/hotspot/conweave-symphony/payload-audit.csv` · Paired CCT sensitivity at `cct-sensitivity.csv`

## Recommendation and reproduction

No correctness correction is required to retain or interpret the existing evaluation under its modeled reliable transport. Keep the affected runs and report early flushes, gap CNPs, and extra wire traffic alongside CCT. Before making strong claims about small CCT differences or a reorder-free ConWeave datapath, run a separately labeled, matched VOQ-margin sensitivity across all ConWeave treatments. Use the measured TAIL estimation-error distribution to define that experiment. Increasing a timeout can reduce premature flushes while increasing occupancy and delaying recovery for genuinely missing TAILs. This audit made no such change.

Run the log-only build and replays from ns-3.19. The frozen base build supplies object files and headers. On the imported workspace build, use `--headers /tmp/ns3-profile-f-build/include` to select the staged readable header tree.

```bash
probe_build=/tmp/ns3-profile-f-ordering-build
probe_results=mix/output/profile-f-ordering
sweep=mix/output/profile-f-seeds-1-10
seed_one=mix/output/profile-f-five-treatment

python3 analysis/build_profile_f_ordering_probe.py --base-build build --output "$probe_build"
export LD_LIBRARY_PATH="$PWD/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

common=(--build "$probe_build" --chunk 524288 --mark-provenance --pilot-detail --no-plots)
python3 analysis/run_profile_f.py "${common[@]}" --output "$probe_results/seed-2" \
  --seed 2 --scenarios hotspot --treatments conweave conweave-symphony-separated
python3 analysis/run_profile_f.py "${common[@]}" --output "$probe_results/seed-5" \
  --seed 5 --scenarios both --treatments conweave-symphony
python3 analysis/run_profile_f.py "${common[@]}" --output "$probe_results/seed-5-final" \
  --seed 5 --scenarios hotspot --treatments conweave-symphony
python3 analysis/run_profile_f.py "${common[@]}" --output "$probe_results/seed-10" \
  --seed 10 --scenarios hotspot --treatments conweave-symphony

python3 analysis/analyze_profile_f_ordering.py --sweep "$sweep" \
  --seed-one "$seed_one" --probes "$probe_results"
```
