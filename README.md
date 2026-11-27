# NS-3 Simulator for RDMA Network Load Balancing

This is a Github repository for the SIGCOMM'23 paper "[Network Load Balancing with In-network Reordering Support for RDMA](https://doi.org/10.1145/3603269.3604849)".

## Quick start from a fresh clone

Clone the team repository and run the commands from its root. The Docker
image builds the simulator from that checkout and provides Python 3 with
Matplotlib for analysis.

```sh
git clone https://github.com/OwenSchillaci/Conweave-Symphony.git
cd Conweave-Symphony
docker build -t conweave-symphony .
mkdir -p mix/output
docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD/mix/output:/simulator/mix/output" \
    conweave-symphony bash -ec '
        python3 analysis/run_ring_suite.py single_ring --built --routing ecmp
        python3 analysis/run_ring_suite.py single_ring --built --routing conweave
        python3 analysis/run_ring_suite.py single_ring --built --symphony active
    '
```

Each command should report ten completed collectives. Logs, effective
scenarios, and PDFs appear on the host under `mix/output/ring_report/single_ring/`.
The last run uses `ecmp/symphony-active/`. For the remaining experiments,
open a shell in the image with the same output mount.

```sh
docker run --rm -it --user "$(id -u):$(id -g)" \
    -v "$PWD/mix/output:/simulator/mix/output" conweave-symphony bash
```

Append `--built` to the ring-suite launch commands in the container. Keep
custom output paths under `mix/output/` so the results survive container exit.
Rebuild the image after changing simulator code or scenarios.

### Building natively

On Ubuntu 20.04, the [Dockerfile](Dockerfile) lists the build and plotting
packages and Matplotlib version. Legacy Waf runs under Python 2 and the analysis scripts run under
Python 3. From the repository root, after installing the packages, run
`./waf configure --build-profile=optimized --disable-python` and
`./waf build`. If your machine lacks a compatible Python 2 environment,
use the container.

### Sharing changes and results

Commit simulator source, experiment scenarios and topologies, analysis
scripts, build files, and documentation. Include newly added headers and
scenario files alongside the code that uses them. `mix/.history` is a tracked
header for the original ConWeave tools. Leave local run entries out of commits.

Keep `build/`, `.waf*`, `mix/output/`, and generated files under
`analysis/figures/` local. The runners regenerate logs, CSVs, and plots from the
committed inputs. Use separate artifact storage if the team needs to share
full run archives. The comparison summaries are in [docs/](docs/).

## Original ConWeave experiments

### Run
You can reproduce the simulation results of Figure 12 and 13 (FCT slowdown), Figure 16 (Queue usage per switch) by running the script:
```shell
./autorun.sh
```

In the script, you can easily change the network load (e.g., `50%`), runtime (e.g., `0.1s`), or topology (e.g., `leaf-spine`).
This takes a few hours, and requires 8 CPU cores and 10G RAM.
Note that we do not run `DRILL` since it takes too much time due to many out-of-order packets.


If you want to run the simulation individually, try this command:
```shell
python3 ./run.py --h
```

It first calls a traffic generator `./traffic_gen/traffic_gen.py` to create an input trace.
Then, it runs NS-3 simulation script `./scratch/network-load-balance.cc`. 
Lastly, it runs FCT analyzer `./fctAnalysis.py` and switch resource analyzer `./queueAnalysis.py`. 


##### Ring all-reduce

Use the build from the quick start. Run these commands from the simulator
repository root (`ns-3.19/` in the all-in-one layout) so the simulator can
resolve topology paths.

Run one four-rank ECMP + DCQCN iteration with a 64 KiB chunk per transfer.
The command copies the report scenario into a temporary directory and directs
all output there.

```sh
out=$(mktemp -d)
sed -e 's/^RING_JOB 0 16 32 48 10$/RING_JOB 0 16 32 48 1/' \
    -e "s|mix/output/ring_report/single_ring/ecmp/|$out/|g" \
    config/ring_report/single_ring.txt > "$out/scenario.txt"
./waf --run "scratch/network-load-balance $out/scenario.txt" > "$out/collective.log" 2>&1
grep '^RING_COMPLETE' "$out/collective.log"
python3 analysis/analyze_ring.py "$out/collective.log" --config "$out/scenario.txt"
```

Expect one `RING_COMPLETE job=0 iteration=0 ranks=4 transfers=24` record
and six logical steps in the analysis. If you already have a compatible build,
you can replace the Waf launch line with the direct launch below. This also
works on hosts whose Python cannot run legacy Waf.

```sh
LD_LIBRARY_PATH="$PWD/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    build/scratch/network-load-balance "$out/scenario.txt" > "$out/collective.log" 2>&1
```

The checked-in [ConWeave example](config/ring_all_reduce.txt) runs the same
four-rank smoke workload with `LB_MODE 9`.

```sh
mkdir -p mix/output/ring
./waf --run 'scratch/network-load-balance config/ring_all_reduce.txt' > mix/output/ring/collective.log 2>&1
```

###### Scenario settings

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

###### Report workloads

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

From the ns-3 directory, after configuring Waf as above, run one workload with
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

###### Symphony with ECMP and DCQCN

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

###### How scheduling and throttling work

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

###### Symphony two-flow bottleneck experiment

Build the simulator, then run the matched comparison from the ns-3 directory:

```sh
python3 analysis/run_symphony_two_flow.py
```

The runner uses `config/symphony_two_flow_scenario.txt` and changes only the Symphony
mode and output paths between cases. It sends two 16 MiB RDMA flows to one
receiver over a shared 10 Gb/s egress. The step-1 flow starts 6.7 ms before
the step-0 flow. The treatment uses fixed alpha and gain 0.01.

Results go to `mix/output/symphony_two_flow/`. The directory contains the
effective scenarios, raw logs, 100 µs payload throughput CSVs, and the
two-panel `throughput.pdf`. The runner prints ACK-based completion times,
in-flight overlap, and verified Symphony mark-to-CNP counts. Use
`--build-dir` to select a build and `--output-dir` to choose a results directory.

###### Symphony paper comparisons

Use `analysis/run_symphony_paper.py` with a built simulator and a writable
output directory. Run the stages `chunks`, `gain`, `contention`, `overlap`,
`motivation`, `tenants`, and `scales` with the same output directory. Each
stage saves its effective scenarios, compressed logs, and processed CSVs.
Use a fresh directory when comparing simulator builds. The plotting script
expects those stage CSVs and the two-flow runner outputs under
`figure9/0.001`, `figure9/0.003`, and `figure9/0.01` in that directory.

```sh
out=mix/output/symphony-paper
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

###### Figure 4 reconstruction

The dedicated topology and scenario are `config/symphony_figure4_topology.txt`
and `config/symphony_figure4_scenario.txt`. From a rebuilt checkout:

```sh
out=mix/output/symphony-figure4-rerun
python3 analysis/run_symphony_figure4.py --build build --output "$out" --chunk 65536
python3 analysis/run_symphony_figure4.py --build build --output "$out" --seeds 1 --modes off on late
```

The four channels model one collective. With the default 8,000,000 bytes
per transfer, each run sends 63,488,000,000 payload bytes across 7,936 transfers.
Use `--seeds` for matched off/on pairs and `--workers` to run simulator
processes concurrently. Use `--run-only` to skip plotting. Use `--plot-only` to regenerate plots
from completed runs. Seed 1 records routes, sampled switch decisions and
100 µs same-egress activity. Other seeds use compact logs by default.

###### Limitations and differences from the Symphony paper

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
  [Local Figure 4 report](docs/symphony-figure4.txt)
  · [Symphony paper, §4.2](https://arxiv.org/html/2604.16880v1#S4.SS2)
- The local gain and chunk-size sweeps also found slowdowns. In the
  eight-rank host-skew workload, the 8 MiB case took 7.71× the matched baseline
  CCT, while the paper reports improvements up to about 20% for larger chunks.
  A faithful step-aware strict-priority comparator and the paper's training
  workloads remain open. The local report records workload and measurement
  differences for each panel.
  [Local paper comparisons](docs/symphony-paper-comparison.txt)
  · [Symphony paper, §4.6](https://arxiv.org/html/2604.16880v1#S4.SS6)
- The built-in ring scheduler replaces flow-trace scheduling for ring scenarios.
  Symphony requires ECMP + DCQCN with QCN and ACKs enabled. ConWeave is a
  separate routing comparison. Short aligned rings can show no eligible
  Symphony marks, and a switch mark alone does not establish that the sender
  slowed before finishing.

##### Plot
You can easily plot the results using the following command:
```shell
python3 ./analysis/plot_fct.py
python3 ./analysis/plot_queue.py
python3 ./analysis/plot_uplink.py
```

The outcome figures are located at `./analysis/figures`. 
1. The script requires input parameters such as `-sT` and `-fT` which indicate the time window to analyze the fct result. 
By default, it assuems to use `0.1 second` runtime. 
2. `plot_fct.py` plots the Average and 99-percentile FCT result and give comparisons between frameworks. It excludes `5ms` of warm-up and `50ms` of cool-down period in measurements. You can control these numbers in `run.py`:
```python
fct_analysis_time_limit_begin = int(flowgen_start_time * 1e9) + int(0.005 * 1e9)  # warmup
fct_analysistime_limit_end = int(flowgen_stop_time * 1e9) + int(0.05 * 1e9)  # extra term
```
or, directly put parameters into `plot_fct.py`. Use `-h` for details. 
3. `plot_queue.py` plots the CDF of queue volume usage per switch for ConWeave. It excludes `5ms` of warm-up period, and cool-down period is not used as it would _underestimate_ the overhead. Similarly, you can control this number in `run.py`:
```python
queue_analysis_time_limit_begin = int(flowgen_start_time * 1e9) + int(0.005 * 1e9)  # warmup
queue_analysistime_limit_end = int(flowgen_stop_time * 1e9) # no extra term!!
```
or, directly put parameters into `plot_queue.py`. Use `-h` for details. 
4. `plot_uplink.py` plots the load balance efficiency with ToR uplink utility. By default, it captures uplink throughputs for every `100µs` and measure the variations. It excludes `5ms` of warm-up and `50ms` of cool-down period in measurements. 
Or, directly put parameters into `plot_uplink.py`. Use `-h` for details. 

##### Output
As well as above figures, other results are located at `./mix/output`, such as uplink usage (Figure 14), queue number usage per port (Figure 15), etc.

* At `./mix/output`, several raw data is stored such as 
  * Flow Completion Time (`XXX_out_fct.txt`), - Figure 12, 13
  * PFC generation (`XXX_out_pfc.txt`), 
  * Uplink's utility (`XXX_out_uplink.txt`), - Figure 14
  * Number of connections (`XXX_out_conn.txt`), 
  * Congestion Notification Packet (`XXX_out_cnp.txt`).
  * CDF of number of queues usage per egress port (`XXX_out_voq_per_dst_cdf.txt`). - Figure 15 
  * CDF of total queue memory overhead per switch (`XXX_out_voq_cdf.txt`). - Figure 16
  
* Each run of simulation creates a repository in `./mix/output` with simulation ID (10-digit number).
* Inside the folder, you can check the simulation config `config.txt` and output log `config.log`. 
* The output files include post-processed files such as CDF results.
* The history of simulations will be recorded in `./mix/.history`. 

##### Topology
To evaluate on fat-tree (K=8) topology, you can simply change the `TOPOLOGY` variable in `autorun.sh` to `fat_k8_100G_OS2`:
```shell
TOPOLOGY="leaf_spine_128_100G_OS2" # or, fat_k8_100G_OS2
```

##### Clean up
To clean all data of previous simulation results, you can run the command:
```shell
./cleanup.sh
```

#### ConWeave Parameters
We include ConWeave's parameter values into `./run.py` based on flow control model and topology.  


### Simulator Structure
The all-in-one directory bundles ns-3, Bake, PyBindGen, and NetAnim. The
experiment code and inputs live under `ns-3.19/`.

| Path | Role and entry points |
| --- | --- |
| [scratch/network-load-balance.cc](scratch/network-load-balance.cc) | Scenario parser and simulator entry point. `RingJob`, `ScheduleRingJobs`, `StartRingIteration`, `RingStepReady`, `qp_finish`, and `CompleteRingTransfer` implement rank and iteration dependencies. |
| [config/](config/) | Topologies, direct scenarios, report workload templates in `ring_report/`, and dedicated Symphony comparison inputs. |
| [analysis/](analysis/) | `run_ring_suite.py` runs workloads and reanalyzes outputs. `analyze_ring.py` parses completion records and `plot_ring.py` generates the suite PDFs. `run_symphony_*.py` drives the targeted comparisons. |
| [rdma-driver.cc](src/point-to-point/model/rdma-driver.cc), [rdma-queue-pair.h](src/point-to-point/model/rdma-queue-pair.h), [ring-progress-tag.h](src/point-to-point/model/ring-progress-tag.h) | `RdmaDriver::AddQueuePair` passes the scheduling metadata to `RdmaQueuePair`. `RingProgressTag` carries the collective ID and logical step to switches. |
| [rdma-hw.cc](src/point-to-point/model/rdma-hw.cc) | `RdmaHw::GetNxtPacket` tags data. `ReceiveUdp` produces feedback and `ReceiveAck` processes ACKs and congestion notifications. `cnp_received_mlx` and the DCQCN rate-decrease timer handle sender response. |
| [switch-node.cc](src/point-to-point/model/switch-node.cc), [switch-mmu.cc](src/point-to-point/model/switch-mmu.cc) | `SwitchNode::GetOutDev` selects a path. `SwitchNotifyDequeue` combines `SymphonyMark` and `SwitchMmu::ShouldSendCN`. `StartSymphonyCollective` and `FinishSymphonyCollective` manage switch state. |
| [conweave-routing.cc](src/point-to-point/model/conweave-routing.cc), [conweave-voq.cc](src/point-to-point/model/conweave-voq.cc) | `ConWeaveRouting` handles path changes and `ConWeaveVOQ` buffers packets for in-network reordering. |
| `mix/output/`, `build/scratch/` | Default run artifacts and built simulator executables, respectively. |

Most implementations of network load balancing are located in the directory `./src/point-to-point/model`.

* `switch-node.h/cc`: Switching logic that includes a default multi-path routing protocol (e.g., ECMP) and DRILL.
* `switch-mmu.h/cc`: Ingress/egress admission control and PFC.
* `conga-routing.h/cc`: Conga routing protocol.
* `letflow-routing.h/cc`: Letflow routing protocol.
* `conweave-routing.h/cc`: ConWeave routing protocol.
* `conweave-voq.h/cc`: ConWeave in-network reordering buffer.
* `settings.h/cc`: Global variables for logging and debugging.
* `rdma-hw.h/cc`: RDMA-enable NIC behavior model.

<b> RNIC behavior model to out-of-order packet arrival </b>
As disussed in the paper, we observe that RNIC reacts to even a single out-of-order packet sensitively by sending CNP packet.
However, existing RDMA-NS3 simulator (HPCC, DCQCN, TLT-RDMA, etc) did not account for this.
In this simulator, we implemented that behavior in `rdma-hw.cc`.


## Citation
If you find this repository useful in your research, please consider citing:
```
@inproceedings{song2023conweave,
  title={Network Load Balancing with In-network Reordering Support for RDMA},
  author={Song, Cha Hwan and Khooi, Xin Zhe and Joshi, Raj and Choi, Inho and Li, Jialin and Chan, Mun Choon},
  booktitle={Proceedings of SIGCOMM},
  year={2023}
}
```

## Credit
This code repository is based on [https://github.com/alibaba-edu/High-Precision-Congestion-Control](https://github.com/alibaba-edu/High-Precision-Congestion-Control) for Mellanox Connect-X based RDMA-enabled NIC implementation, and [https://github.com/kaist-ina/ns3-tlt-rdma-public.git](https://github.com/kaist-ina/ns3-tlt-rdma-public.git) for Broadcom switch's shared buffer model and IRN implementation.

```
MIT License

Copyright (c) 2023 National University of Singapore

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
