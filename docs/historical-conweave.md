# Original ConWeave flow benchmarks

These instructions reproduce the upstream flow workloads. Run them from the simulator repository root. Use a dedicated checkout and output directory for historical reproduction so the legacy launchers have their own output and history.

## Run
You can reproduce the simulation results of Figure 12 and 13 (FCT slowdown), Figure 16 (Queue usage per switch) by running the script:
```shell
./autorun.sh
```

In the script, you can easily change the network load (e.g., `50%`), runtime (e.g., `0.1s`), or topology (e.g., `leaf-spine`).
This takes a few hours, and requires 8 CPU cores and 10G RAM.
Note that we do not run `DRILL` since it takes too much time due to many out-of-order packets.


To run one benchmark, pass the checked-in topology name explicitly:

```sh
python3 ./run.py --topo leaf_spine_128_100G_OS2 --lb fecmp
```

Run `python3 ./run.py --help` for the remaining options.

It first calls a traffic generator `./traffic_gen/traffic_gen.py` to create an input trace.
Then, it runs NS-3 simulation script `./scratch/network-load-balance.cc`. 
Lastly, it runs FCT analyzer `./fctAnalysis.py` and switch resource analyzer `./queueAnalysis.py`.

## Plot
You can easily plot the results using the following command:
```shell
python3 ./analysis/plot_fct.py
python3 ./analysis/plot_queue.py
python3 ./analysis/plot_uplink.py
```

The outcome figures are located at `./analysis/figures`. 
1. The script requires input parameters such as `-sT` and `-fT` which indicate the time window to analyze the fct result. 
By default, it assumes to use `0.1 second` runtime. 
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

## Output
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

## Topology
To evaluate on fat-tree (K=8) topology, you can simply change the `TOPOLOGY` variable in `autorun.sh` to `fat_k8_100G_OS2`:
```shell
TOPOLOGY="leaf_spine_128_100G_OS2" # or, fat_k8_100G_OS2
```

## ConWeave parameters
We include ConWeave's parameter values into `./run.py` based on flow control model and topology.
