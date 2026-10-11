# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Full-resolution stills and timestamped H.264 recordings from the native feed."""
import io
from pathlib import Path
import subprocess
import threading
import time
from PIL import Image, ImageFilter


def save_still(data, path, rotation=0, zoom=1., denoise=True):
    image = Image.open(io.BytesIO(data)).convert('RGB')
    if denoise:
        # 3x3 channel median removes isolated high-gain colour speckles without
        # inventing detail or changing output dimensions.
        image = image.filter(ImageFilter.MedianFilter(3))
    if zoom>1:
        w,h=image.size
        cw,ch=int(w/zoom),int(h/zoom)
        image=image.crop(((w-cw)//2,(h-ch)//2,(w+cw)//2,(h+ch)//2)).resize((w,h),Image.Resampling.LANCZOS)
    if rotation:
        image=image.rotate(rotation,expand=True)
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as output:
        image.save(output,format='JPEG',quality=97,subsampling=0)
    return str(path)


class Recorder:
    """Encoding runs outside GTK; wall-clock timestamps keep video duration real."""
    def __init__(self,capture,path,rotation=0,zoom=1.):
        self.capture,self.path=capture,Path(path)
        self.rotation,self.zoom=rotation,zoom
        self.stopping=threading.Event()
        self.error=None
        self.frames=0
        self.started=time.monotonic()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.pending=self.path.with_suffix('.part.mp4')
        self.log=self.path.with_suffix('.encoder.log').open('x')
        filters=[]
        w,h=capture.width,capture.height
        if zoom>1:
            cw,ch=int(w/zoom)//2*2,int(h/zoom)//2*2
            filters += [f'crop={cw}:{ch}',f'scale={w}:{h}']
        if rotation==90:filters.append('transpose=2')
        elif rotation==180:filters += ['hflip','vflip']
        elif rotation==270:filters.append('transpose=1')
        # Native modes exceed some players' H.264 level limits; the encoder
        # chooses a suitable level and records actual arrival timestamps.
        command=['ffmpeg','-hide_banner','-loglevel','warning','-nostdin','-n',
                 '-f','rawvideo','-pixel_format','rgb24','-video_size',f'{w}x{h}',
                 '-framerate','60' if capture.side=='rear' and capture.mode=='fast' else '30',
                 '-use_wallclock_as_timestamps','1','-i','pipe:0',
                 '-an','-c:v','libx264','-preset','ultrafast','-crf','20','-threads','4',
                 '-pix_fmt','yuv420p','-fps_mode','vfr','-enc_time_base','1:1000']
        if filters:command += ['-vf',','.join(filters)]
        command += ['-movflags','+faststart',str(self.pending)]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=self.log)
        self.thread=threading.Thread(target=self._write,daemon=True)
        self.thread.start()

    def _write(self):
        previous=None
        try:
            while not self.stopping.is_set():
                frame=self.capture.snapshot(previous)
                if not frame:
                    time.sleep(.003)
                    continue
                data,previous=frame
                header=f'P6\n{self.capture.width} {self.capture.height}\n255\n'.encode()
                self.process.stdin.write(data[len(header):])
                self.frames+=1
        except BaseException as exc:
            self.error=str(exc)
        finally:
            try:self.process.stdin.close()
            except OSError:pass

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            self.process.kill()
            self.thread.join(timeout=2)
            self.error='录像编码停止超时'
        try:self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill();self.process.wait();self.error='录像封装超时'
        self.log.close()
        if self.error or self.process.returncode or not self.frames:
            raise RuntimeError(self.error or '录像编码失败，请查看 '+str(self.path.with_suffix('.encoder.log')))
        if self.path.exists():raise RuntimeError('录像目标已存在')
        self.pending.rename(self.path)
        if self.path.with_suffix('.encoder.log').stat().st_size==0:
            self.path.with_suffix('.encoder.log').unlink()
        return {'file':str(self.path),'frames_encoded':self.frames,
                'wall_seconds':time.monotonic()-self.started}
