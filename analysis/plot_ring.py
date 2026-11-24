"""Generate per-run PDF figures from ring completion and monitor logs."""

from collections import defaultdict
from statistics import mean


def read_samples(path, separator=None):
    with path.open() as stream:
        for line in stream:
            if line.strip():
                yield list(map(int, line.strip().split(separator)))


def uplink_imbalance(path):
    previous = {}
    intervals = defaultdict(list)
    for timestamp, switch, port, total in read_samples(path, ","):
        key = (switch, port)
        if key in previous:
            intervals[timestamp, switch].append(total - previous[key])
        previous[key] = total
    return [
        100 * (max(ports) - min(ports)) / mean(ports)
        for ports in intervals.values()
        if sum(ports) > 0
    ]


def plot_run(output, workload, routing, completions):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ValueError("plotting requires Matplotlib in the runner's Python environment") from exc

    title = f"{workload.replace('_', ' ')} · {routing}"

    def save(fig, ax, filename):
        ax.set_title(title)
        ax.grid(alpha=0.25)
        fig.savefig(output / filename, bbox_inches="tight")
        plt.close(fig)

    def plot_cdf(values, xlabel, filename):
        if not values:
            raise ValueError(f"no samples available for {output / filename}")
        ordered = sorted(values)
        fractions = [i / len(ordered) for i in range(len(ordered) + 1)]
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.step([ordered[0], *ordered], fractions, where="post")
        ax.set(xlabel=xlabel, ylabel="CDF", ylim=(0, 1.03))
        save(fig, ax, filename)

    by_iteration = defaultdict(list)
    for (_, iteration), (start, finish) in completions.items():
        by_iteration[iteration + 1].append((finish - start) / 1000)
    iterations = sorted(by_iteration)
    samples = [by_iteration[i] for i in iterations]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.fill_between(iterations, list(map(min, samples)), list(map(max, samples)),
                    alpha=0.2, label="Min–max across jobs")
    ax.plot(iterations, list(map(mean, samples)), marker="o", label="Mean across jobs")
    ax.set(xlabel="Iteration", ylabel="Collective completion time (µs)", xticks=iterations)
    ax.legend()
    save(fig, ax, "collective_completion.pdf")

    # Ring transfers all have the same size, so show slowdown as a distribution.
    slowdown = [max(1, duration / baseline)
                for *_, duration, baseline in read_samples(output / "fct.txt")]
    plot_cdf(slowdown, "Transfer FCT slowdown (×)", "fct_slowdown.pdf")
    plot_cdf(uplink_imbalance(output / "uplink.txt"),
             "ToR uplink imbalance (% of mean)", "uplink_imbalance.pdf")
    if routing == "conweave":
        occupancy = [packets for _, _, _, packets in read_samples(output / "voq.txt", ",")]
        plot_cdf(occupancy, "VOQ occupancy per ToR (packets)", "voq_occupancy.pdf")
