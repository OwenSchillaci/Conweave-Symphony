# Profile F paired evaluation across ten seeds

The evaluation covers 200 cases at 512 KiB: five treatments, four scenarios,
and seeds 1–10. The topology, offered traffic, DCQCN settings, and ConWeave
timers were fixed. Each run validated 112 transfers, 104 dependency
releases, background pinning, and drained VOQs.

**Main finding:** Both combined treatments consistently beat the two ECMP
baselines. Standalone ConWeave was typically faster on the skew-only and
joint cases. Separated feedback reduced NOTIFY counts and showed no
consistent CCT advantage over shared CE.

[Per-run data](../results/profile-f/run-summary.csv) ·
[Paired comparisons](../results/profile-f/paired-cct.csv) ·
[Paired summaries](../results/profile-f/paired-cct-summary.csv) ·
[Main comparison figure](../results/profile-f/paired-cct-effects.pdf)

Seeds 2–10 used compact provenance counters and 100 µs suppression bins.
Detailed replays of both combined treatments in seed 5 matched the compact
runs. Offline compaction also reproduced all applicable seed-1 metrics.
The local `run-inputs.sha256` records build and input hashes.

## CCT and paired effects

| Scenario | DCQCN | Symphony | ConWeave | Shared CE | Separated |
|---|---:|---:|---:|---:|---:|
| Neither | 22.266 | 22.685 | 17.327 | 17.413 | 17.436 |
| Hotspot | 27.733 | 32.454 | 21.408 | 22.025 | 21.654 |
| Skew | 24.059 | 22.285 | 16.719 | 17.692 | 17.914 |
| Both | 28.231 | 32.759 | 20.870 | 21.840 | 21.687 |

Cells give median collective completion time (CCT) in ms.

A positive paired improvement is (100(1 - CCT_{variant}/CCT_{reference})) within one seed and scenario. The median of these ten ratios can differ from a ratio of the marginal medians above. The intervals below are pointwise 95% percentile bootstrap intervals from 20,000 resamples of paired seed indices. With ten seeds, they are descriptive and should not be read as simultaneous tests.

| Scenario | Shared CE vs ConWeave | Separated vs ConWeave | Separated vs shared CE | Separated wins vs shared CE |
|---|---:|---:|---:|---:|
| Neither | +0.49% [−3.60, +6.28] | +0.16% [−3.05, +5.67] | +0.60% [−2.73, +2.96] | 6/10 |
| Hotspot | −1.88% [−6.09, +3.32] | −1.99% [−9.56, +3.53] | +0.19% [−5.17, +1.94] | 6/10 |
| Skew | −5.58% [−9.79, −0.43] | −8.96% [−12.10, −0.86] | −0.33% [−4.99, +0.71] | 3/10 |
| Both | −5.87% [−10.28, +0.19] | −3.48% [−9.40, +1.59] | −0.10% [−2.19, +4.39] | 5/10 |

Both combinations beat both ECMP treatments in every matched pair. Median gains versus ECMP with DCQCN were 21.5–26.1% for shared CE and 21.5–24.7% for separation, depending on the scenario. Median gains versus ECMP with Symphony were 20.7–32.3% and 19.9–33.1%, respectively. Both combinations were typically slower than standalone ConWeave in the skew-only and joint cases. Their seed-1 CCT gains over ConWeave with both perturbations recurred in only three of ten seeds for each combination. The linked paired summary includes the full set of baseline comparisons.

Separation won 20 of 40 comparisons against shared CE, with paired standard deviations of 4.9, 3.8, 4.7, and 4.6 percentage points across the four scenarios. Every interval for that comparison includes zero. The seed-1 hotspot slowdown under separation was the largest hotspot loss observed, and the seed-1 skew-only improvement occurred in three of ten seeds. The data provide no consistent CCT advantage for separation on this workload.

## Step overlap, routing, and transport

Each cell below is a median across ten seeds, with shared CE followed by separation.

| Metric | Neither | Hotspot | Skew | Both |
|---|---:|---:|---:|---:|
| Peak active steps | 2 → 2 | 2 → 2 | 2 → 2 | 2 → 2 |
| Time-weighted active steps | 1.513 → 1.513 | 1.538 → 1.568 | 1.522 → 1.514 | 1.543 → 1.544 |
| NOTIFYs, all causes | 154 → 23 | 375.5 → 164 | 154 → 29 | 352.5 → 140.5 |
| Queue-only NOTIFYs | 17 → 23 | 190.5 → 162.5 | 18 → 29 | 158.5 → 139.5 |
| Symphony-only NOTIFYs | 129.5 → 0 | 180.5 → 0 | 137 → 0 | 188 → 0 |
| Dual-cause NOTIFYs | 0 → 0 | 0.5 → 1 | 0 → 0 | 1 → 1 |
| Transmitted reroutes | 30.5 → 38.5 | 102 → 114.5 | 27 → 39.5 | 101.5 → 113.5 |
| Source reply timeouts | 63 → 58.5 | 218.5 → 181 | 47 → 62 | 207.5 → 187 |
| VOQ-buffered packets | 254 → 327 | 535 → 591.5 | 249.5 → 298 | 499.5 → 579 |
| Ordinary queue marks | 17 → 23 | 244.5 → 214 | 18 → 29 | 226.5 → 199 |
| Collective QP rate cuts | 211 → 231 | 442 → 445 | 228 → 252 | 437.5 → 391 |
| Recovery rate updates | 174 → 178 | 286 → 271.5 | 181.5 → 189.5 | 278 → 267.5 |
| Median last CNP to ACK (ms) | 0.896 → 0.896 | 0.925 → 0.998 | 0.883 → 0.881 | 0.911 → 0.913 |

Standalone ConWeave had a median peak of four active steps in each scenario, and median time-weighted overlap of 2.31–2.60. Better alignment under both combinations is consistent across the sweep. Lower CCT relative to ConWeave is not.

Separation suppressed 6,591 eligible Symphony-only arrivals across 40 runs, with zero Symphony-only NOTIFYs or exclusions. Queue-origin feedback and physical forwarding were still active. For example, the median fraction of reply timeouts that led to a confirmed transmitted reroute rose from 50.0% to 61.4% under the hotspot and from 50.2% to 60.0% with both perturbations. The fractions also rose in the other two scenarios. This is consistent with more alternate candidates being available at each timeout. A packet-level replay with identical counterfactual queues and candidate samples would be needed to isolate that mechanism.

Separation versus shared CE increased reroutes in 7, 7, 8, and 5 of ten seeds across the four scenarios. The paired median differences were +6, +14, +7.5, and +5.5 transmissions. Ordinary queue marks increased in only 5, 4, 7, and 2 seeds. Both counts increased together in 4, 4, 5, and 1 seeds. The seed-1 hotspot pattern of more ordinary congestion and rerouting therefore does not generalize as the dominant hotspot behavior.

For the hotspot, ToR 12's median injected-uplink and alternate utilization changed from 99.3% / 81.2% to 98.4% / 84.3%. The median peak queue on the injected uplink rose from 101.1 to 104.3 kB while total queue marks fell. With both perturbations, the corresponding utilization changed from 98.1% / 81.4% to 99.1% / 84.5%, and the median peak queue fell from 104.8 to 103.8 kB. These are approximate CCT-window measurements from transmit-start bins. The aggregate and fabric CSVs retain per-run rates, residence, timeouts, utilization, and queue statistics. Rate recovery has mixed direction and the post-CNP interval includes sending and ACK delay.

**Ordering caveat.** Fourteen hotspot-bearing runs recorded 42 receiver sequence-gap notifications and 15 VOQ timer-expiration flushes. All reported completion and remain in the evaluation. See the [ordering audit](profile-f-ordering-audit.md) for delivery checks and the CCT sensitivity analysis.

## Time-aligned suppression and physical congestion

For each directed path, group suppressed arrivals in aligned 500 µs epochs of the separated run. Count RED marks, queues, and forwarded reroutes away from that path in the immediately preceding 500 µs and the 500 µs after the suppression epoch. Evaluate the same directed path and time bins in the shared-CE run with the same seed. Retain only windows inside both collective lifetimes. The unit is a path-time observation, rather than an independently randomized trial. A path can appear in several windows, and the schedules may differ in the two runs.

| Scenario | Matched windows | RED after, separated / shared | Reroutes away after, separated / shared |
|---|---:|---:|---:|
| Neither | 508 | 10 / 18 | 75 / 108 |
| Hotspot | 652 | 64 / 101 | 297 / 401 |
| Skew | 524 | 17 / 9 | 108 / 90 |
| Both | 602 | 65 / 102 | 291 / 399 |

In the hotspot and joint scenarios, the separated runs had more RED-bearing windows *before* suppression than after, 149 versus 64 and 144 versus 65. The matched shared-CE windows had more RED and more reroutes away after those same epochs, even though separated runs often had more reroutes overall. Skew alone showed the opposite follow-up direction. These observations weaken a simple explanation in which suppressing a mark systematically creates congestion on the same path and then causes an extra reroute.

Detailed seed-5 traces establish that the sequence is possible. The fixed search looks for a suppressed arrival, a new ordinary queue mark up to 500 µs later on the same path, a corresponding active exclusion, a rejected first sample, and data confirmed on the changed path. It found five such decisions in the hotspot and fourteen in the joint case. For example, in the joint case, path 519 carried Symphony-only data UID 29541 at 12.729 ms. A later packet got a queue mark at 12.761 ms, causing NOTIFY 30459. The source rejected path 519 and transmitted data UID 31242 on path 520 at 12.854 ms. This ordering establishes a genuine queue-driven reroute after suppression on the same path. It does not establish that suppressing the previous mark caused the queue to grow.

On the shared-CE side of the seed-5 replay, eight forwarded reroutes had the first sample excluded by Symphony-only feedback. All eight excluded paths had a prior-bin peak sampled queue of at least 50 kB and at least one hop at 90% utilization or above. Three had ordinary RED in the prior complete 100 µs bin. This resembles the seed-1 observation that a logical mark can affect a path already carrying substantial physical load. It gives no general estimate of when the resulting reroute improves CCT.

## Reproduction

From ns-3.19, use the frozen checkout and fresh output directories. The workspace used a staged build against its imported optimized libraries. The standard build command is:

```bash
./waf build

mkdir -p mix/output
out=$(mktemp -d "$PWD/mix/output/profile-f-sweep-XXXXXX")
run_root="$out/sweep"
seed_one="$out/seed-one"
modes=(dcqcn symphony conweave conweave-symphony conweave-symphony-separated)

# Use the validated seed-1 tree or create it with the same settings.
python3 analysis/run_profile_f.py --build build --output "$seed_one" \
  --seed 1 --chunk 524288 --scenarios clean hotspot skew both \
  --treatments "${modes[@]}" --mark-provenance --pilot-detail --no-plots --workers 8

for seed in {2..10}; do
  python3 analysis/run_profile_f.py --build build --output "$run_root/seed-$seed" \
    --seed "$seed" --chunk 524288 --scenarios clean hotspot skew both \
    --treatments "${modes[@]}" --mark-provenance --pilot-detail --compact --no-plots --workers 8 || exit 1
done

python3 analysis/run_profile_f.py --build build --output "$run_root/trace-seed-5" \
  --seed 5 --chunk 524288 --scenarios hotspot both \
  --treatments conweave-symphony conweave-symphony-separated \
  --mark-provenance --pilot-detail --no-plots --workers 2

python3 analysis/analyze_profile_f_sweep.py "$run_root" \
  --seed-one "$seed_one" --trace-seed-five "$run_root/trace-seed-5"
```

The analyzer exports 200 per-run rows, 280 per-seed CCT contrasts, 28 contrast estimates, path-level fabric statistics, 2,286 matched suppression windows, and the seed-5 packet chains. Plots use paired seed trajectories, points with median bootstrap intervals, and distribution panels for the mechanisms.
