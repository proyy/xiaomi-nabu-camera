#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Actual third-party GStreamer V4L2 frames, demand power states, and flash optics."""
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import gi
gi.require_version('Gst','1.0')
from gi.repository import Gst
from PIL import Image, ImageStat

Gst.init(None)
output=Path(sys.argv[1]); output.mkdir(parents=True,exist_ok=False)
led=Path('/sys/class/leds/nabu:white:flash')
report={'cameras':{},'flash':{}}
def save_report():
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')

def states():
    return {side:Path('/sys/bus/i2c/devices')/device/'power/runtime_status'
            for side,device in [('front','4-0010'),('rear','3-0010')]}
def light(name,value):
    subprocess.run(['sudo','-n','python3','-c',
        'from pathlib import Path; Path('+repr(str(led/name))+').write_text('+repr(str(value))+')'],check=True)

for side,device,w,h in [('front',42,800,600),('rear',43,1024,576)]:
    print(side+' starting',flush=True)
    pipeline=Gst.parse_launch(f'v4l2src device=/dev/video{device} ! videoconvert ! video/x-raw,format=RGB ! appsink name=frames sync=false max-buffers=3 drop=true')
    sink=pipeline.get_by_name('frames'); times=[]; levels=[]; frames={}
    assert pipeline.set_state(Gst.State.PLAYING)!=Gst.StateChangeReturn.FAILURE
    begin=time.monotonic(); phase='off'
    try:
        while time.monotonic()-begin<12:
            sample=sink.emit('try-pull-sample',Gst.SECOND)
            if sample is None:
                message=pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
                raise RuntimeError(str(message.parse_error()) if message else 'No frame within 1 second')
            data=sample.get_buffer().extract_dup(0,sample.get_buffer().get_size())
            # RGB rows from videoconvert are 4-byte aligned.
            photo=Image.frombytes('RGB',(w,h),data,'raw','RGB',((w*3+3)//4)*4)
            now=time.monotonic(); elapsed=now-begin
            if elapsed>=2:
                times.append(now); levels.append({'t':elapsed,'phase':phase,'luma':ImageStat.Stat(photo.convert('L')).mean[0]})
                frames[phase]=photo
                if phase=='flash' and (not report['flash'].get('peak_luma') or levels[-1]['luma']>report['flash']['peak_luma']):
                    report['flash']['peak_luma']=levels[-1]['luma']
                    photo.save(output/'rear-flash-peak.jpg')
            if side=='rear':
                if elapsed>5 and phase=='off':
                    light('brightness',128); phase='torch'
                    print('torch on',flush=True)
                elif elapsed>7 and phase=='torch':
                    light('brightness',0); phase='off-again'
                    print('torch off',flush=True)
                elif elapsed>9 and phase=='off-again':
                    light('flash_timeout',300000); light('flash_brightness',250000)
                    light('flash_strobe',1); phase='flash'
                    print('flash strobed',flush=True)
            if elapsed>=2:
                assert states()[side].read_text().strip()=='active'
        intervals=[b-a for a,b in zip(times,times[1:])]
        report['cameras'][side]={'frames':len(times),'fps':(len(times)-1)/(times[-1]-times[0]),
                                'max_interval_ms':max(intervals)*1000,'resolution':[w,h]}
        for name,photo in frames.items():photo.save(output/f'{side}-{name}.jpg')
        if side=='rear':
            report['flash'].update({'levels':levels,'fault':(led/'flash_fault').read_text().strip(),
                                   'strobe_after_timeout':(led/'flash_strobe').read_text().strip()})
        save_report()
        print(json.dumps(report['cameras'][side]),flush=True)
    finally:
        if side=='rear':
            light('brightness',0);light('flash_strobe',0)
        pipeline.set_state(Gst.State.NULL)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and any(p.read_text().strip()!='suspended' for p in states().values()):time.sleep(.1)
    assert all(p.read_text().strip()=='suspended' for p in states().values())
report['idle_power_states']={side:p.read_text().strip() for side,p in states().items()}
(output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='flash'}))
