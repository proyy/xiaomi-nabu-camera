# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""The real PM8150L rear LED. Every capture clears torch and strobe on exit."""
import io
from pathlib import Path
import time
from PIL import Image, ImageStat

LED=Path('/sys/class/leds/nabu:white:flash')

def available():
    return (LED/'flash_strobe').exists()

def write(name,value):
    (LED/name).write_text(str(value))

def off():
    if available():
        try:
            write('brightness',0)
        finally:
            write('flash_strobe',0)

def torch(enabled):
    off()
    if enabled:
        write('brightness',128)  # Approx. 50 mA; kernel max is 100 mA.

def photo(capture):
    if capture.side!='rear' or not available():
        raise RuntimeError('后置闪光灯驱动未就绪')
    off()
    previous=capture.mtime
    frames=[]
    try:
        # Meter with the same LED current before the short flash. Otherwise the
        # dark-scene AGC exposure makes a close subject completely saturated.
        write('brightness',255)  # 100 mA, matching the following flash.
        deadline=time.monotonic()+1.2
        while time.monotonic()<deadline:
            frame=capture.snapshot(previous)
            if frame:
                _,previous=frame
            time.sleep(.01)
        off()
        write('flash_brightness',100000)
        write('flash_timeout',300000)
        write('flash_strobe',1)
        started=time.monotonic()
        deadline=started+.28
        while time.monotonic()<deadline:
            frame=capture.snapshot(previous)
            if frame and time.monotonic()-started>=.04:
                data,previous=frame
                image=Image.open(io.BytesIO(data)).convert('L')
                frames.append((ImageStat.Stat(image).mean[0],data))
            time.sleep(.005)
        if not frames:
            raise RuntimeError('闪光期间没有收到相机画面')
        fault=(LED/'flash_fault').read_text().strip()
        if fault and fault!='flash-timeout-exceeded':
            raise RuntimeError('闪光灯故障：'+fault)
        # Prefer a usable exposure over a transient clipped frame.
        return min(frames,key=lambda item:abs(item[0]-170))[1]
    finally:
        off()
