# Archived experiment inputs

These files were moved out of the active configuration directories. Supported
experiment runners and regression tests remain at their original paths.

| Previous path | Archived path | Purpose |
| --- | --- | --- |
| `config/ring_concurrent.txt` | [ring/ring_concurrent.txt](ring/ring_concurrent.txt) | Manual two-ring concurrent workload. Preserve alongside any earlier `ring_concurrent` outputs. |
| `config/ring_mixed_sizes.txt` | [ring/ring_mixed_sizes.txt](ring/ring_mixed_sizes.txt) | Manual concurrent four-rank and eight-rank workload. |
| `config/fat_k4_100G_OS2.txt` | [conweave/fat_k4_100G_OS2.txt](conweave/fat_k4_100G_OS2.txt) | Legacy topology referenced by name in historical plot metadata. The supported ConWeave flow launchers use the 128-host leaf–spine or the k=8 fat-tree. |
| `config/fat_topology_gen.py` | [conweave/fat_topology_gen.py](conweave/fat_topology_gen.py) | Standalone fat-tree generator with a k=12 default. |
| `workloads/` | [conweave/workloads/](conweave/workloads/) | Legacy workload distributions and Tcl-format data. |

Launch the archived manual scenarios from the simulator root so relative
topology paths resolve. If replaying, copy the scenario to a fresh results
directory and redirect its output paths first. The archived files preserve
their original contents and historical output names.
