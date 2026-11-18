#!/usr/bin/env python3
"""Archive small seed-paired scheduler/admission ablation results."""
import argparse
import csv
import hashlib
import json
import pathlib
import statistics


def read_csv(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(65536),b""):
            h.update(chunk)
    return h.hexdigest()


def archive(source,dest):
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("destination already contains an archive")
    raw=source/"runs.csv"
    paired=source/"pairs.csv"
    config=source/"config.json"
    for p in (raw,paired,config):
        if not p.is_file():
            raise ValueError("missing "+str(p))
    rows=read_csv(raw)
    pairs=read_csv(paired)
    if not rows or not pairs:
        raise ValueError("empty result")
    cfg=json.loads(config.read_text())
    dest.mkdir(parents=True,exist_ok=True)
    # Both output tables are at most tens of KiB for the default 5-seed run.
    (dest/"runs.csv").write_bytes(raw.read_bytes())
    (dest/"pairs.csv").write_bytes(paired.read_bytes())
    cfg.pop("original_archive",None)
    (dest/"manifest.json").write_text(json.dumps({
        "parameters":cfg,
        "source_sha256":{p.name:sha256(p) for p in (raw,paired,config)},
        "note":"Synthetic simulation, no measured hardware-level validation",
    },indent=2)+"\n")
    report=["# Cache admission × scheduler controlled ablation","",
      "All results are synthetic discrete-event simulations. The two strata "
      "were selected post-hoc in the prior archive; seeds are paired across "
      "policy and admission. This is not an unbiased performance estimate.","",
      "The no_remote policy **does not cache a completed request** if it "
      "reused any remote blocks. It is not a hot-prefix pinning policy.","",
      "| Stratum | Admission | Seeds | Mean P95 improvement (%) | Positive seeds | Greedy P95 mean (ms) | Aware P95 mean (ms) |",
      "|:--|:--|--:|--:|--:|--:|--:|"]
    for case in sorted(set(r["case"] for r in pairs)):
        for admission in ("always","no_remote"):
            subset=[r for r in pairs if r["case"]==case and
                    r["admission"]==admission]
            vals=[float(r["p95_improvement_pct"]) for r in subset]
            report.append("| %s | %s | %d | %+.2f | %d | %.2f | %.2f |" %
                 (case,admission,len(subset),statistics.mean(vals),
                  sum(x>1e-7 for x in vals),
                  statistics.mean(float(r["greedy_p95_ms"]) for r in subset),
                  statistics.mean(float(r["aware_p95_ms"]) for r in subset)))
    report.extend(["","[Paired seed-level results](pairs.csv) | "
                   "[Per-policy metrics](runs.csv) | "
                   "[Manifest](manifest.json)","",
                   "Do not interpret admission-induced performance changes "
                   "as proof of the cause of observed scheduler regressions; "
                   "they may involve cache state, queue lengths and routing "
                   "interactions."])
    (dest/"README.md").write_text("\n".join(report)+"\n")
    size=sum(p.stat().st_size for p in dest.iterdir())
    if size>1024*1024:
        raise RuntimeError("archive exceeds 1 MiB limit")
    print("Saved",dest,"%.1f KiB" % (size/1024))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=pathlib.Path,required=True)
    p.add_argument("--destination",type=pathlib.Path,required=True)
    args=p.parse_args()
    archive(args.source,args.destination)


if __name__=="__main__":
    main()
