# Profile F workload

Profile F exercises reroutable congestion and ring-step misalignment on a
two-path fabric. Use the [README smoke test](../README.md#run-profile-f)
for a quick check and the [ten-seed recipe](profile-f-seeds-1-10.md#reproduction)
for the full 512 KiB evaluation.

## Topology and perturbations

Each host has one 10 Gb/s NIC. ToRs 12 and 13 each connect to spines 14
and 15 over 10 Gb/s links. Every link has 1 µs one-way propagation delay.
The eight-rank ring alternates between racks in the order
`0 → 4 → 1 → 5 → 2 → 6 → 3 → 7 → 0`.

The ring starts at 10 ms. Seven reduce-scatter steps and seven all-gather
steps produce 112 transfers. At 512 KiB per transfer, each rank contributes
4 MiB and the ring sends 56 MiB of payload across racks. A rank can issue its
next step after the predecessor transfer is ACKed and any local processing
delay has elapsed.

| Scenario | Hotspot | Rank skew |
| --- | --- | --- |
| `clean` | Off | Off |
| `hotspot` | On | Off |
| `skew` | Off | On |
| `both` | On | On |

- **Hotspot.** Hosts 8 and 9 each send 8 MiB to separate receivers, hosts 10
  and 11, starting at 9.95 ms. Their data follows ToR 12 → spine 14 → ToR 13,
  with ACKs on the reverse path. Collective flows use the selected routing
  policy. Spine 15 is the equal-cost alternate.
- **Rank skew.** Rank 1 (host 4) waits an additional 250 µs after each
  predecessor ACK for its 13 dependent transfers. The initial launch is
  unaffected.

The runner verifies offered traffic, pinned routes, transfer counts, and
rank-local dependency release times. It holds the workload and DCQCN
configuration fixed across treatments. Dedicated rails belong to the
separate ECMP-based Figure 4 experiments.

## Treatments

| Runner selector | Routing and congestion feedback |
| --- | --- |
| `dcqcn` | ECMP + DCQCN |
| `symphony` | ECMP + DCQCN + original Symphony |
| `conweave` | ConWeave + DCQCN |
| `conweave-symphony` | ConWeave + DCQCN + original Symphony with shared CE |
| `conweave-symphony-separated` | ConWeave + DCQCN + original Symphony with queue-only NOTIFY eligibility |

Both combined treatments let DCQCN react to CE from either marking cause.
Shared CE lets either cause trigger an otherwise eligible ConWeave NOTIFY.
Separated feedback requires a queue cause, including packets marked for
both reasons. ConWeave checks CE at destination-ToR ingress. A mark added
at that ToR's host-facing dequeue can still reach DCQCN.

The runner defaults to the three standalone treatments. The
[ten-seed recipe](profile-f-seeds-1-10.md#reproduction) explicitly selects
all five and saves the inputs and measurements for each run.
