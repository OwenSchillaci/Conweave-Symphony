# Historical Symphony experiments

Run commands from the simulator repository root after [building ns-3](../README.md#build-the-simulator). These experiments document the earlier reproduction work. See the [Profile F guide](profile-f.md) for the current evaluation.

## Symphony paper comparisons

Use `analysis/run_symphony_paper.py` with a built simulator and a writable
output directory. Run the stages `chunks`, `gain`, `contention`, `overlap`,
`motivation`, `tenants`, and `scales` with the same output directory. Each
stage saves its effective scenarios, compressed logs, and processed CSVs.
Use a fresh directory when comparing simulator builds. The plotting script
expects those stage CSVs and the two-flow runner outputs under
`figure9/0.001`, `figure9/0.003`, and `figure9/0.01` in that directory.

```sh
mkdir -p mix/output
out=$(mktemp -d "$PWD/mix/output/symphony-paper-XXXXXX")
for gain in 0.001 0.003 0.01; do
    python3 analysis/run_symphony_two_flow.py --gain "$gain" --output-dir "$out/figure9/$gain"
done
for stage in chunks gain contention overlap motivation tenants scales; do
    python3 analysis/run_symphony_paper.py "$stage" --build build --output "$out"
done
python3 analysis/plot_symphony_paper.py "$out"
```

For custom ring scenarios, `RING_JOB_START_OFFSET_NS <job> <offset>` delays
a job relative to `FLOWGEN_START_TIME`. `RING_EGRESS_TRACE 1` adds
`RING_EGRESS` records with per-job payload bytes on receiver-facing switch
links in 100 µs bins, including retransmissions.

## Figure 4 reconstruction

The base scenario is `config/symphony_figure4_scenario.txt`. It uses the
shared-NIC topology by default. Pass `--dedicated-rails` to select
`config/symphony_figure4_dedicated_topology.txt`. This example uses a fresh
output directory for each run.

```sh
mkdir -p mix/output
out=$(mktemp -d "$PWD/mix/output/symphony-figure4-XXXXXX")
python3 analysis/run_symphony_figure4.py --build build --output "$out/smoke" --dedicated-rails --chunk 65536
# Full-size historical reconstruction
python3 analysis/run_symphony_figure4.py --build build --output "$out/full" --dedicated-rails --seeds 1 --modes off on late
```

The four channels model one collective. With the default 8,000,000 bytes
per transfer, each run sends 63,488,000,000 payload bytes across 7,936 transfers.
Use `--seeds` for matched off/on pairs and `--workers` to run simulator
processes concurrently. Use `--run-only` to skip plotting. Use `--plot-only` to regenerate plots
from completed runs. Seed 1 records routes, sampled switch decisions and
100 µs same-egress activity. Other seeds use compact logs by default.

## Limitations and differences from the Symphony paper

- The default report suite uses ACK-driven, one-dimensional communication
  workloads on a 100 Gb/s leaf–spine fabric. It models network dependencies
  and optional fixed rank delays. The paper uses Astra-Sim over ns-3, 10 Gb/s
  links, and 2D rings for its default simulations. Tensor reduction, training
  compute graphs, and end-to-end Transformer JCT are outside this model.
  [Symphony paper, §§4.1 and 4.4](https://arxiv.org/html/2604.16880v1#S4)
- `RingProgressTag` is simulator metadata with no wire-byte overhead. Local
  marking runs at switch dequeue, with progress retired by the scheduler.
  The model also skips adaptive updates for windows with too few packets.
  The local comparison for the Tofino2 hardware experiment uses a two-flow ns-3
  workload with much smaller transfers and start offsets. Hardware timing,
  queue behavior, and signal attribution remain unverified by that workload.
  [Symphony paper, §3](https://arxiv.org/html/2604.16880v1#S3)
  · [§4.7](https://arxiv.org/html/2604.16880v1#S4.SS7)
- Figure 4 uses reconstructed channel placement and link delays. In the
  documented seed-1 full-size pilot, always-on Symphony reduced peak
  collective-wide overlap from 17 to 3 steps while increasing CCT from
  4.926 s to 5.640 s. The paper reported roughly 30% lower CCT for its Figure 4
  experiment. The local full-size ECDF has one matched seed, and the detailed
  RDMA trace cap was reached before the first Symphony marks. More matched
  seeds and an uncapped or targeted feedback trace are needed to assess
  repeatability and confirm the mark-to-CNP chain at full size.
  [Local Figure 4 report](symphony-figure4.txt)
  · [Symphony paper, §4.2](https://arxiv.org/html/2604.16880v1#S4.SS2)
- The local gain and chunk-size sweeps also found slowdowns. In the
  eight-rank host-skew workload, the 8 MiB case took 7.71× the matched baseline
  CCT, while the paper reports improvements up to about 20% for larger chunks.
  A faithful step-aware strict-priority comparator and the paper's training
  workloads remain open. The local report records workload and measurement
  differences for each panel.
  [Local paper comparisons](symphony-paper-comparison.txt)
  · [Symphony paper, §4.6](https://arxiv.org/html/2604.16880v1#S4.SS6)
- The built-in ring scheduler replaces flow-trace scheduling for ring scenarios.
  Symphony requires ECMP + DCQCN with QCN and ACKs enabled. ConWeave is a
  separate routing comparison. Short aligned rings can show no eligible
  Symphony marks, and a switch mark alone does not establish that the sender
  slowed before finishing.
