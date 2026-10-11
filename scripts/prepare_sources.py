#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
"""Verify the release's upstream archives and apply the exact ordered patches."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
archives = json.loads((root/'config/upstream-archives.json').read_text())
for name, metadata in archives.items():
    path = root/'downloads'/name
    if not path.exists():
        raise SystemExit('Extract the upstream-sources release asset here first: '+str(path))
    with path.open('rb') as f:
        assert hashlib.file_digest(f, 'sha256').hexdigest() == metadata['sha256'], name

for name, directory, series in [
    ('linux-6.1.10.tar.gz', 'linux', 'series-camera3-focus.txt'),
    ('libcamera-191e202178f02430b5942397c70d215cdd2056fa.tar.gz', 'libcamera', 'series-libcamera-final.txt'),
]:
    destination = root/'sources'/directory
    if destination.exists():
        raise SystemExit('Refusing to overwrite existing source: '+str(destination))
    temporary = root/'build'/('unpack-'+directory)
    temporary.mkdir(parents=True, exist_ok=False)
    with tarfile.open(root/'downloads'/name) as archive:
        archive.extractall(temporary, filter='data')
    children = list(temporary.iterdir())
    assert len(children) == 1 and children[0].is_dir()
    children[0].rename(destination)
    temporary.rmdir()
    for line in (root/'patches'/series).read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            # Do not let the parent project's Git worktree silently skip patches.
            env = os.environ | {'GIT_CEILING_DIRECTORIES': str(destination.parent)}
            subprocess.run(['git', 'apply', '--check', str(root/'patches'/line)], cwd=destination, env=env, check=True)
            subprocess.run(['git', 'apply', str(root/'patches'/line)], cwd=destination, env=env, check=True)
    print('Prepared', directory)

manifest = json.loads((root/'evidence/libcamera-source-manifest.json').read_text())
for name, expected in manifest['files'].items():
    assert hashlib.sha256((root/'sources/libcamera'/name).read_bytes()).hexdigest() == expected, name
print('All 15 modified libcamera files match the validated sources.')
