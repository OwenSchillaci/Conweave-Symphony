# Profile F ten-seed results

This bundle contains the existing 512 KiB evaluation. It covers four
scenarios (clean, hotspot, skew, and both), five treatments, and seeds 1–10.
Each comparison pairs results from the same scenario and seed.

| File | Contents |
| --- | --- |
| [run-summary.csv](run-summary.csv) | All 200 runs, including CCT in nanoseconds, step overlap, and routing and transport counters. |
| [paired-cct.csv](paired-cct.csv) | The 280 per-seed CCT comparisons. The CCT columns here are in milliseconds. |
| [paired-cct-summary.csv](paired-cct-summary.csv) | The 28 groups of paired comparisons, including median improvements, variability, and 95% bootstrap intervals. |
| [paired-cct-effects.pdf](paired-cct-effects.pdf) | Figure of the median paired CCT improvements and their uncertainty. |

Positive `improvement_pct` means the variant completed faster. It is
`100 × (1 − variant CCT / baseline CCT)`.

These files were copied from `analysis/figures/profile-f-seeds-1-10/`.
The full run logs and packet traces stay in the local artifact trees.
The source checkout includes the [runner](../../analysis/run_profile_f.py)
and the [sweep analyzer](../../analysis/analyze_profile_f_sweep.py).
