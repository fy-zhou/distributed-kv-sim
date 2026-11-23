#!/usr/bin/env python3
"""Verify two complete result directories and write a compact Git proof."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

TIMING = {'decision_mean_us', 'decision_max_us', 'execution_plan_mean_us',
          'total_planning_mean_us'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deterministic(path):
    with path.open(newline='') as handle:
        return [{k:v for k,v in row.items() if k not in TIMING}
                for row in csv.DictReader(handle)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--runs', type=int, required=True)
    parser.add_argument('--timing-study', action='store_true',
                        help='Compare deterministic run fields only when paired CSVs contain wall time')
    args = parser.parse_args()
    before, after = args.original, args.replay
    original = deterministic(before/'runs.csv')
    replay = deterministic(after/'runs.csv')
    assert len(original) == len(replay) == args.runs
    assert original == replay, 'deterministic fields differ'
    comparison_files = []
    if not args.timing_study:
        comparison_files = ['pairs.csv', 'summary.csv']
        comparison_files += [name for name in ('placement_pairs.csv', 'placement_summary.csv',
                                               'admission_pairs.csv', 'admission_summary.csv',
                                               'segments.csv')
                             if (before/name).exists() or (after/name).exists()]
    for name in comparison_files:
        assert sha(before/name) == sha(after/name), 'paired evidence differs: '+name
    record = dict(verified_runs=args.runs, ignored_fields=sorted(TIMING),
                  compared=('All deterministic run fields. Timing CSVs may vary.'
                            if args.timing_study else
                            'All deterministic run fields and byte-identical comparison CSVs.'))
    for label, directory in (('original', before), ('replay', after)):
        record[label] = dict(path=str(directory),
            code_commit=json.loads((directory/'config.json').read_text())['code_commit'],
            sha256={name:sha(directory/name) for name in
                    ['config.json', 'runs.csv'] + comparison_files})
    with args.out.open('x') as handle:
        json.dump(record, handle, indent=2)
        handle.write('\n')
    print('Verified', args.runs, 'runs:', args.out)


if __name__ == '__main__':
    main()
