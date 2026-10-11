#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
"""Collect a freshly built kernel and generate this build's hash manifest."""
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

root = Path(__file__).resolve().parents[1]
out = root/'build/kernel'
release = (out/'include/config/kernel.release').read_text().strip()
assert release == '6.1.10-nabu-camera3', release
bundle = root/'build/deploy-camera3-stack'
kernel = bundle/'kernel'
kernel.mkdir(parents=True, exist_ok=True)
for source, name in [('arch/arm64/boot/Image.gz', 'vmlinuz'),
                     ('arch/arm64/boot/dts/qcom/sm8150-xiaomi-nabu-camera.dtb', 'dtb'),
                     ('.config', 'config'), ('System.map', 'System.map')]:
    shutil.copy2(out/source, kernel/(name+'-'+release))

modules = root/'build/module-staging/lib/modules'/release
(modules/'extra').mkdir(parents=True, exist_ok=True)
shutil.copy2(root/'sources/nabu-focus/nabu-cn3927.ko', modules/'extra/nabu-cn3927.ko')
for name in ['build', 'source']:
    path = modules/name
    if path.is_symlink():
        path.unlink()
with tarfile.open(kernel/('modules-'+release+'.tar.gz'), 'w:gz', format=tarfile.GNU_FORMAT) as archive:
    archive.add(modules, arcname='lib/modules/'+release)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {'kernel_release': release,
            'esp_files': {'/boot/efi/'+p.name: sha(p) for p in kernel.iterdir() if p.name.endswith(release)},
            'module_files': {'/lib/modules/'+release+'/'+p.relative_to(modules).as_posix(): sha(p)
                             for p in modules.rglob('*.ko')}}
(bundle/'kernel-verification-input.json').write_text(json.dumps(manifest, indent=2)+'\n')
print('Kernel staged:', release, len(manifest['module_files']), 'modules')
