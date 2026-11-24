#!/usr/bin/env python3
"""Measure local Qwen3 decoder prefill with GPU-resident prefix state."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import new_directory, provenance, write_csv

LENGTHS = (512, 2048, 4096)
HIT_RATIOS = (0., .25, .5, .875)
REPEATS = 8
WARMUPS = 2
TENSOR_LIMIT = 3*2**30


def gpu_read(gpu, fields):
    return subprocess.check_output(['nvidia-smi', '-i', str(gpu), '--query-'+fields,
        '--format=csv,noheader,nounits'], universal_newlines=True).strip()


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for buffer in iter(lambda: handle.read(2**20), b''):
            digest.update(buffer)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    hardware = gpu_read(args.gpu, 'gpu=uuid,name,driver_version,pci.bus_id,memory.used,utilization.gpu')
    fields = next(csv.reader([hardware]))
    if float(fields[-2]) > 200 or float(fields[-1]) > 10:
        raise SystemExit('Selected GPU is busy; measurement not started: '+hardware)
    os.environ['CUDA_VISIBLE_DEVICES'] = fields[0].strip()
    os.environ['HF_HUB_OFFLINE'] = '1'
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, DynamicCache

    torch.set_num_threads(1)
    torch.manual_seed(20261124)
    out = new_directory(args.out)
    deadline = time.monotonic()+45
    torch.cuda.set_per_process_memory_fraction(TENSOR_LIMIT/torch.cuda.get_device_properties(0).total_memory)
    model_paths = [args.model/'config.json', args.model/'model.safetensors']
    config = provenance(dict(kind='qwen3_decoder_prefill_with_gpu_cache', gpu_before=hardware,
        model_files={p.name: sha(p) for p in model_paths}, model_directory=str(args.model),
        script_sha256=sha(Path(__file__)), torch=torch.__version__, transformers=transformers.__version__,
        cuda_runtime=torch.version.cuda, cpu_affinity=sorted(os.sched_getaffinity(0)),
        dtype='bfloat16', attention='sdpa', lengths=LENGTHS, hit_ratios=HIT_RATIOS,
        samples_per_cell=REPEATS, warmups=WARMUPS, tensor_allocation_limit=TENSOR_LIMIT,
        internal_deadline_seconds=45,
        scope='Decoder forward with output KV cache. Excludes LM head, tokenizer, prefix preparation, '
              'CPU/GPU copies, model load, scheduler and network. CUDA events include launch gaps.'))
    config['synthetic'] = False
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    model = AutoModelForCausalLM.from_pretrained(str(args.model), local_files_only=True,
        trust_remote_code=False, dtype=torch.bfloat16, device_map={'': 0},
        attn_implementation='sdpa').eval().model
    rows, summary, check = [], [], []
    with torch.inference_mode():
        for length in LENGTHS:
            tokens = torch.randint(model.config.vocab_size, (1, length), device='cuda')
            reference = None
            for ratio in HIT_RATIOS:
                apps = gpu_read(args.gpu, 'compute-apps=pid')
                if any(line.strip() != str(os.getpid()) for line in apps.splitlines() if line.strip()):
                    raise SystemExit('Another process appeared; completed cells are preserved.')
                hit = int(length*ratio)
                entries = None
                if hit:
                    prefix = model(input_ids=tokens[:, :hit], use_cache=True)
                    entries = tuple(prefix.past_key_values)
                    del prefix
                for rep in range(-WARMUPS, REPEATS):
                    if time.monotonic() > deadline:
                        raise SystemExit('Time limit reached; completed cells are preserved.')
                    cache = DynamicCache(ddp_cache_data=entries, config=model.config)
                    torch.cuda.synchronize()
                    a, b = (torch.cuda.Event(enable_timing=True) for _ in range(2))
                    start = time.perf_counter()
                    a.record()
                    result = model(input_ids=tokens[:, hit:], past_key_values=cache, use_cache=True)
                    b.record()
                    b.synchronize()
                    wall_ms = (time.perf_counter()-start)*1000
                    if rep == 0:
                        assert result.past_key_values.get_seq_length() == length
                        last = result.last_hidden_state[:, -1].float()
                        if reference is None:
                            reference = last
                        similarity = torch.nn.functional.cosine_similarity(reference, last).item()
                        assert similarity >= .995, (length, hit, similarity)
                        check.append(dict(length=length, hit_tokens=hit, cosine_last_hidden=similarity))
                    if rep >= 0:
                        rows.append(dict(length=length, hit_tokens=hit, new_tokens=length-hit,
                                         repeat=rep, wall_ms=wall_ms, cuda_ms=a.elapsed_time(b)))
                    del result, cache
                del entries
                measured = rows[-REPEATS:]
                summary.append(dict(length=length, hit_tokens=hit, new_tokens=length-hit,
                    median_wall_ms=statistics.median(r['wall_ms'] for r in measured),
                    median_cuda_ms=statistics.median(r['cuda_ms'] for r in measured)))
                write_csv(out/'samples.csv', rows)
                write_csv(out/'summary.csv', summary)
                write_csv(out/'correctness.csv', check)
                print('Completed', length, hit, flush=True)
    peak = torch.cuda.max_memory_allocated()
    assert peak <= TENSOR_LIMIT
    (out/'completion.json').write_text(json.dumps(dict(samples=len(rows),
        peak_tensor_bytes=peak, peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        elapsed_seconds=45-(deadline-time.monotonic())), indent=2)+'\n')


if __name__ == '__main__':
    main()
