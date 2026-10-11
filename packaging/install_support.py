#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Guarded nabu camera package configuration; preserves the merged-usr link."""
import ctypes
import fcntl
import hashlib
import grp
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path('/opt/nabu-camera')
STATE = Path('/var/lib/nabu-camera')
CFG = Path('/boot/simpleinit/simpleinit.uefi.cfg')
RELEASE = '6.1.10-nabu-camera3'
ENTRY = 'nabu_camera'
GST_PLUGIN = '/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstlibcamera.so'
PW_PLUGIN = '/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstpipewire.so'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def atomic_write(path, data, mode=0o644):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def platform_check():
    require(os.uname().machine == 'aarch64', 'Requires Ubuntu 24.04 ARM64')
    info = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(info.get('ID', '').strip('"') == 'ubuntu' and info.get('VERSION_ID', '').strip('"') == '24.04',
            'Only Ubuntu 24.04 is supported')
    require(b'xiaomi,nabu' in Path('/proc/device-tree/compatible').read_bytes().split(b'\0'),
            'Only Xiaomi Pad 5 (nabu) is supported')
    require(Path('/lib').is_symlink() and Path('/lib').resolve() == Path('/usr/lib'),
            '/lib must retain its Ubuntu merged-usr link')
    require(CFG.is_file() and not CFG.is_symlink() and os.path.ismount('/boot/efi')
            and os.path.ismount('/boot/simpleinit'), 'Mounted Simple Init ESP/logfs boot layout is required')
    cfg = CFG.read_text()
    require(re.search(r'^boot\.configs\.linux\.extra\.kernel = "@part_esp:\\\\vmlinuz-6\.1\.10-nabu(?:-camera3)?"$', cfg, re.M),
            'Unsupported boot layout: expected original nabu Linux entry')
    require(len(re.findall(r'^boot.default = "[^"\n]+"$', cfg, re.M)) == 1,
            'Boot default is ambiguous')
    require(shutil.disk_usage('/boot/efi').free > 30*1024*1024, 'ESP has less than 30 MiB free')
    for dev in ['3-0010', '4-0010', '3-000c']:
        state = Path('/sys/bus/i2c/devices')/dev/'power/runtime_status'
        require(not state.exists() or state.read_text().strip() == 'suspended',
                'Stop active camera capture before installing')


def preflight(manifest=None):
    platform_check()
    if manifest:
        for path, expected in manifest['kernel_files'].items():
            target = Path(path)
            if target.exists():
                require(target.is_file() and not target.is_symlink()
                        and hashlib.sha256(target.read_bytes()).hexdigest() == expected,
                        'An existing camera kernel file differs: '+path)
    print('nabu Ubuntu package preflight passed')


def system_camera_preinstall():
    """The obsolete 0.2 provider hides v4l2deviceprovider, including our cameras."""
    owner=subprocess.check_output(['dpkg-divert','--listpackage',GST_PLUGIN],text=True).strip()
    require(owner in ('','nabu-camera'),'Another package owns the libcamera GStreamer diversion')
    subprocess.run(['dpkg-divert','--package','nabu-camera','--add','--rename',
                    '--divert',GST_PLUGIN+'.nabu-disabled',GST_PLUGIN],check=True)
    owner=subprocess.check_output(['dpkg-divert','--listpackage',PW_PLUGIN],text=True).strip()
    require(owner in ('','nabu-camera'),'Another package owns the PipeWire GStreamer diversion')
    subprocess.run(['dpkg-divert','--package','nabu-camera','--add','--rename',
                    '--divert',PW_PLUGIN+'.nabu-system',PW_PLUGIN],check=True)
    subprocess.run(['systemctl','stop','nabu-camera-bridge.service'],stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def prepare_devices():
    require(os.uname().release==RELEASE,'Boot the packaged camera kernel first')
    for module in ['nabu-cn3927','nabu-flash-device','v4l2loopback']:
        subprocess.run(['modprobe',module],check=True)
    subprocess.run(['udevadm','settle','--timeout=15'],check=True)
    led=Path('/sys/class/leds/nabu:white:flash')
    require(led.is_dir(),'The nabu PM8150L flash LED did not probe')
    for name in ['brightness','flash_strobe','flash_brightness','flash_timeout']:
        os.chown(led/name,0,grp.getgrnam('video').gr_gid)
        os.chmod(led/name,0o664)
    (led/'brightness').write_text('0')
    (led/'flash_strobe').write_text('0')
    for number,label in [('42','Pad-Front-Camera'),('43','Pad-Rear-Camera')]:
        node=Path('/sys/class/video4linux')/('video'+number)
        require(node.is_dir() and (node/'name').read_text().strip()==label,
                'The expected camera endpoint is unavailable: /dev/video'+number)


def publish_devices():
    # PipeWire caches capabilities when a device first appears, before the
    # loopback producer has claimed it. Reannounce only our two ready endpoints.
    nodes=[]
    for number,label in [('42','Pad-Front-Camera'),('43','Pad-Rear-Camera')]:
        node=Path('/sys/class/video4linux')/('video'+number)
        require((node/'name').read_text().strip()==label,'Refusing to reannounce a foreign camera')
        nodes.append(str(node))
    for action in ['remove','add']:
        subprocess.run(['udevadm','trigger','--action='+action,*nodes],check=True)
        subprocess.run(['udevadm','settle','--timeout=15'],check=True)


def configure_boot_text(text, restore=False):
    # Replace only the original Linux kernel/DTB pointers. Preserve its command
    # line, desktop setup and other entries. Use original Linux by default.
    clean = re.sub(r'^boot\.configs\.nabu_camera\.[^\n]*\n?', '', text, flags=re.M)
    require(re.search(r'^boot\.configs\.linux\.extra\.cmdline = "[^\n]+"$', clean, re.M),
            'Original Linux command line is missing')
    release = '6.1.10-nabu' if restore else RELEASE
    for key, name in [('kernel','vmlinuz'), ('dtb','dtb')]:
        pattern = r'^boot\.configs\.linux\.extra\.'+key+r' = "[^"\n]+"$'
        replacement = 'boot.configs.linux.extra.'+key+' = "@part_esp:\\\\'+name+'-'+release+'"'
        clean, count = re.subn(pattern, lambda match: replacement, clean, flags=re.M)
        require(count == 1, 'Original Linux '+key+' setting is missing or ambiguous')
    clean = re.sub(r'^boot.default = "[^"\n]+"$', 'boot.default = "linux"', clean, flags=re.M)
    return clean


def verify_payload():
    manifest = json.loads((ROOT/'manifest.json').read_text())
    differences = []
    for name, expected in manifest['package_files'].items():
        path = Path(name)
        if isinstance(expected, dict):
            if not path.is_symlink() or os.readlink(path) != expected['symlink']:
                differences.append(name)
        elif not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            differences.append(name)
    return manifest, differences


def configure():
    platform_check()
    manifest, differences = verify_payload()
    require(not differences, 'Package content mismatch: '+str(differences))
    for name in ['vmlinuz', 'dtb', 'config', 'System.map']:
        source = ROOT/'boot'/f'{name}-{RELEASE}'
        target = Path('/boot/efi')/source.name
        if target.exists():
            require(target.read_bytes() == source.read_bytes(), 'Existing boot artifact differs: '+str(target))
        else:
            atomic_write(target, source.read_bytes())
    subprocess.run(['depmod', '-a', RELEASE], check=True)
    old = CFG.read_text()
    new = configure_boot_text(old)
    if new != old:
        atomic_write(CFG, new.encode())
    subprocess.run(['udevadm', 'control', '--reload-rules'], check=True)
    for subsystem in ['dma_heap', 'video4linux', 'media', 'drm', 'leds']:
        subprocess.run(['udevadm', 'trigger', '--subsystem-match='+subsystem, '--action=change'], check=True)
    subprocess.run(['udevadm', 'settle', '--timeout=15'], check=True)
    STATE.mkdir(mode=0o755, exist_ok=True)
    if os.uname().release == RELEASE:
        subprocess.run(['modprobe', 'nabu-cn3927'], check=True)
        calibrate()
        prepare_devices()
    if subprocess.run(['id','nabu-camera'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['useradd','--system','--no-create-home','--home-dir','/nonexistent',
                        '--shell','/usr/sbin/nologin','--groups','video,render','nabu-camera'],check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'nabu-camera-prepare.service'], check=True)
    subprocess.run(['systemctl', 'enable', 'nabu-camera-bridge.service'], check=True)
    if os.uname().release==RELEASE:
        subprocess.run(['systemctl','restart','nabu-camera-prepare.service'],check=True)
        subprocess.run(['systemctl','restart','nabu-camera-bridge.service'],check=True)
    print('nabu-camera installed. Open Pad Camera from the Ubuntu desktop.')
    if os.uname().release != RELEASE:
        print('Reboot into the original Linux entry to use the patched kernel.')


class I2CMessage(ctypes.Structure):
    _fields_ = [('addr',ctypes.c_uint16), ('flags',ctypes.c_uint16),
                ('length',ctypes.c_uint16), ('buffer',ctypes.c_void_p)]


class I2CTransfer(ctypes.Structure):
    _fields_ = [('messages',ctypes.POINTER(I2CMessage)), ('count',ctypes.c_uint32)]


def calibrate():
    """Read this unit's own verified nabu module layout, never a copied unit's DAC."""
    require(os.uname().release == RELEASE, 'Boot the packaged camera kernel first')
    STATE.mkdir(mode=0o755, exist_ok=True)
    node = subprocess.check_output(['media-ctl', '-d', '/dev/media0', '-e', 'nabu-cn3927 3-000c'], text=True).strip()
    require(re.fullmatch(r'/dev/v4l-subdev\d+', node), 'Native lens entity missing')
    lens = os.open(node, os.O_RDWR)
    bus = None
    try:
        bus = os.open('/dev/i2c-3', os.O_RDWR)
        data = bytearray()
        for offset in range(0, 96, 8):
            tx = ctypes.create_string_buffer(offset.to_bytes(2,'big'))
            rx = ctypes.create_string_buffer(8)
            messages = (I2CMessage*2)(I2CMessage(0x51,0,2,ctypes.addressof(tx)),
                                      I2CMessage(0x51,1,8,ctypes.addressof(rx)))
            fcntl.ioctl(bus, 0x0707, I2CTransfer(messages, 2))
            data.extend(rx.raw)
        values = sorted([int.from_bytes(data[p:p+2], 'big') for p in [45,49]])
        require(0 < values[0] < values[1] <= 733 and values[1]-values[0] >= 64,
                'Unrecognized focus calibration; refusing guessed lens range')
        calibration = {'native_dac_bounds': values, 'eeprom_sha256': hashlib.sha256(data).hexdigest(),
                       'layout': 'nabu_ofilm_ov13b10 offsets45,49 big endian',
                       'source': 'read from this tablet during installation/boot'}
        atomic_write(STATE/'focus.json', (json.dumps(calibration, indent=2)+'\n').encode())
        print(json.dumps(calibration))
    finally:
        if bus is not None:
            os.close(bus)
        os.close(lens)


def diagnose():
    manifest, differences = verify_payload()
    kernel_differences = [p for p,h in manifest['kernel_files'].items()
                          if not Path(p).is_file() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    result = {'kernel':os.uname().release, 'expected_kernel':RELEASE,
              'package_hash_differences':differences, 'kernel_hash_differences':kernel_differences,
              'lib_link_ok':Path('/lib').is_symlink() and Path('/lib').resolve()==Path('/usr/lib'),
              'focus_calibrated': (STATE/'focus.json').exists(),
              'dma_heap_access':os.access('/dev/dma_heap/system', os.R_OK|os.W_OK)}
    result['system_camera_devices']={side:Path('/dev/nabu-camera-'+side).resolve().as_posix()
                                    for side in ['front','rear']}
    result['camera_bridge_active']=subprocess.run(['systemctl','is-active','--quiet','nabu-camera-bridge.service']).returncode==0
    result['flash_available']=Path('/sys/class/leds/nabu:white:flash/flash_strobe').exists()
    result['flash_access']=os.access('/sys/class/leds/nabu:white:flash/flash_strobe',os.W_OK)
    result['system_camera_devices_ok']=all(Path('/dev/nabu-camera-'+side).is_char_device() and
        Path('/dev/nabu-camera-'+side).resolve()==Path('/dev/video'+str(number))
        for side,number in [('front',42),('rear',43)])
    result['passed'] = not differences and not kernel_differences and all(
        result[k] for k in ['lib_link_ok','focus_calibrated','dma_heap_access','camera_bridge_active',
                           'flash_available','flash_access','system_camera_devices_ok']) and os.uname().release==RELEASE
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


def restore_kernel():
    require(os.geteuid() == 0, 'Run with sudo')
    require(Path('/boot/efi/vmlinuz-6.1.10-nabu').is_file()
            and Path('/boot/efi/dtb-6.1.10-nabu').is_file(), 'Original kernel files missing')
    text = configure_boot_text(CFG.read_text(), restore=True)
    text = re.sub(r'^boot.default = "[^"\n]+"$', 'boot.default = "linux"', text, flags=re.M)
    atomic_write(CFG, text.encode())
    print('Original Linux kernel restored in boot configuration. Reboot before removing nabu-camera.')


def before_remove():
    require(os.uname().release != RELEASE,
            'Run sudo nabu-camera-restore-kernel, reboot, then remove this package.')
    restore_kernel()
    subprocess.run(['systemctl','disable','--now','nabu-camera-prepare.service'], check=True)
    subprocess.run(['systemctl','disable','--now','nabu-camera-bridge.service'],check=True)


if __name__ == '__main__':
    try:
        operation = sys.argv[1]
        if operation == 'preflight':
            preflight()
        elif operation == 'configure':
            configure()
        elif operation == 'calibrate':
            calibrate()
            prepare_devices()
        elif operation == 'publish-devices':
            publish_devices()
        elif operation == 'diagnose':
            raise SystemExit(diagnose())
        elif operation == 'remove':
            before_remove()
        elif operation == 'restore-kernel':
            restore_kernel()
        else:
            raise RuntimeError('Unknown operation')
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('nabu-camera: '+str(error), file=sys.stderr)
        raise SystemExit(1)
