#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Stage a single native ARM64 .deb; dpkg-deb is run on the target Ubuntu builder."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile

p=argparse.ArgumentParser()
p.add_argument('--version', default='1.2.0-3')
a=p.parse_args()
base=Path(__file__).resolve().parents[1]
pack=base/'packaging'
stage=base/'build'/('nabu-camera_'+a.version+'_arm64')
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir()

def write(name, text, mode=0o644):
    target=stage/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    target.chmod(mode)

def copy(source, name):
    target=stage/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source,target)

with tarfile.open(base/'build/deb-runtime.tar.gz') as archive:
    for item in archive.getmembers():
        parts=Path(item.name).parts
        assert parts[:3]==('opt','nabu-camera','runtime') or item.isdir()
    archive.extractall(stage, filter='data')
# SDK headers/pkg-config are included so consumers can explicitly use this private libcamera.
release='6.1.10-nabu-camera3'
bundle=base/'build/deploy-camera3-stack'
with tarfile.open(bundle/'kernel'/('modules-'+release+'.tar.gz')) as archive:
    for member in archive.getmembers():
        name=Path(member.name)
        assert name.parts[:3]==('lib','modules',release) and '..' not in name.parts
        assert member.isdir() or member.isfile()
        target=stage/'usr'/name
        if member.isfile() and name.name.startswith('modules.') and name.name not in {'modules.order','modules.builtin','modules.builtin.modinfo'}:
            continue
        if member.isdir():
            target.mkdir(parents=True,exist_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o644)
assert not (stage/'lib').exists()
for name in ['vmlinuz','dtb','config','System.map']:
    copy(bundle/'kernel'/f'{name}-{release}',f'opt/nabu-camera/boot/{name}-{release}')
for name in ['nabu_camera.py','install_support.py','preview_gl.py','flash_control.py','bridge_service.py','camera_ui.py','camera_media.py']:
    copy(pack/name, 'opt/nabu-camera/'+name)
copy(base/'build/camera-bridge','opt/nabu-camera/camera-bridge')
copy(base/'build/pipewire-gst/libgstpipewire.so','usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstpipewire.so')
for path in (base/'build/pipewire-gst').iterdir():
    if path.suffix in ('.c','.h','.sh') or path.name=='COPYING':
        copy(path,'usr/share/doc/nabu-camera/sources/pipewire-gst/'+path.name)
copy(base/'patches/userspace/nabu-pipewire-device-copy.patch','usr/share/doc/nabu-camera/patches/nabu-pipewire-device-copy.patch')
extra_modules={}
for path in sorted((base/'build/system-camera-modules').glob('*.ko')):
    name=f'usr/lib/modules/{release}/extra/nabu-camera/{path.name}'
    copy(path,name)
    extra_modules['/'+name]=hashlib.sha256(path.read_bytes()).hexdigest()
assert len(extra_modules)==5,'All five flash/loopback modules must be included'
copy(pack/'nabu-camera.desktop','usr/share/applications/nabu-camera.desktop')
copy(pack/'70-nabu-camera.rules','usr/lib/udev/rules.d/70-nabu-camera.rules')
copy(pack/'nabu-camera-prepare.service','usr/lib/systemd/system/nabu-camera-prepare.service')
copy(pack/'nabu-camera-bridge.service','usr/lib/systemd/system/nabu-camera-bridge.service')
copy(pack/'nabu-camera-loopback.conf','usr/lib/modprobe.d/nabu-camera-loopback.conf')
write('usr/lib/modules-load.d/nabu-camera.conf','nabu-cn3927\nnabu-flash-device\nv4l2loopback\n')
write('usr/bin/nabu-camera','#!/bin/sh\nexec /usr/bin/python3 /opt/nabu-camera/nabu_camera.py "$@"\n',0o755)
write('usr/bin/nabu-camera-restore-kernel','#!/bin/sh\nexec /usr/bin/python3 /opt/nabu-camera/install_support.py restore-kernel\n',0o755)
write('usr/bin/nabu-camera-cam', '''#!/usr/bin/python3
import os, sys, subprocess
sys.path.insert(0, '/opt/nabu-camera')
from nabu_camera import RUNTIME, environment, platform_check
platform_check()
if subprocess.run(['systemctl','is-active','--quiet','nabu-camera-bridge.service']).returncode==0:
    raise SystemExit('Stop the compatibility service before native libcamera diagnostics: sudo systemctl stop nabu-camera-bridge')
os.execve(str(RUNTIME/'bin/cam'), ['cam']+sys.argv[1:], environment())
''',0o755)
# Rebuild sources/patches are shipped as documentation, without private signing keys.
kernel_series=base/'patches/series-camera3-focus.txt'
copy(kernel_series, 'usr/share/doc/nabu-camera/patches/'+kernel_series.name)
for line in kernel_series.read_text().splitlines():
    if line.strip() and not line.startswith('#'):
        copy(base/'patches'/line, 'usr/share/doc/nabu-camera/patches/'+line)
for path in (base/'patches/libcamera').glob('*.patch'):
    copy(path,'usr/share/doc/nabu-camera/patches/libcamera/'+path.name)
copy(base/'patches/series-libcamera-final.txt','usr/share/doc/nabu-camera/patches/series-libcamera-final.txt')
for path in (base/'sources/nabu-focus').iterdir():
    if (path.suffix in ('.c', '.h') and not path.name.endswith('.mod.c')) or path.name=='Makefile':
        copy(path,'usr/share/doc/nabu-camera/sources/nabu-focus/'+path.name)
for path in (base/'sources/nabu-flash').iterdir():
    if (path.suffix in ('.c','.h') and not path.name.endswith('.mod.c')) or path.name=='Makefile':
        copy(path,'usr/share/doc/nabu-camera/sources/nabu-flash/'+path.name)
for name in ['v4l2loopback.c','v4l2loopback.h','v4l2loopback_formats.h','Kbuild','COPYING']:
    copy(base/'sources/v4l2loopback'/name,'usr/share/doc/nabu-camera/sources/v4l2loopback/'+name)
copy(pack/'camera_bridge.cpp','usr/share/doc/nabu-camera/sources/camera_bridge.cpp')
for name in ['libcamera-source-manifest.json','libcamera-patch-verification.json']:
    source=base/'evidence'/name
    if source.exists():
        copy(source,'usr/share/doc/nabu-camera/'+name)
for path in (base/'sources/libcamera/LICENSES').iterdir():
    copy(path,'usr/share/doc/nabu-camera/licenses/'+path.name)
copy(pack/'README.md','usr/share/doc/nabu-camera/README.md')
copy(pack/'SOURCE.md','usr/share/doc/nabu-camera/SOURCE.md')
for name in ['LICENSE', 'AUTHORS', 'THIRD_PARTY_NOTICES.md']:
    copy(pack/name, 'usr/share/doc/nabu-camera/'+name)
write('usr/share/doc/nabu-camera/copyright', '''Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: nabu-camera
Upstream-Contact: zouhong <admin@proyy.com>
Source: https://github.com/proyy/xiaomi-nabu-camera
 https://git.libcamera.org/libcamera/libcamera.git
 https://github.com/maverickjb/linux-6.1.10

Files: opt/nabu-camera/*.py opt/nabu-camera/camera-bridge
 usr/share/doc/nabu-camera/sources/camera_bridge.cpp
Copyright: 2026 zouhong <admin@proyy.com>
License: Nabu-Camera-NonCommercial-1.0
 See /usr/share/doc/nabu-camera/LICENSE for the full text.
 This restriction applies only to the independently authored project files.

Files: opt/nabu-camera/runtime/lib/*/libcamera*.so*
Copyright: libcamera contributors (see sources)
License: LGPL-2.1-or-later

Files: usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstpipewire.so
Copyright: PipeWire contributors (see sources)
License: MIT

Files: opt/nabu-camera/runtime/bin/cam
Copyright: libcamera and Linux kernel contributors (see sources)
License: GPL-2.0-or-later

Files: usr/lib/modules/* opt/nabu-camera/boot/*
Copyright: Linux kernel contributors; 2026 zouhong (nabu adaptations)
License: GPL-2.0-only and other per-file licenses
 See source SPDX notices and THIRD_PARTY_NOTICES.md.

Comment: Full license texts and source revisions/patches accompany the package.
 Kernel sources retain their individual SPDX licensing notices.
''')
control=stage/'DEBIAN'
control.mkdir()
# preinst must run before any payload is unpacked; include the guard, not a path to an old package.
support=(pack/'install_support.py').read_text().split("if __name__ == '__main__':")[0]
original=json.loads((bundle/'kernel-verification-input.json').read_text())
kernel_files=dict(original['esp_files'])
kernel_files.update({name.replace('/lib/modules/','/usr/lib/modules/',1):sha
                     for name,sha in original['module_files'].items()})
write('DEBIAN/preinst',support+"\nif len(sys.argv)>1 and sys.argv[1] in ('install','upgrade'):\n    preflight("+repr({'kernel_files':kernel_files})+")\n    system_camera_preinstall()\n",0o755)
write('DEBIAN/postinst','''#!/bin/sh
set -eu
if [ "$1" = configure ]; then
    /usr/bin/python3 /opt/nabu-camera/install_support.py configure
fi
''',0o755)
write('DEBIAN/prerm','''#!/bin/sh
set -eu
if [ "$1" = remove ]; then
    /usr/bin/python3 /opt/nabu-camera/install_support.py remove
fi
''',0o755)
write('DEBIAN/postrm','''#!/bin/sh
set -eu
if [ "$1" = remove ] || [ "$1" = purge ]; then
    dpkg-divert --package nabu-camera --remove --rename --divert /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstlibcamera.so.nabu-disabled /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstlibcamera.so
    dpkg-divert --package nabu-camera --remove --rename --divert /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstpipewire.so.nabu-system /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstpipewire.so
    systemctl daemon-reload
    udevadm control --reload-rules
fi
''',0o755)
# Hash all immutable payload, excluding the manifest that contains these hashes.
files={}
for path in sorted(stage.rglob('*')):
    name=path.relative_to(stage).as_posix()
    if path.is_dir() or name.startswith('DEBIAN/'):
        continue
    files['/'+name]=({'symlink':os.readlink(path)} if path.is_symlink()
                    else hashlib.sha256(path.read_bytes()).hexdigest())
manifest={'version':a.version,'architecture':'arm64','ubuntu':'24.04','kernel_release':release,
          'libcamera_commit':'191e202178f02430b5942397c70d215cdd2056fa',
          'kernel_files':dict(kernel_files,**extra_modules),'package_files':files,
          'extra_modules':extra_modules}
write('opt/nabu-camera/manifest.json',json.dumps(manifest,indent=2)+'\n')
size=sum(p.stat().st_size for p in stage.rglob('*') if p.is_file() and not p.is_symlink())//1024
write('DEBIAN/control',f'''Package: nabu-camera
Version: {a.version}
Architecture: arm64
Section: video
Priority: optional
Maintainer: zouhong <admin@proyy.com>
Homepage: https://github.com/proyy/xiaomi-nabu-camera
Installed-Size: {size}
Pre-Depends: python3 (>= 3.12), systemd
Depends: libc6 (>= 2.38), libstdc++6 (>= 13), libgcc-s1, libevent-2.1-7t64, libevent-pthreads-2.1-7t64, libyaml-0-2, libtiff6, libegl1, libgles2, libgnutls30t64, libgl1-mesa-dri, libyuv0, libpipewire-0.3-0t64 (>= 1.0.5), gstreamer1.0-pipewire (>= 1.0.5), gstreamer1.0-plugins-good, python3-gi, python3-pil, gir1.2-gtk-3.0, gir1.2-gdkpixbuf-2.0, librsvg2-common, ffmpeg, v4l-utils, kmod, udev, systemd, init-system-helpers, passwd
Description: Xiaomi Pad 5 Ubuntu 24.04 front and rear cameras
 Camera kernel, device tree, native focus driver, isolated libcamera software
 ISP, desktop preview, photo capture and hardware self-test for xiaomi-nabu.
 Requires the nabu Simple Init UEFI Ubuntu boot layout.
''')
md5=[]
for path in sorted(stage.rglob('*')):
    name=path.relative_to(stage).as_posix()
    if path.is_file() and not path.is_symlink() and not name.startswith('DEBIAN/'):
        md5.append(hashlib.md5(path.read_bytes()).hexdigest()+'  '+name)
write('DEBIAN/md5sums','\n'.join(md5)+'\n')
print(json.dumps({'stage':str(stage),'payload_files':len(files),'kernel_modules':len(original['module_files'])+len(extra_modules),
                  'installed_kib':size},indent=2))
