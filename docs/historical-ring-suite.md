# Historical ring suite

These presets reproduce the earlier 100 Gb/s ring report. For quick checks, start with [standalone validation](ring-validation.md).

The [manual ConWeave ring scenario](../config/ring_all_reduce.txt) uses four ranks.

## Scenario settings

| Key | Meaning and units |
| --- | --- |
| `TOPOLOGY_FILE` | Path relative to the simulator working directory. Link rows give endpoint IDs, rate (e.g. `100Gbps`), propagation delay (e.g. `1000ns`), and packet error probability. |
| `RING_ALL_REDUCE` | Boolean (0 or 1). Enable the built-in ring workload. |
| `RING_JOB` | Ordered host IDs followed by the iteration count. Each line defines one job. |
| `RING_CHUNK_BYTES` | Payload bytes per rank per step. Total payload per iteration is this value times `2N(N − 1)`, before headers and retransmissions. |
| `RING_PROCESSING_DELAY_NS`, `RING_JOB_DELAY_NS`, `RING_JOB_START_OFFSET_NS` | Per-rank processing delays or per-job launch offsets, in nanoseconds. Job indices are zero-based. |
| `FLOWGEN_START_TIME`, `FLOWGEN_STOP_TIME` | Simulation timestamps in seconds. For rings, the stop timestamp is the deadline for pending transfers. |
| `RANDOM_SEED` | Integer simulation seed. Keep it matched across treatments. |
| `LB_MODE`, `CC_MODE` | Numeric selectors. ECMP/FECMP uses `LB_MODE 0`, ConWeave uses `LB_MODE 9`, and DCQCN uses `CC_MODE 1`. |
| `ENABLE_PFC`, `ENABLE_QCN`, `USE_DYNAMIC_PFC_THRESHOLD` | Boolean (0 or 1) controls for priority flow control, congestion notification, and dynamic pause thresholds. |
| `BUFFER_SIZE`, `PACKET_PAYLOAD_SIZE` | Switch MMU capacity in MiB and maximum RDMA payload per data packet in bytes, respectively. |
| `L2_ACK_INTERVAL`, `L2_CHUNK_SIZE` | Receive sequence milestone increment and layer-2 chunk boundary, both in bytes. An ACK interval of 0 disables ACKs. The ring examples use 1 for ACKs on in-order packets. |
| `RATE_AI`, `RATE_HAI`, `EWMA_GAIN` | DCQCN additive and hyper-additive rate increments (e.g. `40Mb/s` and `100Mb/s`), and dimensionless smoothing gain. |
| `KMIN_MAP`, `KMAX_MAP`, `PMAX_MAP` | Entry count followed by link-rate/value pairs. Rates are in bits per second, queue thresholds in decimal kB (1,000 bytes), and maximum marking probabilities in [0, 1]. Add entries for each switch link rate. |
| `SYMPHONY_MODE`, `SYMPHONY_ADAPTIVE_ALPHA` | Mode 0 off, 1 log, 2 active. The adaptive-alpha flag is 0 for fixed alpha = 1 and 1 for adaptive alpha. |
| `SYMPHONY_WARMUP_PACKETS`, `SYMPHONY_MIN_SAMPLE_PACKETS`, `SYMPHONY_GAIN` | Packet-index warmup threshold (default 4), window packet-count threshold (default 32), and dimensionless marking gain (default 0.01, valid in (0, 1]). |

With `RING_ALL_REDUCE 1`, each `RING_JOB` line lists at least two distinct
host IDs in rank order, followed by the number of iterations. The host count
sets the rank count for that job. On the 128-host topology, these lines run a
four-rank job and an eight-rank job together:

```text
RING_JOB 0 16 32 48 3
RING_JOB 1 17 33 49 65 81 97 113 2
RING_JOB_DELAY_NS 1 0 20000 0 0 0 0 0 0
```

Jobs start together at `FLOWGEN_START_TIME` and share the configured chunk size.
Each job starts its next iteration after its own `2N(N − 1)` transfers complete,
where `N` is its rank count. `RING_JOB_DELAY_NS` takes a zero-based job index
followed by one processing delay in nanoseconds per rank. It overrides the shared
`RING_PROCESSING_DELAY_NS` list for that job. The shared list must match the rank
count of every job that uses it. Without either setting, delays are zero.
With no `RING_JOB` entries, the default is one iteration on hosts 0, 16, 32, and 48.
`RING_TRANSFER` and `RING_COMPLETE` report zero-based job and iteration IDs and
the job's rank count.
Set `FLOWGEN_STOP_TIME` far enough ahead for all jobs to finish.

The analyzer uses the effective scenario to check expected iterations and
report per-iteration collective completion time (CCT), per-job completion
time (JCT), rank start skew, and adjacent-step overlap, in nanoseconds.
Overlap counts time when both steps have at least one transfer in flight.
Without `--config`, the analyzer reports the span of observed iterations.
Check `RING_COMPLETE` counts alongside the report to catch a truncated run.

## Report workloads

The seven scenarios in `config/ring_report/` use
`leaf_spine_128_100G_OS2`, 64 KiB chunks, ten iterations per ring, and seed 1.
All jobs start at 2 s. Each ring uses four distinct hosts, and jobs within a
scenario use disjoint hosts.

| Workload | Rings | Active hosts | Leaves used |
| --- | ---: | ---: | ---: |
| `single_ring` | 1 | 4 | 4 |
| `two_shared_leaves` | 2 | 8 | 4 |
| `two_separate_leaves` | 2 | 8 | 8 |
| `quarter_occupancy` | 8 | 32 | 8 |
| `half_occupancy` | 16 | 64 | 8 |
| `full_occupancy` | 32 | 128 | 8 |
| `injected_rank_skew` | 1 | 4 | 4 |

Only `injected_rank_skew` adds a rank delay, with 20 µs on zero-based rank 1.

From the ns-3 directory, after [building the simulator](../README.md#build-the-simulator), run one workload with
ECMP/FECMP + DCQCN, the default (`LB_MODE 0`, `CC_MODE 1`).

```sh
python3 analysis/run_ring_suite.py single_ring --routing ecmp
python3 analysis/run_ring_suite.py single_ring --routing conweave
```

Run all seven workloads with each routing mode to compare the same host placements and DCQCN settings.

```sh
python3 analysis/run_ring_suite.py all
python3 analysis/run_ring_suite.py all --routing conweave
```

Each run creates `mix/output/ring_report/<workload>/<routing>/` before launch
and saves the effective `scenario.txt`, simulator stdout and stderr in
`collective.log`, and the analyzer's report in `analysis.txt`. With Matplotlib
installed in the runner's Python environment, it also writes PDF figures
alongside those files.

- `collective_completion.pdf` shows completion time by iteration, with the mean
  and range across jobs.
- `fct_slowdown.pdf` shows the transfer slowdown CDF for the fixed-size chunks.
- `uplink_imbalance.pdf` shows the uplink imbalance CDF across active ToR
  sampling intervals.
- `voq_occupancy.pdf` shows the per-ToR virtual output queue occupancy CDF for
  ConWeave runs.

Install Matplotlib in the runner's Python environment to generate the PDFs.

```sh
python3 -m pip install 'matplotlib>=3.4'
```

A repeat run with the same workload, routing, and Symphony settings overwrites
those files. Use separate output roots to compare seeds, gains, or builds.
The runner exits with an error for simulator failures, missing collective
completions, or plot generation failures. Regenerate the reports and figures
from the saved scenarios and logs without rerunning the simulator.

```sh
python3 analysis/run_ring_suite.py all --analyze-only
python3 analysis/run_ring_suite.py all --routing conweave --analyze-only
```

To analyze one log directly, run the analyzer with its saved scenario.

```sh
output=mix/output/ring_report/single_ring/ecmp
python3 analysis/analyze_ring.py "$output/collective.log" --config "$output/scenario.txt"
```

Add `--built` to launch an existing build from `build/scratch/` directly.
Add `--output-dir /tmp/ns3-ring-report` to choose a writable results root, and
use the same option when reanalyzing those results.

## Symphony with ECMP and DCQCN

Symphony is opt-in for ring-only ECMP + DCQCN scenarios with QCN and ACKs enabled.
Rebuild the simulator before running these commands from the ns-3 directory.

```sh
for workload in single_ring injected_rank_skew two_shared_leaves; do
    python3 analysis/run_ring_suite.py "$workload" --symphony off
    python3 analysis/run_ring_suite.py "$workload" --symphony log
    python3 analysis/run_ring_suite.py "$workload" --symphony active
done
```

`off` is the default. `log` records the same progress estimates and samples
the marking probability while leaving the existing queue-based marking in
effect. `active` can also mark a packet for step misalignment. Reports and
logs for these modes go in `ecmp/symphony-log/` and `ecmp/symphony-active/`
under each workload. Pass `--built --build-dir PATH` to select an existing
build and repeat `--symphony MODE` when using `--analyze-only`.

## How scheduling and throttling work

1. `StartRingIteration` starts step 0 for every rank. Each rank sends to its
   successor in the ring. There are `N − 1` reduce-scatter steps and
   `N − 1` all-gather steps. The sender's final ACK completes the queue
   pair (QP). Through `qp_finish` and `CompleteRingTransfer`, that completion
   makes the successor rank's next step ready after its processing delay.
   `RingStepReady` issues ready transfers in rank-local order. Ranks can
   therefore reach different steps at the same time. Each job waits for all
   its transfers before starting its next iteration.
2. The scheduler passes a collective iteration ID and zero-based logical
   step to the RDMA QP. `RdmaHw::GetNxtPacket` attaches them as a
   `RingProgressTag`; `FlowStatTag` identifies the final data packet.
   The switch converts the RDMA byte sequence offset to a zero-based packet
   index (PSN) by integer division by `PACKET_PAYLOAD_SIZE`. The transfer log's
   `step` field is phase-local. Add `N − 1` for all-gather to match the switch tag.
3. At dequeue each switch estimates progress independently per collective.
   A final packet advances the estimated step to the packet's step plus one
   and clears the recorded PSN. An earlier non-final step corrects the
   estimate downward. Packets at the estimated step update the maximum
   recorded PSN. The PSN resets lazily on the first packet of a new 100 µs
   window. Completion retires the collective's switch state.
4. A packet ahead of the updated estimated step is eligible when the
   recorded PSN exceeds the warmup threshold. The switch samples
   `min(1, gain × alpha × packet_PSN / recorded_PSN)`. With adaptive alpha,
   the switch counts outpacing packets against the estimate before each
   packet's update. At window rollover, if the previous window exceeded the
   minimum sample count, alpha rises by one at an outpacing share of at least
   25% and otherwise falls by one, with a floor of one. `SYMPHONY_WINDOW`
   records the counts and the alpha update. Use `--symphony-gain` for a gain
   override or `--symphony-alpha fixed` for fixed alpha.
5. In active mode a selected packet receives IPv4 ECN Congestion Experienced
   (CE), as does a packet selected by normal queue-based marking. The receiver
   returns congestion feedback on the RDMA ACK/NACK path. DCQCN processes the
   congestion notification at the sender and can reduce the QP's sending rate,
   giving lagging transfers more opportunity to use shared links.

`SYMPHONY_DEQUEUE` reports the packet's step, progress estimate, eligibility,
alpha, sampled probability and draw, and the local marking cause (`none`, `queue`,
`symphony`, or `both`). Match its `packet_uid` to `SYMPHONY_FEEDBACK`, then
match the latter's `ack_uid` to `SYMPHONY_CNP`. `SYMPHONY_RATE` records the
sender's actual DCQCN rate change for that flow. Short transfers can finish
before the DCQCN rate-decrease timer fires. For a first pass through a log,
extract the feedback chain and window updates.

```sh
log=mix/output/ring_report/injected_rank_skew/ecmp/symphony-active/collective.log
grep -E '^SYMPHONY_(WINDOW|DEQUEUE|FEEDBACK|CNP|RATE) ' "$log" > /tmp/symphony-events.txt
```

Use `mark=symphony` for a local Symphony-only decision. Inspect all dequeues
with the same packet UID before attributing end-to-end feedback, since another
switch on the path may also mark it. Rate fields are in bits per second.
For the uncompressed suite logs, pass `collective.log` directly to
`analyze_ring.py`. The paper and Figure 4 runners process their own compressed
logs and write CSVs for the plotting scripts.
