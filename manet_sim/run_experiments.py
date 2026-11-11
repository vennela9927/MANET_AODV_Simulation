"""Run every experiment from slide 10 and write results.csv (takes a few minutes)."""
import csv
import os
from multiprocessing import Pool

from manet_sim import simulate

SEEDS = range(1, 11)
NODE_COUNTS = [20, 40, 60, 80]
FAIL_FRACS = [0.0, 0.1, 0.2, 0.3]
ABLATION = ["AODV", "+priority only", "+battery-aware only", "+store-carry only", "AODV-DR"]
FIELDS = ["experiment", "variant", "n_nodes", "fail_frac", "seed", "sos_pdr", "sos_delay_ms",
          "sos_delay_med_ms", "all_pdr", "all_delay_ms", "recovery_ms", "recovery_med_ms",
          "recoveries", "nrl", "ctrl_tx", "data_tx", "energy_j", "battery_deaths", "alive_end",
          "sos_sent", "sos_delivered"]


def jobs():
    for n in NODE_COUNTS:                     # vary node count, 10 % failures
        for v in ("AODV", "AODV-DR"):
            for s in SEEDS:
                yield ("nodes", v, n, 0.1, s)
    for f in FAIL_FRACS:                      # vary failures, 40 nodes
        for v in ("AODV", "AODV-DR"):
            for s in SEEDS:
                yield ("failures", v, 40, f, s)
    for v in ABLATION:                        # which enhancement does what
        for s in SEEDS:
            yield ("ablation", v, 40, 0.1, s)


def run(job):
    exp, v, n, f, s = job
    r = simulate(v, n_nodes=n, fail_frac=f, seed=s)
    row = dict(experiment=exp, variant=v, n_nodes=n, fail_frac=f, seed=s)
    row.update({k: r[k] for k in FIELDS if k in r})
    return row


if __name__ == "__main__":
    todo = list(jobs())
    print(f"running {len(todo)} simulations on {os.cpu_count()} cores ...")
    with Pool() as pool, open("results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for i, row in enumerate(pool.imap_unordered(run, todo), 1):
            w.writerow(row)
            if i % 20 == 0:
                print(f"  {i}/{len(todo)} done")
    print("wrote results.csv")
