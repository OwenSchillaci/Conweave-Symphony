# Profile F validation and packet tracing

These checks record the standalone pilot, shared-CE integration, and mark provenance work. See the [experiment guide](profile-f.md) for the current workload.

## Standalone seed-1 pilot

These are 512 KiB runs. Each cell gives CCT in ms, then peak and time-weighted mean active steps. A logical step is active when at least one of its transfers is outstanding from QP issue through final ACK. Several concurrent QPs in the same step count once.

| Scenario | ECMP + DCQCN | Original Symphony | ConWeave + DCQCN |
|---|---:|---:|---:|
| Neither perturbation | 21.291 / 5 / 2.95 | 23.023 / 2 / 1.61 | 16.828 / 4 / 2.25 |
| Hotspot only | 36.640 / 7 / 4.17 | 29.727 / 2 / 1.60 | 21.774 / 4 / 2.22 |
| Skew only | 24.455 / 5 / 3.09 | 22.272 / 2 / 1.53 | 16.277 / 3 / 1.99 |
| Both | 27.852 / 5 / 3.07 | 33.048 / 2 / 1.63 | 22.370 / 4 / 2.42 |

### Mechanism checks

- **Reroutable hotspot.** In hotspot-only ECMP/DCQCN, ToR 12 → spine 14 generated 586 queue marks, versus 140 in the clean treatment. With both perturbations, the hot link generated 574, 371, and 227 marks under DCQCN, Symphony, and ConWeave, respectively. In all six runs with injected background, the hot link's peak queue exceeded the peak on any host-facing switch port. With both perturbations, all three treatments carried 17,582,560 background wire bytes on each pinned forward fabric hop, with zero background data on the alternate. The 1,006,680 background feedback bytes on each reverse fabric hop matched as well.
- **Independent rank skew.** In skew-only DCQCN, active-step overlap occurred during 2.977 ms of the 3.250 ms of injected processing wait. Mean active steps rose from 2.951 to 3.087. With skew only, Symphony saw 34,547 eligible packets across switches and applied 309 progress marks. The rank delay is reproducible and the workload can already develop natural skew.
- **Both conditions can occur together.** With both perturbations, the Symphony run had 48 complete 100 µs bins with active background, queue marks on 12 → 14, less than 90% utilization on 12 → 15, collective step overlap, and eligible Symphony traffic at ToR 12. In one bin beginning 3.8 ms after collective release, the hot link transmitted 124,656 wire bytes, reached a sampled queue peak of 59,736 bytes and recorded two RED marks. The alternate transmitted 4,380 wire bytes, or 3.5% of bin capacity. ToR 12 recorded 110 eligible Symphony packet observations in this bin. The observation resolution is 100 µs and eligibility is observed at switch scope.
- **ConWeave actually used the alternate.** In the both case, ConWeave sent 315 NOTIFYs and installed or refreshed 315 path exclusions. It made 239 changed path decisions with matching data physically transmitted on the selected port in the corresponding flow, epoch, and phase. Of those, 92 changed from the injected hot path to the alternate at ToR 12. The 1,347 VOQ packet releases had a maximum residence of 77.099 µs. All VOQs drained. Across the 12 runs, there were no receiver out-of-order-triggered congestion notifications or PFC receptions.

### Timing control and interpretation

The source-ToR base propagation RTT in the two-spine path setup is 4 µs. The reply deadline adds 40 µs, equivalent to serializing 50 kB at 10 Gb/s. This follows the queue budget in the existing 100 Gb/s default's comment and was chosen independently of CCT. The path exclusion lasts 80 µs, equivalent to 100 kB at line rate. The VOQ flush margin is 16 µs, connection expiry is 1 ms, and fallback VOQ wait is 500 µs.

A separate 16 KiB clean ConWeave run completed in 0.653 ms. All 283 INIT replies met the deadline, with zero queue marks, timeouts, reroutes, VOQ packets, or PFC events. The 512 KiB clean ring can itself queue traffic because four sending NICs can offer 40 Gb/s in each direction into a 20 Gb/s fabric cut. That run had 45 queue marks and 59 forwarded path changes under ConWeave. The light-load control is the timing check. The clean 512 KiB run controls for the injected perturbations.

The standalone pilot demonstrates exposure to both mechanisms. It shows that original Symphony lowered overlap even in the both case, where CCT increased. The later [ten-seed evaluation](profile-f-seeds-1-10.md) compares the combinations, and the trace walkthrough below joins a mark to a forwarded route change. This pilot uses single-NIC endpoint addresses. The dedicated-rail flow-key and ToR mapping issues remain relevant for a later multi-NIC experiment. A 100 µs bin also cannot show whether an individual progress mark freed bandwidth for a particular lagging packet.

## Reproduce

From the ns-3.19 checkout, in the existing ns-3.19 build environment with Matplotlib installed:

```bash
./waf build
python3 -m unittest discover -s analysis

MPLBACKEND=Agg python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-seed1-512k \
  --seed 1 --chunk 524288 --workers 3 --check matrix

MPLBACKEND=Agg python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-quiet \
  --seed 1 --chunk 16384 --scenarios clean --treatments conweave --check quiet
```

Use a fresh writable output directory. The runner supports `--scenarios`, `--treatments`, `--skew-rank`, `--skew-ns`, `--hotspot-manifest`, `--background-bytes`, `--background-start-ns`, and options for each ConWeave timer. A manifest row contains source host, source ToR, spine, destination ToR, destination host, source port, destination port, payload bytes and release time in ns. It describes one directed path for the hotspot. The two output checks enforce mechanism exposure and quiet control behavior without using CCT.

Each run saves its effective scenario, background offers, simulator log, completion and dependency records, per-flow path bytes, per-port 100 µs wire utilization and packet-sampled queue occupancy, mark counts, and NIC rate samples. The ConWeave outputs include NOTIFYs, exclusion updates, path decisions, packet-confirmed path changes, and VOQ residence. Wire utilization counts bytes at transmit start and can slightly exceed 100% in a bin at a packet boundary. Per-flow rates distinguish sampled pacing settings from measured wire bytes per 1 ms. The matrix writes `summary.csv`, `validation.json`, `comparison.pdf`, `step-overlap.pdf`, and `both-fabric.pdf`.

The workspace runs used a temporary rebuild of the modified translation units linked against the existing optimized ns-3 libraries because the imported build headers were unreadable under the workspace user. All eight Python tests passed. A Profile D regression run reproduced the archived seed-1 512 KiB CCTs, overlap, routes, and tuples exactly in off, original, and egress-aware modes. At the standalone pilot stage, a combined configuration was rejected. The later integration below enables it for Profile F. Profile F with Figure 4 tracing remains outside that configuration.

## ConWeave and original Symphony with shared CE

Select `--treatments conweave-symphony` to run Profile F with `LB_MODE 9`, `CC_MODE 1`, and `SYMPHONY_MODE 2`. The default treatment list and `--check matrix` still run the three standalone treatments. The simulator allows this combination through the Profile F configuration gate. The Figure 4 and dedicated-rail guards still apply.

1. The RDMA sender attaches `RingProgressTag` and `FlowStatTag` to the data packet. Symphony uses these tags for the collective, logical step, and final-packet indication.
2. Source-ToR ConWeave attaches `ConWeaveDataTag` to that packet. A spine replaces only the ConWeave tag when advancing its hop count. On a route change, ConWeave assigns the appropriate path, epoch, and phase to each packet and the other tags stay attached.
3. At each switch's normal egress dequeue, ordinary RED and original Symphony can each set the same IPv4 CE bit. An existing CE bit persists across later hops. On ingress, the destination ToR runs the existing ConWeave NOTIFY logic against CE already on the arriving packet. Its host-facing dequeue comes later, so CE added there reaches the receiver without affecting that packet's earlier NOTIFY decision.
4. The destination ToR's VOQ stores and flushes the original packet, retaining its headers and tags. After delivery, the RDMA receiver echoes CE through the existing ACK CNP flag and the sender runs the normal DCQCN rate controller.

### Shared-CE smoke test

```bash
./waf build
python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-combined-smoke \
  --scenarios both --treatments conweave-symphony --seed 1 \
  --chunk 262144 --background-bytes 2097152
```

The run completed all 112 collective transfers and both background QPs in seed 1. Collective CCT was 10.623 ms. It recorded 213 Symphony marks, 94 queue marks, 210 ConWeave NOTIFYs, 42 packet-confirmed path changes, and 238 VOQ releases. All VOQs drained. Existing packet UID traces matched 107 released VOQ packets to later Symphony processing. Three of those packets received a Symphony mark after VOQ release and reached the receiver with congestion feedback. This was a completion and tag-preservation check. The NOTIFY count includes all eligible CE arrivals and does not attribute them to individual causes.

### Shared-CE correctness checks

The C++ `devices-point-to-point` suite includes a shared-CE regression. It sends later sequences through the new spine before the old-path TAIL arrives. The test checks that the destination ToR buffers both packets, delivers TAIL before releasing them in FIFO order, and preserves their collective, step, and last-write tags. It checks that an unmarked packet generates no NOTIFY and that queue-only, Symphony-only, and dual-cause CE all generate the original path NOTIFY. The test uses the production ConWeave routing and VOQ code.

The log-based Python tests verify full completion, one transmission's worth of wire bytes per collective flow, zero receiver sequence-gap notifications, and empty VOQs. They check Symphony progress on a newly selected path and on the same packet UID after VOQ release. For both queue-only and Symphony-only marked packets, the tests match the data UID to the receiver's CNP-bearing ACK, then match the ACK UID at the sender and verify an active QP decrease in the next DCQCN check interval. Multiple CNPs can share one decrease interval.

The smoke run passed all three log checks. The same checks passed for all four combined 512 KiB seed-1 scenarios. Each run completed 112 transfers and verified all 104 rank-local dependency releases. The combined 512 KiB matrix had 256 packet-confirmed reroutes and 1,539 VOQ releases in total, with zero receiver out-of-order notifications and no residual VOQs. All 12 standalone 512 KiB reruns matched their archived summaries and exported transfers, port samples, flow paths, rates, notifications, exclusions, forwarded reroutes, VOQ residence, dependency waits, CNP, and PFC files byte for byte. The validation changes are tests only.

To reproduce the correctness checks, build with the existing Waf options and enable tests. Use fresh output directories.

```bash
./waf configure --build-profile=optimized --disable-python --enable-tests
./waf build
./test.py -s devices-point-to-point

python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-shared-ce-512k \
  --seed 1 --chunk 524288 --treatments conweave-symphony --workers 2

for scenario in clean hotspot skew both; do
  PROFILE_F_SHARED_CE_RESULTS="mix/output/profile-f-shared-ce-512k/$scenario/conweave-symphony" \
    python3 -m unittest discover -s analysis -v
done

python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-standalone-validation \
  --seed 1 --chunk 524288 --workers 3 --check matrix
```

The workspace ran all four point-to-point C++ cases using a staged test executable linked against the imported optimized libraries. The imported runner was built with tests disabled. The Python suite passed all 11 tests with combined results supplied.

The live checks cover seed 1 and the single-NIC Profile F topology. ConWeave's pre-existing VOQ timeout and prior-epoch paths still merit monitoring under other loads. Destination-ToR host-egress marks reach DCQCN after ConWeave's ingress decision. The original shared CE bit carries no mark cause, so aggregate NOTIFY counts cannot establish how many route changes were caused by Symphony.

## Observational ECN mark provenance

Use `--mark-provenance` to enable this trace in Profile F. The packet tag's two-bit mask is cumulative across marking switches.

| Mask | Cause |
|---:|---|
| 0 | Neither observed |
| 1 | Queue only |
| 2 | Symphony only |
| 3 | Both |

The switch records ordinary RED and original Symphony decisions at egress dequeue and ORs their causes into the tag. An unmarked packet can represent mask zero without a tag. The tag is simulator metadata and leaves `Packet::GetSize()` unchanged. Copies retain the existing tag and UID. A later update to a copy leaves the original tag unchanged. ConWeave forwarding replaces its own forwarding tag, while VOQ buffering stores the packet and its full tag list. The IPv4 CE bit is still used by DCQCN and by the shared-CE treatment. Separated feedback also checks the queue cause before sending a NOTIFY.

### Follow a notification to a transmitted path

| Trace event | Fields to join |
|---|---|
| `ECN_PROV_MARK` | `data_uid`, marking switch and port, local cause, cumulative cause |
| `CW_PROV_RX` | Same `data_uid`, destination ToR, observed CE and cumulative cause, path, epoch and phase |
| `CW_PROV_NOTIFY` | Maps `data_uid` to `notify_uid` at the destination ToR |
| `CW_PROV_EXCLUDE` | Same `notify_uid`, source ToR, excluded path and expiry |
| `CW_PROV_CANDIDATES` | The two sampled candidates and their usability, with the latest active `first_notify_uid` or `second_notify_uid` explaining any exclusion |
| `CW_PROV_PATH` | Same `decision_uid`, stored old and new paths and the path taken by the decision packet |
| `CW_PROV_TX` | First physical transmission for the same source switch, flow ID, epoch, phase and new egress port |

The trace records candidates at opportunities to select a path, including initial selection, flow expiry and reply timeout. A notification can be refreshed or expire before any selection. An unusable second candidate has no effect if the first is accepted. A selected path may equal the current path. Verify `changed=1` and a matching `CW_PROV_TX` before counting a transmitted reroute. On reply timeout the TAIL packet still uses the old path, so the decision packet UID can differ from the first transmitted packet on the new path. Prior-epoch packets and packets marked on the destination ToR's host-facing dequeue follow the original ConWeave rules.

In the seed-1 256 KiB smoke run, spine 15 marked data UID **32225** for Symphony only. ToR 13 observed CE with cause 2 and emitted NOTIFY UID **32597**. ToR 12 used it to exclude path **520**. Decision UID **32658** sampled 520 first, skipped it, and selected **519** for flow 31. In epoch 15, phase 1, data UID **32709** physically transmitted on path 519 via port 7. This is an observed decision chain. The trace does not determine the overall performance effect of shared CE.

### Reproduce and compare behavior

Run a matched traced and untraced case after rebuilding the test-enabled checkout.

```bash
./waf build
./test.py -s devices-point-to-point

python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-provenance \
  --scenarios both --treatments conweave-symphony --seed 1 --chunk 262144 \
  --background-bytes 2097152 --mark-provenance

python3 analysis/run_profile_f.py \
  --build build --output mix/output/profile-f-provenance-control \
  --scenarios both --treatments conweave-symphony --seed 1 --chunk 262144 \
  --background-bytes 2097152

export PROFILE_F_PROVENANCE_RESULTS=mix/output/profile-f-provenance/both/conweave-symphony
export PROFILE_F_PROVENANCE_CONTROL=mix/output/profile-f-provenance-control/both/conweave-symphony
PROFILE_F_SHARED_CE_RESULTS="$PROFILE_F_PROVENANCE_RESULTS" \
  python3 -m unittest discover -s analysis -v

rg 'data_uid=(32225|32709)\b|notify_uid=32597\b|decision_uid=32658\b' \
  "$PROFILE_F_PROVENANCE_RESULTS/collective.log"
```

Both runs completed in **10.623060 ms**. Existing simulator event records were identical, including packet IDs, timestamps, and recorded Symphony random draws. The exported CCT, queues, marks, routes, VOQ, rates, completion, and feedback files matched byte for byte. ConWeave observed 73 queue-only and 137 Symphony-only CE arrivals in the traced run. The C++ regressions cover all four states, including cumulative dual-cause marks, copying, and survival through the real forwarding and VOQ code. All five point-to-point C++ cases and 14 Python tests passed. The workspace used the staged build described above.

For the opt-in queue-only NOTIFY policy and the five-treatment results, see the [separated-feedback pilot](profile-f-separated-feedback.md).
