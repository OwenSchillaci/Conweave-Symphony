# Saved runs and test fixtures

The [published Profile F bundle](../results/profile-f/README.md) contains
the summary CSVs and main figure. Full runs live in ignored directories.
Use a new path under `mix/output/` for a new run.

In the research checkout, these saved directories are under
`analysis/figures/`. Use the path where the data was restored when
following commands that refer to `mix/output/`.

| Directory | Use |
| --- | --- |
| `profile-f-five-treatment/` | Seed-1 matrix for the sweep, ordering audit, and separated-feedback tests. |
| `profile-f-seeds-1-10/` | Seeds 2–10, detailed replays in `trace-seed-5/`, and original build and input hashes in `run-inputs.sha256`. |
| `profile-f-ordering/` | Detailed ordering replays and packet and payload audit CSVs. |
| `profile-f-provenance/`, `profile-f-provenance-control/`, `profile-f-separated-untraced/` | Packet-level regression fixtures. |

Keep the bundle's effective configs, logs or compact records, and input
hashes when sharing full runs. The local `profile-f-artifacts.sha256`
lists checksums for the saved Profile F artifacts.

## Trace-backed tests

Generate missing fixtures with the [shared CE and provenance recipe](profile-f-validation.md#reproduce-and-compare-behavior)
and the [separated-feedback recipe](profile-f-separated-feedback.md#verification-and-reproduction).

```sh
fixtures="$PWD/analysis/figures"
export PROFILE_F_SHARED_CE_RESULTS="$fixtures/profile-f-provenance/both/conweave-symphony"
export PROFILE_F_PROVENANCE_RESULTS="$PROFILE_F_SHARED_CE_RESULTS"
export PROFILE_F_PROVENANCE_CONTROL="$fixtures/profile-f-provenance-control/both/conweave-symphony"
export PROFILE_F_SEPARATED_RESULTS="$fixtures/profile-f-five-treatment"
export PROFILE_F_SEPARATED_UNTRACED="$fixtures/profile-f-separated-untraced/both/conweave-symphony-separated"
python3 -m unittest discover -s analysis -v
```

## Reanalyze a saved sweep

The analyzer writes CSVs and plots to its results directory. Use a copy when
working from saved data.

```bash
saved="$PWD/analysis/figures"
out=$(mktemp -d)
cp -a "$saved/profile-f-seeds-1-10"/seed-{2..10} \
      "$saved/profile-f-seeds-1-10/trace-seed-5" "$out/"
python3 analysis/analyze_profile_f_sweep.py "$out" \
  --seed-one "$saved/profile-f-five-treatment" \
  --trace-seed-five "$out/trace-seed-5"
```
