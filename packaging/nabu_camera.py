#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Packaged Xiaomi nabu camera: independent runtime, GTK preview and CLI."""
import argparse
from collections import deque
import fcntl
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path('/opt/nabu-camera')
RUNTIME = ROOT / 'runtime'
RELEASE = '6.1.10-nabu-camera3'
IDS = {side: f'/base/soc@0/cci@ac4a000/i2c-bus@{bus}/camera@10'
       for side, bus in [('front', 1), ('rear', 0)]}
SIZES = {'front': (1600, 1200), 'rear': (1920, 1080)}
NATIVE_SIZES = {'fast': {'front': (1632, 1232), 'rear': (1920, 1080)},
                'full': {'front': (3272, 2464), 'rear': (4200, 3120)}}
FRAME = re.compile(r'^(\d+\.\d+) .*?cam\d+-stream0 status: (\d+) seq: (\d+) bytesused: (\d+)')


def environment():
    env = dict(os.environ)
    # Use one consistent ABI and IPA set, regardless of system libcamera version.
    env.update(LD_LIBRARY_PATH=str(RUNTIME/'lib/aarch64-linux-gnu'),
               LIBCAMERA_IPA_MODULE_PATH=str(RUNTIME/'lib/aarch64-linux-gnu/libcamera/ipa'),
               LIBCAMERA_IPA_CONFIG_PATH=str(RUNTIME/'share/libcamera/ipa'),
               LIBCAMERA_IPA_PROXY_PATH=str(RUNTIME/'libexec/libcamera'),
               LIBCAMERA_PIPELINES_MATCH_LIST='simple', LIBCAMERA_SOFTISP_MODE='gpu')
    return env


def platform_check():
    compatible = Path('/proc/device-tree/compatible')
    if not compatible.exists() or b'xiaomi,nabu' not in compatible.read_bytes().split(b'\0'):
        raise RuntimeError('此软件包只适用于 Xiaomi Pad 5 (nabu)')
    if os.uname().release != RELEASE:
        raise RuntimeError('请重启进入原来的 Linux 系统，再打开相机')
    if not os.access('/dev/dma_heap/system', os.R_OK | os.W_OK):
        raise RuntimeError('没有相机设备权限。请重新登录桌面；SSH 用户需加入 video 组')


def camera_list():
    platform_check()
    if (ROOT/'camera-bridge').is_file():
        # A second CameraManager can reconfigure the media graph. Query the
        # prepared kernel endpoints without disturbing an active consumer.
        for side,number,sensor in [('front',42,'4-0010'),('rear',43,'3-0010')]:
            name=Path(f'/sys/class/video4linux/video{number}/name')
            if not name.exists() or name.read_text().strip()!=f'Pad-{side.title()}-Camera' or not Path('/sys/bus/i2c/devices',sensor,'driver').exists():
                raise RuntimeError(side+' 摄像头设备未就绪')
        if subprocess.call(['systemctl','is-active','--quiet','nabu-camera-bridge.service']):
            raise RuntimeError('相机服务未启动，请运行 nabu-camera --diagnose')
        return {'front':IDS['front'],'rear':IDS['rear'],'devices':{'front':'/dev/video42','rear':'/dev/video43'},'kernel':os.uname().release}
    p = subprocess.run([str(RUNTIME/'bin/cam'), '--list'], env=environment(),
                       capture_output=True, text=True, timeout=20)
    if p.returncode or not all(camera in p.stdout for camera in IDS.values()):
        raise RuntimeError('前后摄像头未全部识别\n'+p.stderr[-3000:]+p.stdout)
    return {'front': IDS['front'], 'rear': IDS['rear'], 'kernel': os.uname().release}


class NativeLease:
    """One native CameraManager at a time; generic devices return on release."""
    def __init__(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(8)
        try:
            self.socket.connect('/run/nabu-camera/native.sock')
            if self.socket.recv(32) != b'OK\n':
                raise RuntimeError('相机正被其他应用使用，请先关闭那个应用')
        except BaseException:
            self.socket.close()
            raise
    def close(self):
        self.socket.close()


class Capture:
    def __init__(self, side, mode='system', lease=None):
        self.side = side
        self.mode, self.lease, self.own_lease = mode, lease, False
        self.width, self.height = (SIZES if mode=='system' else NATIVE_SIZES[mode])[side]
        self.ram = None
        self.process = None
        self.lens = None
        self.reader = None
        self.focus_lock = threading.Lock()
        self.lines = deque(maxlen=60)
        self.frames = self.errors = self.missing = self.nonmonotonic = 0
        self.first_time = self.last_time = None
        self.last_sequence = None
        self.closed = False
        self.latest = None
        self.mtime = None
        self.received_at = time.monotonic()

    def start(self):
        platform_check()
        try:
            if self.mode != 'system' and self.lease is None:
                self.lease, self.own_lease = NativeLease(), True
            self.ram = tempfile.TemporaryDirectory(prefix='nabu-camera-', dir='/dev/shm')
            self.image = Path(self.ram.name)/'live.ppm'
            if self.side == 'rear':
                node = subprocess.check_output(['media-ctl', '-d', '/dev/media0', '-e',
                                                'nabu-cn3927 3-000c'], text=True).strip()
                if not re.fullmatch(r'/dev/v4l-subdev\d+', node):
                    raise RuntimeError('后置对焦设备未找到')
                self.lens = os.open(node, os.O_RDWR)
            command = [str(RUNTIME/'bin/cam'), '-c', IDS[self.side], '-s',
                       f'role=viewfinder,width={self.width},height={self.height},pixelformat=XRGB8888',
                       '--strict-formats', '--capture', '--file='+str(self.image)]
            if self.mode=='system' and (ROOT/'camera-bridge').is_file() and Path('/dev/video42').exists():
                command = [str(ROOT/'camera-bridge'), '--capture',
                           '/dev/video'+('42' if self.side=='front' else '43'), str(self.image)]
            elif self.mode!='system':
                command = [str(ROOT/'camera-bridge'), '--parent-bound'] + command
            self.process = subprocess.Popen(command, env=environment(), stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1)
            self.reader = threading.Thread(target=self._read_log, daemon=True)
            self.reader.start()
        except BaseException:
            self.stop()
            raise
        return self

    def _read_log(self):
        for line in self.process.stdout:
            self.lines.append(line.rstrip())
            m = FRAME.match(line)
            if not m:
                continue
            stamp, status, sequence, size = m.groups()
            stamp, status, sequence, size = float(stamp), int(status), int(sequence), int(size)
            self.frames += 1
            self.errors += int(status != 0 or size <= 0)
            if self.last_sequence is not None:
                self.missing += max(0, sequence-self.last_sequence-1)
                self.nonmonotonic += int(sequence <= self.last_sequence or stamp <= self.last_time)
            if self.first_time is None:
                self.first_time = stamp
            self.last_time, self.last_sequence = stamp, sequence

    def snapshot(self, previous=None):
        if self.closed or self.process.poll() is not None:
            raise RuntimeError('相机已停止\n'+'\n'.join(self.lines)[-2500:])
        try:
            # cam publishes by atomic rename. Keep one opened inode: a newer
            # frame may replace the path while this complete frame is copied.
            with self.image.open('rb') as source:
                stamp = os.fstat(source.fileno()).st_mtime_ns
                if stamp == previous:
                    return None
                data = source.read()
            header = f'P6\n{self.width} {self.height}\n255\n'.encode()
            if data.startswith(header) and len(data) == len(header)+self.width*self.height*3:
                self.latest, self.mtime = data, stamp
                self.received_at = time.monotonic()
                return data, stamp
        except FileNotFoundError:
            pass
        return None

    def wait_frame(self, previous=None, timeout=5):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            frame = self.snapshot(previous)
            if frame is not None:
                return frame
            time.sleep(.01)
        raise RuntimeError('5 秒内未收到完整画面')

    def lens_position(self, code=None):
        if self.lens is None:
            raise RuntimeError('前置为固定焦点镜头')
        if code is not None:
            if not 0 <= code <= 1023:
                raise ValueError('无效的镜头位置')
            fcntl.ioctl(self.lens, 0xc008561c, bytearray(struct.pack('<Ii', 0x009a090a, code)))
        control = bytearray(struct.pack('<Ii', 0x009a090a, 0))
        fcntl.ioctl(self.lens, 0xc008561b, control)
        actual = struct.unpack('<Ii', control)[1]
        if code is not None and actual != code:
            raise RuntimeError('镜头未到达目标位置')
        return actual

    def autofocus(self):
        from PIL import Image, ImageChops, ImageStat
        if not self.focus_lock.acquire(blocking=False):
            raise RuntimeError('正在对焦')
        old = None
        try:
            calibration = json.loads(Path('/var/lib/nabu-camera/focus.json').read_text())
            low, high = calibration['native_dac_bounds']
            if not 0 < low < high <= 733:
                raise RuntimeError('EEPROM 对焦范围无效')
            old = self.lens_position()
            scored = {}
            def evaluate(code):
                self.lens_position(code)
                time.sleep(.25)
                scores, stamp = [], None
                for _ in range(3):
                    raw, stamp = self.wait_frame(stamp)
                    im = Image.open(io.BytesIO(raw)).getchannel('G')
                    w, h = im.size
                    im = im.crop((w//3, h//3, 2*w//3, 2*h//3))
                    im = im.resize((im.width//2, im.height//2), Image.Resampling.BOX)
                    mean = ImageStat.Stat(im).mean[0]
                    if not 10 < mean < 240:
                        raise RuntimeError('中央区域过暗或过亮，保留原焦点')
                    a = im.crop((0, 0, im.width-2, im.height-2))
                    b = im.crop((2, 0, im.width, im.height-2))
                    c = im.crop((0, 2, im.width-2, im.height))
                    scores.append((ImageStat.Stat(ImageChops.difference(a,b)).rms[0]**2
                                   +ImageStat.Stat(ImageChops.difference(a,c)).rms[0]**2)/mean**2)
                scored[code] = statistics.median(scores)
            coarse = sorted(set(range(low, high+1, 32)) | {high})
            for code in coarse:
                evaluate(code)
            best = max(scored, key=scored.get)
            for code in range(max(low, best-32), min(high, best+32)+1, 8):
                if code not in scored:
                    evaluate(code)
            best = max(scored, key=scored.get)
            ratio = scored[best]/max(min(scored.values()), 1e-12)
            if ratio < 1.1:
                self.lens_position(old)
                return {'focused': False, 'reason': '中央区域纹理不足，保留原焦点', 'ratio': ratio}
            self.lens_position(best)
            return {'focused': True, 'dac': best, 'ratio': ratio,
                    'peak_inside_window': low < best < high}
        except BaseException:
            if old is not None and not self.closed:
                self.lens_position(old)
            raise
        finally:
            self.focus_lock.release()

    def stop(self):
        if self.closed:
            return
        self.closed = True
        if self.process:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            if self.reader:
                self.reader.join(timeout=2)
            self.process.stdout.close()
        if self.lens is not None:
            os.close(self.lens)
            self.lens = None
        if self.ram:
            self.ram.cleanup()
        if self.own_lease and self.lease:
            self.lease.close()
            self.lease = None

    def result(self):
        duration = (self.last_time-self.first_time) if self.frames > 1 else 0
        return {'camera': self.side, 'frames': self.frames, 'seconds': duration,
                'fps': (self.frames-1)/duration if duration else 0,
                'error_frames': self.errors, 'missing_sequences': self.missing,
                'nonmonotonic': self.nonmonotonic,
                'exit': self.process.returncode if self.process else None}


def save_photo(data, path, rotation=0):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if rotation:
        im = im.rotate(rotation, expand=True)
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as output:
        im.save(output, format='PNG' if path.suffix.lower()=='.png' else 'JPEG', quality=95)
    return str(path)


def self_test(output, seconds):
    if (ROOT/'camera-bridge').is_file():
        return system_self_test(output,seconds)
    report = {'cameras': camera_list(), 'tests': [], 'complete': False}
    process, lens = None, None
    with tempfile.TemporaryDirectory(prefix='nabu-camera-check-', dir='/dev/shm') as temporary:
        ram = Path(temporary)
        command = [str(RUNTIME/'bin/cam')]
        for side, camera in IDS.items():
            width, height = SIZES[side]
            command += ['-c', camera, '-s',
                        f'role=viewfinder,width={width},height={height},pixelformat=XRGB8888',
                        '--strict-formats', '--capture', '--file='+str(ram/(side+'.ppm'))]
        try:
            node = subprocess.check_output(['media-ctl', '-d', '/dev/media0', '-e',
                                            'nabu-cn3927 3-000c'], text=True).strip()
            if not re.fullmatch(r'/dev/v4l-subdev\d+', node):
                raise RuntimeError('Native focus device missing')
            lens = os.open(node, os.O_RDWR)
            with (ram/'capture.log').open('w') as log:
                process = subprocess.Popen(command, env=environment(), stdout=log, stderr=subprocess.STDOUT)
                try:
                    process.wait(timeout=seconds)
                    raise RuntimeError('双路采集提前退出')
                except subprocess.TimeoutExpired:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=5)
            text = (ram/'capture.log').read_text()
            for index, side in enumerate(IDS):
                frames = [(float(t), int(s), int(q), int(n)) for t,s,q,n in re.findall(
                    rf'^(\d+\.\d+) .*?cam{index}-stream0 status: (\d+) seq: (\d+) bytesused: (\d+)', text, re.M)]
                duration = frames[-1][0]-frames[0][0] if len(frames)>1 else 0
                result = {'camera':side, 'frames':len(frames), 'seconds':duration,
                          'fps':(len(frames)-1)/duration if duration else 0,
                          'error_frames':sum(s!=0 or n<=0 for t,s,q,n in frames),
                          'missing_sequences':sum(max(0,b[2]-a[2]-1) for a,b in zip(frames,frames[1:])),
                          'nonmonotonic':sum(b[2]<=a[2] or b[0]<=a[0] for a,b in zip(frames,frames[1:])),
                          'exit':process.returncode}
                report['tests'].append(result)
                save_photo((ram/(side+'.ppm')).read_bytes(), output/(side+'.jpg'))
            report['complete'] = len(report['tests']) == 2 and all(
                x['exit']==0 and x['seconds']>=seconds-5 and x['error_frames']==0
                and x['nonmonotonic']==0 and x['missing_sequences']==0 for x in report['tests'])
        except BaseException as error:
            report['failure'] = str(error)
            raise
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            if lens is not None:
                os.close(lens)
            if (ram/'capture.log').exists():
                (output/'capture.log').write_bytes((ram/'capture.log').read_bytes())
            (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return 0 if report['complete'] else 1


def system_self_test(output,seconds):
    report={'cameras':camera_list(),'tests':[],'complete':False,
            'measurement':'V4L2 client frames; sensor sequence counters are in the camera service journal'}
    captures=[]
    try:
        for side in IDS:
            captures.append(Capture(side).start())
        for capture in captures:
            capture.wait_frame(timeout=10)
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            for capture in captures:
                capture.snapshot(capture.mtime)
                if time.monotonic()-capture.received_at>3:
                    raise RuntimeError(capture.side+' 摄像头超过 3 秒没有新画面')
            time.sleep(.02)
        for capture in captures:
            save_photo(capture.latest,output/(capture.side+'.jpg'))
    except BaseException as error:
        report['failure']=str(error)
    finally:
        for capture in captures:
            capture.stop()
            report['tests'].append(capture.result())
        report['complete']=not report.get('failure') and len(report['tests'])==2 and all(
            r['exit']==0 and r['frames']>seconds*20 and not any(r[k] for k in ['error_frames','nonmonotonic'])
            for r in report['tests'])
        (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['complete'] else 1


def preview(side, smoke=None, smoke_seconds=20):
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib, Gdk
    from preview_gl import PreviewGL
    import flash_control
    if not Gtk.init_check()[0]:
        raise RuntimeError('预览需要 Ubuntu 桌面。控制台可用 nabu-camera --capture 照片.jpg')
    window = Gtk.Window(title='Pad 相机')
    window.set_default_size(1000, 720)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    root.set_border_width(12)
    window.add(root)
    toolbar = Gtk.Box(spacing=8)
    root.pack_start(toolbar, False, False, 0)
    selector = Gtk.ComboBoxText()
    selector.append('rear', '后置摄像头')
    selector.append('front', '前置摄像头')
    selector.set_active_id(side)
    toolbar.pack_start(selector, False, False, 0)
    photo = Gtk.Button(label='拍照')
    focus = Gtk.Button(label='自动对焦')
    rotate = Gtk.Button(label='旋转画面')
    for button in [photo, focus, rotate]:
        toolbar.pack_start(button, False, False, 0)
    flash = Gtk.ComboBoxText()
    for key,label in [('off','闪光灯关闭'),('photo','拍照闪光'),('torch','补光常亮')]:
        flash.append(key,label)
    flash.set_active_id('off')
    toolbar.pack_start(flash,False,False,0)
    image = PreviewGL()
    image.set_hexpand(True)
    image.set_vexpand(True)
    root.pack_start(image, True, True, 0)
    fps_label = Gtk.Label(label='')
    toolbar.pack_end(fps_label, False, False, 0)
    status = Gtk.Label(label='正在启动相机…')
    status.set_line_wrap(True)
    root.pack_start(status, False, False, 0)
    state = {'capture': None, 'mtime': None, 'rotation': 0, 'focusing': False,
             'closed': False, 'shown': {'front':0,'rear':0}, 'failures': [], 'started': time.monotonic(),
             'display_times': {'front':deque(maxlen=4096),'rear':deque(maxlen=4096)},
             'capture_results': [], 'drawn': None, 'next_fps_update': 0, 'measuring': True}

    def fail(exc):
        status.set_text(str(exc))
        state['failures'].append(str(exc))

    def start(*_):
        if state['focusing']:
            return
        flash_control.off()
        flash.set_active_id('off')
        if state['capture']:
            state['capture'].stop()
            state['capture_results'].append(state['capture'].result())
        image.frame = None
        state['capture'], state['mtime'] = None, None
        state['measuring'] = True
        selected = selector.get_active_id()
        try:
            state['capture'] = Capture(selected).start()
            focus.set_sensitive(selected == 'rear' and Path('/var/lib/nabu-camera/focus.json').exists())
            flash.set_sensitive(selected=='rear' and flash_control.available())
            status.set_text('正在启动'+('后置' if selected=='rear' else '前置')+'摄像头…')
            state['started'] = time.monotonic()
        except Exception as exc:
            fail(exc)

    def tick(widget, frame_clock):
        if state['closed']:
            return False
        capture = state['capture']
        if capture:
            try:
                if image.error or image.get_error():
                    raise RuntimeError(image.error or str(image.get_error()))
                frame = capture.snapshot(state['mtime'])
                if frame:
                    data, state['mtime'] = frame
                    header = f'P6\n{capture.width} {capture.height}\n255\n'.encode()
                    image.submit(data[len(header):], capture.width, capture.height,
                                 state['mtime'], state['rotation'])
                    state['shown'][capture.side] += 1
                    if state['shown'][capture.side] == 1:
                        status.set_text('画面已就绪；照片保存到 Pictures/Pad Camera')
                elif capture.latest is None and time.monotonic()-state['started'] > 10:
                    raise RuntimeError('相机启动超时，请运行 nabu-camera --diagnose')
                elif capture.latest is not None and time.monotonic()-capture.received_at>3:
                    raise RuntimeError('相机超过 3 秒没有新画面，请运行 nabu-camera --diagnose')
            except Exception as exc:
                fail(exc)
                capture.stop()
                state['capture'] = None
        return True

    def take_photo(*_, destination=None):
        try:
            capture = state['capture']
            if not capture or not capture.latest:
                raise RuntimeError('画面尚未就绪')
            destination = destination or Path.home()/'Pictures/Pad Camera'/(
                time.strftime('%Y%m%d-%H%M%S')+f'-{time.time_ns()%1000000000:09d}-{capture.side}.jpg')
            data=flash_control.photo(capture) if flash.get_active_id()=='photo' else capture.latest
            status.set_text('已保存：'+save_photo(data, destination, state['rotation']))
        except Exception as exc:
            fail(exc)

    def do_focus(*_):
        if not state['capture'] or state['focusing']:
            return
        state['focusing'] = True
        for control in [selector, focus, photo]:
            control.set_sensitive(False)
        status.set_text('正在对焦，请保持片刻…')
        def work():
            try:
                result = state['capture'].autofocus()
                message = '对焦完成' if result['focused'] else result['reason']
            except Exception as exc:
                message = str(exc)
            def done():
                state['focusing'] = False
                if not state['closed']:
                    for control in [selector, focus, photo]:
                        control.set_sensitive(True)
                    status.set_text(message)
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    def close(*_):
        if state['focusing']:
            status.set_text('正在结束对焦，请片刻后关闭')
            return True
        state['closed'] = True
        flash_control.off()
        if state['capture']:
            state['capture'].stop()
            state['capture_results'].append(state['capture'].result())
        Gtk.main_quit()
        return False

    def painted(frame_clock):
        capture = state['capture']
        if capture and state['measuring'] and image.rendered_serial is not None and image.rendered_serial != state['drawn']:
            state['drawn'] = image.rendered_serial
            now = time.monotonic()
            samples = state['display_times'][capture.side]
            samples.append(now)
            if now >= state['next_fps_update']:
                recent = [t for t in samples if now-t < 2]
                if len(recent) > 2:
                    fps_label.set_text(f'{(len(recent)-1)/(recent[-1]-recent[0]):.0f} fps')
                state['next_fps_update'] = now+1

    selector.connect('changed', start)
    def flash_changed(*_):
        try:
            flash_control.torch(flash.get_active_id()=='torch')
        except Exception as exc:
            fail(exc)
    flash.connect('changed',flash_changed)
    photo.connect('clicked', take_photo)
    focus.connect('clicked', do_focus)
    def rotation(*_):
        state['rotation'] = (state['rotation']+90)%360
        state['mtime'] = None
    rotate.connect('clicked', rotation)
    window.connect('delete-event', close)
    window.show_all()
    if smoke:
        window.maximize()
    window.get_frame_clock().connect('after-paint', painted)
    start()
    image.add_tick_callback(tick)
    if smoke:
        smoke.mkdir(parents=True, exist_ok=True)
        def switch_smoke():
            take_photo(destination=smoke/'rear.jpg')
            selector.set_active_id('front')
            return False
        def finish_smoke():
            take_photo(destination=smoke/'front.jpg')
            close()
            return False
        def screenshot(side):
            # PNG readback is a test-only stall, outside the preview benchmark.
            state['measuring'] = False
            image.capture_next_frame(smoke/(side+'-preview.png'))
            return False
        GLib.timeout_add_seconds(smoke_seconds-1, screenshot, 'rear')
        GLib.timeout_add_seconds(smoke_seconds*2-1, screenshot, 'front')
        GLib.timeout_add_seconds(smoke_seconds, switch_smoke)
        GLib.timeout_add_seconds(smoke_seconds*2, finish_smoke)
    Gtk.main()
    if smoke:
        performance = {}
        for side, timestamps in state['display_times'].items():
            times = [t for t in timestamps if t-timestamps[0] >= 3]
            intervals = sorted(b-a for a,b in zip(times,times[1:]))
            performance[side] = {'displayed_frames': len(times),
                'display_fps': (len(times)-1)/(times[-1]-times[0]) if len(times)>1 else 0,
                'p95_interval_ms': intervals[int(.95*(len(intervals)-1))]*1000 if intervals else None,
                'max_interval_ms': max(intervals)*1000 if intervals else None}
        result = {'shown': state['shown'], 'failures': state['failures'], 'renderer': image.renderer,
                  'measurement': 'GTK after-paint, unique frames; excludes 3s warmup and test-only PNG readback',
                  'capture_results':state['capture_results'], 'display_performance': performance,
                  'passed': all(v >= 3 for v in state['shown'].values()) and not state['failures']
                            and all(x['display_fps'] >= 25 for x in performance.values())
                            and len(state['capture_results']) == 2
                            and all(x['exit'] == 0 and x['frames'] > 0
                                    and not any(x[k] for k in ('error_frames','missing_sequences','nonmonotonic'))
                                    for x in state['capture_results'])
                            and all((smoke/(side+'-preview.png')).exists() for side in IDS)}
        (smoke/'gui-result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
        return 0 if result['passed'] else 1
    return 0


def main():
    parser = argparse.ArgumentParser(description='Xiaomi Pad 5 Ubuntu 相机')
    parser.add_argument('--camera', choices=IDS, default='rear')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='检查前后相机识别')
    mode.add_argument('--capture', type=Path, help='拍照到 JPEG/PNG 文件')
    mode.add_argument('--self-test', type=Path, metavar='DIRECTORY', help='双路采集自检并保存报告/照片')
    mode.add_argument('--diagnose', action='store_true', help='安装与硬件检查')
    mode.add_argument('--gui-test', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--seconds', type=int, default=20, help='自检持续秒数，10–610')
    parser.add_argument('--autofocus', action='store_true', help='拍照前执行后置有限扫描对焦')
    parser.add_argument('--flash', action='store_true', help='后置拍照时使用硬件闪光灯')
    parser.add_argument('--quality', choices=['fast','full'], default='full', help='原生分辨率：fast 高帧率，full 全像素')
    args = parser.parse_args()
    if not 10 <= args.seconds <= 610:
        parser.error('--seconds 范围为 10–610')
    if args.list:
        print(json.dumps(camera_list(), indent=2))
        return 0
    if args.diagnose:
        return subprocess.call(['/usr/bin/python3', str(ROOT/'install_support.py'), 'diagnose'])
    if args.self_test:
        args.self_test.mkdir(parents=True, exist_ok=False)
        return self_test(args.self_test, args.seconds)
    if args.capture:
        if args.flash and args.camera!='rear':
            parser.error('--flash 仅支持后置摄像头')
        capture = Capture(args.camera,args.quality).start()
        try:
            capture.wait_frame()
            time.sleep(3)
            if args.autofocus:
                print(json.dumps(capture.autofocus()))
            raw, _ = capture.wait_frame(capture.mtime)
            if args.flash:
                from flash_control import photo
                raw=photo(capture)
            print(save_photo(raw, args.capture))
        finally:
            capture.stop()
        if capture.process.returncode != 0:
            raise RuntimeError('相机退出异常')
        return 0
    from camera_ui import preview as camera_preview
    return camera_preview(args.camera, args.gui_test, args.seconds)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
