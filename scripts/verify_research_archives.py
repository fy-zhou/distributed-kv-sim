#!/usr/bin/env python3
"""Resolve archived regression artifacts by config checksum, then verify raw bytes."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', type=Path, required=True)
    parser.add_argument('--verify-source', action='store_true')
    parser.add_argument('archives', type=Path, nargs='+')
    args = parser.parse_args()
    configs = {}
    for path in args.raw_root.glob('*/config.json'):
        configs.setdefault(sha(path), []).append(path.parent)
    checks = 0
    source_checks = 0
    source_blobs = {}
    for archive in args.archives:
        size = sum(p.stat().st_size for p in archive.rglob('*') if p.is_file())
        if size >= 2**20:
            raise ValueError('archive exceeds 1 MiB: '+str(archive))
        manifest = json.loads((archive/'manifest.json').read_text())
        hashes = manifest['raw_sha256']
        directories = configs.get(hashes['config.json'], ())
        for src in directories:
            if all((src/name).is_file() and sha(src/name) == h for name, h in hashes.items()):
                checks += len(hashes)
                break
        else:
            raise ValueError('no complete raw match for '+str(archive))
        if args.verify_source:
            proof = archive/'SOURCE_PROVENANCE.json'
            commit = manifest['code_commit']
            if proof.exists():
                commit = json.loads(proof.read_text())['verified_source_snapshot_commit']
            for name, expected in manifest.get('source_sha256', {}).items():
                key = (commit, name)
                if key not in source_blobs:
                    blob = subprocess.check_output(['git', 'show', commit+':'+name])
                    source_blobs[key] = hashlib.sha256(blob).hexdigest()
                if source_blobs[key] != expected:
                    raise ValueError('source snapshot mismatch: %s %s' % (archive, name))
                source_checks += 1
        print(archive.name, len(hashes), size)
    print('Verified', checks, 'raw hashes across', len(args.archives), 'archives')
    if args.verify_source:
        print('Verified', source_checks, 'source hashes')


if __name__ == '__main__':
    main()
