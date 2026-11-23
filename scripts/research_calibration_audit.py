#!/usr/bin/env python3
"""Audit measured data for cold service and separate KV transfer components."""
import argparse
import csv
import hashlib
from itertools import combinations
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim.predictor import ComputePredictor
from scripts.research_common import archive, new_directory, provenance, write_csv


EXPECTED = {
    'matrix.csv': '5ba2af37636b47d61652005c4a635a48e24abf800c300860a58f9840ea5802f6',
    'configuration.json': '1c51fcbb4ea0f03ef213a5a17f5b2a8af2004988477ea486e4bef6c7a39f7cfc',
}


def nonnegative_fit(features, targets):
    """Enumerate active sets for the small nonnegative least-squares problem."""
    import numpy as np
    x = np.asarray(features, dtype=float)
    y = np.asarray(targets, dtype=float)
    best = np.zeros(x.shape[1])
    error = float(y @ y)
    for size in range(1, x.shape[1] + 1):
        for subset in combinations(range(x.shape[1]), size):
            weights = np.linalg.lstsq(x[:, subset], y, rcond=None)[0]
            if min(weights) < 0:
                continue
            residual = x[:, subset] @ weights - y
            candidate_error = float(residual @ residual)
            if candidate_error < error:
                error = candidate_error
                best[:] = 0
                best[list(subset)] = weights
    return best.tolist()


def dot(x, y):
    return sum(a*b for a, b in zip(x, y))


def compute_features(length):
    return [1., length/1024, length*(length+1)/2/1e6]


def summarize(rows, x, y, group):
    weights = nonnegative_fit([x(r) for r in rows], [y(r) for r in rows])
    predictions = []
    for held in sorted({group(r) for r in rows}):
        train = [r for r in rows if group(r) != held]
        fit = nonnegative_fit([x(r) for r in train], [y(r) for r in train])
        predictions.extend((r, dot(x(r), fit)) for r in rows if group(r) == held)
    mae = statistics.mean(abs(y(r)-value) for r, value in predictions)
    return weights, predictions, mae


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    for name, checksum in EXPECTED.items():
        if hashlib.sha256((args.source/name).read_bytes()).hexdigest() != checksum:
            raise ValueError('historical input checksum mismatch: '+name)
    with (args.source/'matrix.csv').open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    cells = {}
    for row in rows:
        assert row['verified'] == 'True' and row['status'] == 'measured'
        length, hit = int(row['length']), int(row['injected_hit_tokens'])
        assert length-hit == int(float(row['actual_prefill_kv_tokens']))
        assert int(row['kv_bytes']) == hit*114688
        cells.setdefault((length, hit), []).append(row)
    assert len(rows) == 1200 and len(cells) == 60
    fields = ('prefill_service_ms', 'h2d_cuda_ms', 'scatter_cuda_ms', 'worker_load_wall_ms')
    medians = []
    for (length, hit), samples in sorted(cells.items()):
        assert len({int(r['repeat']) for r in samples}) == len(samples) == 20
        medians.append(dict(length=length, hit=hit, payload_gib=hit*114688/2**30,
                            **{f: statistics.median(float(r[f]) for r in samples) for f in fields}))
    cold = [r for r in medians if not r['hit']]
    warm = [r for r in medians if r['hit']]
    vector = lambda r: compute_features(r['length'])
    weights, predictions, mae = summarize(cold, vector, lambda r:r['prefill_service_ms'],
                                          lambda r:r['length'])
    out = new_directory(args.out)
    predictor = ComputePredictor()
    write_csv(out/'cold_validation.csv', [dict(length=r['length'], observed_ms=r['prefill_service_ms'],
        illustrative_ms=predictor.predict_ms(r['length'], 0), held_length_prediction_ms=p)
        for r, p in predictions])
    fits = {}
    transfer_predictions = []
    for field in fields[1:]:
        fit, validation, error = summarize(warm, lambda r:[1., r['payload_gib']],
                                           lambda r:r[field], lambda r:r['length'])
        fits[field] = dict(intercept_ms=fit[0], ms_per_gib=fit[1], held_length_mae_ms=error)
        transfer_predictions.extend(dict(metric=field, length=r['length'], hit=r['hit'],
            payload_gib=r['payload_gib'], observed_ms=r[field], held_length_prediction_ms=p)
            for r, p in validation)
    write_csv(out/'transfer_validation.csv', transfer_predictions)
    write_csv(out/'measured_cell_medians.csv', medians)
    import numpy
    metadata = provenance(dict(source=str(args.source), sha256=EXPECTED, numpy=numpy.__version__,
        cells=len(cells), rows=len(rows), source_configuration=json.loads((args.source/'configuration.json').read_text()),
        validation='Hold out an entire request length at once. The fit operates on cell medians.',
        interpretation='Cold vLLM prefill service proxy, separate CUDA H2D and paged-cache scatter, '
                       'and total worker load. No standalone reused-token compute timing.'))
    metadata['synthetic'] = False
    metadata['note'] = 'Reanalysis of existing physical vLLM data; no new hardware runs.'
    (out/'config.json').write_text(json.dumps(metadata, indent=2)+'\n')
    result = dict(cold_service_proxy=dict(fixed_ms=weights[0], ms_per_new_token=weights[1]/1024,
        ms_per_causal_pair=weights[2]/1e6, held_length_mae_ms=mae,
        illustrative_mae_ms=statistics.mean(abs(r['prefill_service_ms']-
            predictor.predict_ms(r['length'], 0)) for r in cold)), transfer_fits=fits)
    (out/'fits.json').write_text(json.dumps(result, indent=2)+'\n')
    archive(out, args.archive, ['fits.json', 'cold_validation.csv', 'transfer_validation.csv',
                               'measured_cell_medians.csv'],
            '# Physical cost model audit\n\n'
            'Existing RTX 4090 measurements with verified SHA-256 and verified KV injection. '
            'Cold prefill uses the no-injection vLLM service phase as a proxy. '
            'Injected prefill service includes loading, so it cannot isolate GPU compute. '
            'H2D and GPU paged-cache scatter are per-layer CUDA event sums. Worker load is a wall-clock '
            'phase that already includes these operations. Fit validation holds out entire lengths. '
            'The data come from one system and support sensitivity experiments within this sampled range.')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
