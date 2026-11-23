#!/usr/bin/env python3
"""Resolve archived regression artifacts by config checksum, then verify raw bytes."""
import argparse
import hashlib
import json
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
    parser.add_argument('archives', type=Path, nargs='+')
    args = parser.parse_args()
    configs = {}
    for path in args.raw_root.glob('*/config.json'):
        configs.setdefault(sha(path), []).append(path.parent)
    checks = 0
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
        print(archive.name, len(hashes), size)
    print('Verified', checks, 'raw hashes across', len(args.archives), 'archives')


if __name__ == '__main__':
    main()
