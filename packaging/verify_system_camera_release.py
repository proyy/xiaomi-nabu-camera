#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Post-install evidence for the camera package on the original Pad system."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

def run(*args):
    return subprocess.check_output(args,text=True).strip()

package=Path('/data/pad5-camera/build/nabu-camera_1.2.0-2_arm64.deb')
process=subprocess.Popen(['dpkg-deb','--fsys-tarfile',str(package)],stdout=subprocess.PIPE)
with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
    members=list(archive)
assert process.wait()==0
names=[x.name.removeprefix('./') for x in members]
assert all(x.uid==0 and x.gid==0 for x in members)
assert not any(x=='lib' or x.startswith('lib/') for x in names)
assert sum(x.endswith('.ko') for x in names)==661
assert not any(x.startswith('data/') for x in names)
assert not any(Path(x).name.startswith('._') for x in names)
assert run('dpkg','-V','nabu-camera')==''
assert run('dpkg','-V','gstreamer1.0-pipewire')==''
diversions={name:run('dpkg-divert','--listpackage',
    '/usr/lib/aarch64-linux-gnu/gstreamer-1.0/'+name)
    for name in ['libgstlibcamera.so','libgstpipewire.so']}
assert all(owner=='nabu-camera' for owner in diversions.values()),diversions
assert run('systemctl','is-enabled','nabu-camera-bridge.service')=='enabled'
assert run('systemctl','show','nabu-camera-bridge.service','-p','NRestarts','--value')=='0'
diagnosis=json.loads(run('nabu-camera','--diagnose'))
assert diagnosis['passed'],diagnosis
bootcfg=Path('/boot/simpleinit/simpleinit.uefi.cfg').read_text()
default=re.search(r'^boot.default = "([^"]+)"$',bootcfg,re.M).group(1)
cmdline=Path('/proc/cmdline').read_text().strip()
assert default=='linux'
assert cmdline=='root=PARTLABEL=linux loglevel=3 fbcon=rotate:1'
states={side:Path(f'/sys/bus/i2c/devices/{dev}/power/runtime_status').read_text().strip()
        for side,dev in [('front','4-0010'),('rear','3-0010'),('lens','3-000c')]}
assert all(s=='suspended' for s in states.values()),states
manifest=json.loads(Path('/usr/share/doc/nabu-camera/libcamera-source-manifest.json').read_text())
differences=[name for name,digest in manifest['files'].items()
             if hashlib.sha256((Path('/data/pad5-camera/sources/libcamera')/name).read_bytes()).hexdigest()!=digest]
assert not differences,differences
dmesg=run('sudo','-n','dmesg')
faults=[line for line in dmesg.splitlines() if 'Unhandled context fault' in line and re.search(r'cb=12\b',line)]
assert not faults,faults
print(json.dumps({'package':{'deb':package.name,'bytes':package.stat().st_size,
    'sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'members':len(members),
    'kernel_modules':661,'root_ownership':True,'no_top_level_lib':True,'no_development_tree_payload':True},
    'version':run('dpkg-query','-W','-f=${Version}','nabu-camera'),
    'diagnosis':diagnosis,'dpkg_verify':True,'pipewire_dpkg_verify':True,
    'managed_gstreamer_diversions':diversions,'no_appledouble_files':True,
    'bridge_enabled':True,'bridge_unplanned_restarts':0,'boot_default':default,'cmdline':cmdline,
    'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'kernel':os.uname().release,'root_partition':run('findmnt','-n','-o','SOURCE','/'),
    'idle_power_states':states,'source_files_verified':len(manifest['files']),
    'camera_smmu_cb12_faults_observed':len(faults),'passed':True},indent=2))
