# Profile F four-treatment provenance pilot

The 16 runs use seed 1, 512 KiB chunks, and the four existing Profile F perturbation scenarios. Each completed all 112 collective QPs and passed the matching workload, pinning, dependency, and VOQ-drain checks. The three standalone treatments reproduced the archived outputs byte for byte. The 16 detailed runs also matched the corresponding provenance-only runs on summaries, transfers, queues, rates, paths, path changes, VOQ residence, CNP, and PFC outputs.

The only simulator additions for this run are optional observations. `--pilot-detail` enables the existing DCQCN rate, CNP, and recovery traces for all treatments. It also records each VOQ timer arm and whether TAIL triggered it. The analyzer matches each actual release to its last arm and verifies the deadline. The workload, DCQCN configuration, Symphony rules, and ConWeave rules use their existing settings.

## Completion time and step overlap

Each cell gives CCT in ms, then peak and time-weighted mean active steps.

| Perturbation | ECMP + DCQCN | ECMP + Symphony | ConWeave | ConWeave + Symphony shared CE |
|---|---:|---:|---:|---:|
| Neither | 21.291 / 5 / 2.95 | 23.023 / 2 / 1.61 | 16.828 / 4 / 2.25 | 16.768 / 2 / 1.51 |
| Hotspot | 36.640 / 7 / 4.17 | 29.727 / 2 / 1.60 | 21.774 / 4 / 2.22 | 22.149 / 2 / 1.53 |
| Skew | 24.455 / 5 / 3.09 | 22.272 / 2 / 1.53 | 16.277 / 3 / 1.99 | 18.077 / 2 / 1.54 |
| Both | 27.852 / 5 / 3.07 | 33.048 / 2 / 1.63 | 22.370 / 4 / 2.42 | 21.247 / 2 / 1.54 |

Adding Symphony to ConWeave changed CCT by −0.36%, +1.73%, +11.06%, and −5.02%, in table order. The combination reduced overlap and beat both ECMP treatments in every scenario. The incremental CCT effect relative to ConWeave alone varies in sign. The small difference in the clean run remains inconclusive at one seed.

In the both case, ToR 12 uplink utilization was approximately 92.7%/35.9% on the injected hot path and alternate in DCQCN, 90.8%/30.2% under Symphony, 93.2%/76.8% under ConWeave, and 99.6%/82.9% under shared CE. The hot uplink peak queue was 117,376, 115,280, 102,704, and 98,512 bytes, respectively. Utilization uses bytes attributed to 100 µs transmit-start bins intersecting the CCT window. Boundary bins can include traffic outside the exact window. Individual bin utilization can slightly exceed line rate due to packet boundaries.

## Mark provenance and decisions

In the combined treatment, local mark events and NOTIFY counts are reported as queue-only / Symphony-only / both. IPv4 CE is shared across hops, so a packet may accumulate both causes at distinct switches even when neither switch records a dual local event. NOTIFYs use the cumulative cause observed at the destination ToR. Exclusion counts matched NOTIFY counts by cause in all eight ConWeave runs.

| Perturbation | Combined local events | Combined NOTIFYs and exclusion updates | Standalone ConWeave NOTIFYs, all queue | Combined transmitted reroutes | Reroutes preceded by Symphony-only exclusion of the first sample |
|---|---:|---:|---:|---:|---:|
| Neither | 31 / 185 / 0 | 31 / 118 / 0 | 45 | 40 | 1 |
| Hotspot | 278 / 282 / 0 | 207 / 205 / 3 | 307 | 123 | 7 |
| Skew | 6 / 276 / 0 | 6 / 176 / 0 | 40 | 11 | 0 |
| Both | 176 / 196 / 0 | 122 / 132 / 1 | 315 | 82 | 1 |

Of 1,001 combined NOTIFYs, 631 were Symphony-only, 366 queue-only, and four dual-cause. In 140 path selection opportunities, an active Symphony-only exclusion made the first sampled candidate unusable. With a usable second sample, 19 were confirmed initial transmissions on the second candidate, nine were confirmed physical reroutes, two kept the current path, and one had no matching transmission in the chosen epoch and phase. The other 109 decisions had neither sample usable. A rejected second draw after an accepted first draw is outside this count. For every counted reroute, the trace links the marked data UID, NOTIFY UID, active exclusion, sampled candidates, path update and physical transmit. These counts show where the exclusion entered the decision. They do not estimate CCT under cause-separated feedback, which would also change subsequent rates, queues, and path choices.

### Physical congestion on the excluded path

The primary comparison uses both egress hops of the decoded path, in the last complete 100 µs bin before the decision. Ordinary RED on any packet in that bin is direct evidence of queue-based congestion marking on the path. A peak of at least 50,000 bytes is a queue-pressure proxy because the exported device queue differs from the RED counter for shared bytes in the marked priority group. A busy link by itself establishes utilization. The analyzer also records the bin containing the triggering Symphony mark and the bin containing the decision. Those enclosing bins can include later traffic.

- Among the **nine confirmed reroutes**, two excluded paths had RED marks, six reached the 50 kB queue proxy, and all nine had at least one hop near line rate in the preceding bin. In both enclosing-bin checks, seven out of nine reached the queue proxy and two had RED marks. The reroutes therefore provide mixed queue evidence and no examples of abandoning a lightly utilized path in this seed.
- Among the **19 confirmed initial selections**, zero excluded paths had prior-bin RED, one reached the queue proxy, and nine had a hop at or above 90% utilization. Ten had neither hop at 90%, leaving more room for progress marks to suppress usable initial choices. These are distinct selection events and can share a notification or sample bin.
- Eight reroutes in the hotspot-bearing scenarios moved from spine 15 to the injected hotspot path via spine 14. The destination path was not uniformly better. For example, in the both scenario decision 196611 avoided a path with a 56.6 kB peak and no RED in the preceding bin. The selected path had an 84.9 kB peak and four RED marks in that same bin. Both paths ran near line rate. In the clean scenario decision 40093, the excluded path peaked at 45.1 kB and 99.4% utilization, while the chosen path had no sampled queue and 3.9% utilization.

## VOQ ordering and sender recovery

| Perturbation | ConWeave / shared CE reply timeouts | ConWeave / shared CE VOQ packets | ConWeave / shared CE max VOQ residence (µs) | ConWeave / shared CE prior-epoch data |
|---|---:|---:|---:|---:|
| Neither | 107 / 74 | 436 / 372 | 60.874 / 60.118 | 1 / 0 |
| Hotspot | 326 / 244 | 1,293 / 584 | 79.253 / 71.909 | 17 / 7 |
| Skew | 132 / 43 | 600 / 108 | 60.874 / 45.620 | 1 / 0 |
| Both | 384 / 189 | 1,347 / 475 | 77.099 / 71.376 | 12 / 7 |

All VOQ releases in the eight ConWeave runs followed TAIL-triggered arms. There were zero releases due to an ordinary estimated timer expiration. The reply timeouts in the table are a separate event used at the source ToR. Both ToRs drained in every case, with zero receiver sequence-gap notifications and zero PFC receptions. Four prior-epoch packets carried CE in standalone ConWeave with both perturbations and one did so with the hotspot alone. Their provenance records preserve the existing prior-epoch forwarding treatment.

The sender exports count CNP arrivals, actual rate decreases, and recovery updates per collective QP. Recovery updates record the before and after pacing rates. The per-flow export also gives time and unsent payload at the final active CNP, time to the first subsequent recovery event, and maximum subsequent recovery rate. Last CNP through final ACK includes sending and acknowledgment latency.

Relative to ConWeave, the combined runs had 208 vs 45 rate cuts in clean, 470 vs 299 in hotspot, 268 vs 38 in skew, and 306 vs 310 in both. With both perturbations, median last-CNP-to-ACK time among marked collective flows rose from 0.667 to 0.929 ms even as overall CCT fell 5.02%. Neither final flow received a CNP before its final ACK. With skew alone, low VOQ residence and strong alignment coexisted with slower CCT and many more rate cuts. These measurements give candidate explanations for the mixed result. The one-seed pilot cannot attribute the CCT changes to sender recovery, path exclusions, or alignment in isolation.

## Reproduction

From the ns-3.19 checkout with Matplotlib installed, use new writable output directories. The workspace used a staged rebuild against the imported optimized libraries. The regular checkout commands are:

```bash
./waf build
python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-pilot-complete \
  --scenarios clean hotspot skew both \
  --treatments dcqcn symphony conweave conweave-symphony \
  --seed 1 --chunk 524288 --workers 4 --mark-provenance --pilot-detail

python3 analysis/analyze_profile_f_pilot.py mix/output/profile-f-pilot-complete
```

For artifact reproducibility checks, pass the original standalone output tree with `--standalone-reference`. To reproduce the observation-only check, repeat the runner command with a separate output tree and omit `--pilot-detail`, then pass that tree as `--provenance-only-reference`. The analyzer compares existing result files byte for byte. The standard runner writes CCT, overlap and both-case fabric plots. The analyzer adds `pilot-summary.csv`, `fabric-ports.csv`, `symphony-exclusion-decisions.csv`, feedback, congestion, and rate-recovery plots, and per-run sender recovery and VOQ flush CSVs. All 14 Python tests and all five point-to-point C++ cases passed.
