#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Read/decode the actual files produced by the final camera UI scenarios."""
import json
from pathlib import Path
import subprocess
from PIL import Image,ImageStat
root=Path('/data/pad5-camera/deb-validation')
rear=root/'gui-hd-fixed-1.2';front=root/'gui-front-final-1.2'
report={'photos':{},'videos':{},'passed':False}
for side,directory,size in [('front',front,(3272,2464)),('rear',rear,(4200,3120))]:
    photo=directory/(side+'-full.jpg');im=Image.open(photo);im.load();assert im.size==size,(photo,im.size)
    stats=ImageStat.Stat(im)
    report['photos'][side]={'size':list(im.size),'mean_rgb':stats.mean,'stddev_rgb':stats.stddev,'bytes':photo.stat().st_size}
    assert max(stats.stddev)>2,'Photo is blank'
    video=directory/(side+'-video.mp4')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-count_frames',
            '-show_entries','stream=codec_name,width,height,avg_frame_rate,r_frame_rate,nb_read_frames,duration',
            '-of','json',str(video)],text=True))['streams'][0]
    assert probe['codec_name']=='h264' and int(probe['nb_read_frames'])>100
    assert 4<float(probe['duration'])<9,probe
    expected=(1632,1232) if side=='front' else (1920,1080)
    assert (probe['width'],probe['height'])==expected
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),'-f','null','-'],check=True)
    report['videos'][side]=probe
im=Image.open(rear/'rear-flash.jpg');im.load();assert im.size==(4200,3120)
report['flash_photo']={'size':list(im.size),'mean_rgb':ImageStat.Stat(im).mean}
report['led_final']={name:Path('/sys/class/leds/nabu:white:flash',name).read_text().strip() for name in ['brightness','flash_strobe']}
assert set(report['led_final'].values())=={'0'}
report['passed']=True
(root/'media-final-1.2.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
