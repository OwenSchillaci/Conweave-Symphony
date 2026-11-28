# Standalone checks

Use a built simulator and run from the repository root. These checks
write to a new output directory.

```sh
out=$(mktemp -d)

# ECMP + DCQCN, a single four-rank ring on the 100 Gb/s reference fabric.
python3 analysis/run_ring_suite.py single_ring --built --routing ecmp \
    --output-dir "$out/ring"

# ConWeave timing on an unloaded Profile F fabric.
python3 analysis/run_profile_f.py --build build --output "$out/quiet" \
    --seed 1 --chunk 16384 --scenarios clean --treatments conweave --check quiet

# DCQCN vs fixed-alpha Symphony across a shared 10 Gb/s egress.
python3 analysis/run_symphony_two_flow.py --build-dir build \
    --output-dir "$out/two-flow"
```

The ECMP ring should report ten completed collectives. The quiet check
expects no queue CE, reply timeouts, reroutes, or receiver sequence gaps,
and all INIT replies should be timely. The two-flow runner reports verified
Symphony mark-to-CNP counts and writes `throughput.pdf`.

See the [historical ring suite](historical-ring-suite.md) for the older
occupancy sweep, additional presets, and scenario settings.
