# ConWeave and Symphony experiments in ns-3.19

Profile F evaluates ConWeave and Symphony together on an eight-rank ring
with a controllable network hotspot and rank processing delay.

- [Workload and treatments](docs/profile-f.md)
- [Ten-seed findings and reproduction commands](docs/profile-f-seeds-1-10.md)
- [Committed results and comparison figure](results/profile-f/README.md)
- [Validation and historical references](docs/experiment-reference.md)

## Build the simulator

Run from the simulator repository root, which contains `waf` and `config/`.
In the all-in-one checkout, use `ns-3.19/`. The Docker image includes
legacy Python 2 for Waf and Python 3 with Matplotlib for analysis.

```sh
docker build -t conweave-symphony .
mkdir -p mix/output
docker run --rm -it --user "$(id -u):$(id -g)" \
    -v "$PWD/mix/output:/simulator/mix/output" conweave-symphony bash
```

For a native build with the [same dependencies](Dockerfile), run
`./waf configure --build-profile=optimized --disable-python` and `./waf build`.
Add `--enable-tests` when configuring for C++ tests.

## Run Profile F

This smoke test uses 16 KiB chunks in the clean scenario. Each treatment
should complete 112 transfers. New results appear in `"$out/smoke"`.

```sh
mkdir -p mix/output
out=$(mktemp -d "$PWD/mix/output/profile-f-check-XXXXXX")
python3 analysis/run_profile_f.py --build build --output "$out/smoke" \
    --seed 1 --chunk 16384 --scenarios clean \
    --treatments dcqcn symphony conweave conweave-symphony conweave-symphony-separated
```

Run `python3 -m unittest discover -s analysis -v` for Python checks and
`./test.py -s devices-point-to-point` with a test-enabled build.

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
