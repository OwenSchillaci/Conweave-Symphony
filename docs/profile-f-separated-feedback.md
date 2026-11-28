# Profile F separated-feedback pilot

The new `conweave-symphony-separated` treatment requires ordinary queue congestion in the cumulative mark provenance before ConWeave can send a NOTIFY. Queue-only and dual-cause CE qualify. Symphony-only and unmarked packets do not. All other conditions still apply, including path-aware operation and the prior-epoch bypass. The packet retains its IPv4 CE bit for DCQCN. The switch accumulates provenance for this treatment even when trace logging is disabled. The original `conweave-symphony` treatment uses shared CE, and the three standalone treatments remain the runner defaults.

The gate is in `src/point-to-point/model/conweave-routing.cc` immediately before `SendNotify`. The switch populates the existing provenance tag in `SwitchNode::SwitchNotifyDequeue`. The scoped config is `PROFILE_F_QUEUE_ONLY_NOTIFY 1`. It requires Profile F with ConWeave and original Symphony. No timers, DCQCN parameters, marking logic, workload inputs, or VOQ rules changed.

## CCT and step overlap

All 20 seed-1 runs completed at 512 KiB. Each cell gives CCT in ms, peak active steps, and time-weighted mean active steps.

| Perturbation | ECMP + DCQCN | ECMP + Symphony | ConWeave | Shared CE | Separated |
|---|---:|---:|---:|---:|---:|
| Neither | 21.291 / 5 / 2.95 | 23.023 / 2 / 1.61 | 16.828 / 4 / 2.25 | 16.768 / 2 / 1.51 | 17.342 / 2 / 1.52 |
| Hotspot | 36.640 / 7 / 4.17 | 29.727 / 2 / 1.60 | 21.774 / 4 / 2.22 | 22.149 / 2 / 1.53 | 23.855 / 2 / 1.57 |
| Rank skew | 24.455 / 5 / 3.09 | 22.272 / 2 / 1.53 | 16.277 / 3 / 1.99 | 18.077 / 2 / 1.54 | 17.863 / 2 / 1.53 |
| Both | 27.852 / 5 / 3.07 | 33.048 / 2 / 1.63 | 22.370 / 4 / 2.42 | 21.247 / 2 / 1.54 | 21.388 / 2 / 1.56 |

Relative to shared CE, separation increased CCT by **3.42%** in clean, **7.70%** with the hotspot, and **0.66%** with both. It lowered CCT **1.19%** under rank skew alone. Separation preserved the low peak overlap and lost the small clean-case CCT advantage over ConWeave. With both perturbations, it remained **4.39% faster than ConWeave**. The reported changes are paired seed-1 observations.

## Routing and transport

Each cell gives shared CE followed by separated feedback. Forwarded reroutes require a physical transmission on the new path in the chosen epoch and phase.

| Perturbation | NOTIFYs and exclusion updates | Forwarded reroutes | VOQ packets | Source reply timeouts | Collective rate cuts |
|---|---:|---:|---:|---:|---:|
| Neither | 149 → 34 | 40 → 33 | 372 → 329 | 74 → 57 | 208 → 255 |
| Hotspot | 415 → 298 | 123 → 184 | 584 → 992 | 244 → 275 | 470 → 640 |
| Rank skew | 182 → 38 | 11 → 43 | 108 → 355 | 43 → 62 | 268 → 279 |
| Both | 255 → 120 | 82 → 107 | 475 → 596 | 189 → 210 | 306 → 388 |

There were **781 current-epoch Symphony-only CE arrivals** across the separated runs and zero corresponding NOTIFYs. Queue-only and dual-cause arrivals generated **485 and five NOTIFYs**, respectively. Every NOTIFY corresponded to an exclusion update. The hotspot's eligible queue-only notifications increased from 207 to 295, while dual-cause notifications stayed at three. A reduction in notifications can leave more candidate paths usable during a reply timeout, and changed traffic patterns can produce genuine queue congestion elsewhere. The aggregate reroute count alone cannot distinguish those effects.

In the hotspot case the injected hot uplink's peak queue grew from **100.6 to 115.3 kB**. Approximate mean utilization on the hot and alternate uplinks changed from **98.3% / 82.1%** to **93.2% / 74.1%**. With both perturbations, the hot peak fell from **98.5 to 89.1 kB** and utilization changed from **99.6% / 82.9%** to **99.1% / 85.1%**. These are transmit-start-bin estimates over each run's CCT window.

In the hotspot case, maximum VOQ residence increased from **71.9 to 80.5 µs**. With both perturbations it fell from **71.4 to 67.2 µs** even though more packets entered VOQs. All releases followed TAIL-triggered arms, all VOQs drained, and no receiver reported a sequence gap. Prior-epoch events are in the summary CSV.

CE from both causes still reached active DCQCN senders in each separated case. Separated sender recovery-event counts were **205, 331, 207, and 271**, against **175, 315, 200, and 239** with shared CE, in table order. With the hotspot, median last-CNP-to-final-ACK time among marked collective flows fell from **0.897 to 0.814 ms** even as CCT increased. With both perturbations, it rose from **0.929 to 0.942 ms**, and the separated run's final flow spent **1.385 ms** between its last CNP and final ACK. The CSVs record actual recovery-rate updates per exercised QP and distinguish last-CNP tails from collective CCT.

## Which decisions did separation remove?

The shared runs provide two examples of what the gate suppresses. They describe the immediate choice at the sampled state. The separated runs have different packet timings and may draw different candidates for the same flow.

- **Potentially beneficial.** In the clean scenario, decision 40093 moved flow 100 from path 263 to path 264 after a Symphony-only exclusion blocked the first sample. In the prior complete 100 µs bin, the excluded path had a 45.1 kB sampled peak and 99.4% utilization at its busiest hop. The selected path had no sampled queue and 3.9% utilization. The separated run sent the entire flow through spine 15 from the start, so this particular late reroute disappeared while the preferred path was used anyway.
- **Potentially undesirable.** With both perturbations, decision 196611 moved flow 12 from the alternate to the injected hotspot. In the prior bin, the excluded path peaked at 56.6 kB with no RED. The chosen path peaked at 84.9 kB and had four RED marks. Both paths were near line rate. The separated run kept all of flow 12 on spine 15, eliminating that observed switch onto the hotspot.

The filter removes Symphony-only exclusions regardless of whether they happened to identify a busy path. The examples establish both types of opportunity. They cannot establish the isolated contribution of an individual decision to collective CCT, or a systematic advantage for either feedback policy.

## Verification and reproduction

The C++ suite exercises all four provenance states through the real ConWeave forwarding and VOQ path, and checks that all three CE-bearing states remain CE at delivery. Packet-level Python tests check the NOTIFY, exclusion, ACK, and active-sender CNP chains. All six point-to-point cases and all 16 Python tests passed. The separated both case matched its traced counterpart with provenance logging disabled. The four original treatments matched the previous pilot's summaries, completions, dependency waits, paths, queues, sender rate samples, VOQ records, notifications, and CNP outputs byte for byte. The workspace used a staged rebuild against its imported optimized libraries.

From ns-3.19, use a fresh output directory:

```bash
./waf build
./test.py -s devices-point-to-point

python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-five-treatment \
  --scenarios clean hotspot skew both \
  --treatments dcqcn symphony conweave conweave-symphony conweave-symphony-separated \
  --seed 1 --chunk 524288 --mark-provenance --pilot-detail --workers 4

python3 analysis/analyze_profile_f_pilot.py mix/output/profile-f-five-treatment
```

To verify the old treatments against a saved four-treatment pilot, add `--pilot-reference PATH` to the analyzer. To run only the new treatment, pass `--treatments conweave-symphony-separated`. To repeat the logging-independence check, rerun both perturbations with that treatment and `--pilot-detail`, omitting `--mark-provenance`. Set `PROFILE_F_SEPARATED_RESULTS` to the matrix root and `PROFILE_F_SEPARATED_UNTRACED` to the individual untraced run directory before running `python3 -m unittest discover -s analysis -v`. The earlier provenance and shared-CE tests use the environment variables recorded in the Profile F report.
