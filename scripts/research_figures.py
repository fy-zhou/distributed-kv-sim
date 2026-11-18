#!/usr/bin/env python3
"""Small scientific SVG figures from retained CSVs; requires matplotlib only."""
import argparse
import csv
from pathlib import Path
import statistics


def read(path):
    with Path(path).open(newline='')as f:return list(csv.DictReader(f))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mechanisms',type=Path,required=True)
    p.add_argument('--evaluation',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':9,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
    args.out.mkdir(parents=True,exist_ok=False)
    rows=read(args.mechanisms/'runs.csv')
    controls=['reference','capacity260','source_pin','no_fetch','force_first_divergence']
    labels=['Reference','Cache 260','Protect source','No remote','Force first placement']
    fig,axs=plt.subplots(1,2,figsize=(10,3.8),layout='constrained')
    for offset,pol,color in ((-.18,'greedy','#2364aa'),(.18,'bandwidth_aware','#d65a31')):
        vals=[next(float(r['p95_ttft_ms'])for r in rows if r['seed']=='42' and r['control']==c and r['policy']==pol and r['admission']=='always')for c in controls]
        axs[0].barh([i+offset for i in range(len(controls))],vals,height=.34,label=pol,color=color)
        snap=read(args.mechanisms/('seed42-%s-always'%pol)/'snapshots.csv')
        source=[r for r in snap if r['node']=='0']
        axs[1].step([float(r['time_ms'])for r in source],[int(r['hot_contiguous'])for r in source],where='post',label=pol,color=color)
    axs[0].set_yticks(range(len(controls)),labels);axs[0].invert_yaxis()
    axs[0].set_xlabel('Simulated P95 TTFT (ms)');axs[0].set_title('Seed 42: individual interventions')
    axs[1].set_xlabel('Simulated time (ms)');axs[1].set_ylabel('Reusable source hot-prefix blocks')
    axs[1].set_title('Source residency under reference policy');axs[1].legend(frameon=False)
    fig.savefig(args.out/'mechanism.svg',metadata={'Date':None});plt.close(fig)
    pairs=read(args.evaluation/'pairs.csv')
    names=list(dict.fromkeys(r['scenario']for r in pairs))
    fig,axs=plt.subplots(1,2,figsize=(10,6),sharey=True,layout='constrained')
    for ax,baseline,color in zip(axs,('greedy','round_robin_cost'),('#2364aa','#d65a31')):
        values=[[float(r['p95_improvement_pct'])for r in pairs if r['scenario']==name and r['baseline']==baseline]for name in names]
        means=[statistics.mean(v)for v in values]
        ax.errorbar(means,range(len(names)),xerr=[[m-min(v)for m,v in zip(means,values)],[max(v)-m for m,v in zip(means,values)]],fmt='o',color=color,capsize=2)
        ax.axvline(0,color='.5',lw=.8)
        ax.set_title('Cost-aware vs '+baseline)
        ax.set_xlabel('Paired P95 improvement (%)')
        ax.set_yticks(range(len(names)),names)
    axs[0].invert_yaxis()
    fig.suptitle('Held-out synthetic scenarios; dots: mean, whiskers: observed min–max\nFive seeds × two admissions per scenario; whiskers are not confidence intervals',fontsize=10)
    fig.savefig(args.out/'evaluation.svg',metadata={'Date':None});plt.close(fig)
    print('Saved SVGs to',args.out)


if __name__=='__main__':main()
