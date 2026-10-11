#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Measure installed flash-photo behavior against ambient and recovered frames."""
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, '/opt/nabu-camera')
from PIL import Image, ImageStat
import flash_control as flash
from nabu_camera import Capture, save_photo

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=False)
camera = Capture('rear').start()
results = {}

def settle(seconds):
    until = time.monotonic()+seconds
    data = None
    while time.monotonic() < until:
        data, _ = camera.wait_frame(camera.mtime)
    return data

def record(name, data):
    image = Image.open(io.BytesIO(data)).convert('L')
    stats = ImageStat.Stat(image)
    hist = image.histogram()
    results[name] = {'mean_luma': stats.mean[0], 'stddev': stats.stddev[0],
                     'clipped_fraction': sum(hist[250:])/sum(hist),
                     'size': list(image.size)}
    save_photo(data, output/(name+'.jpg'))

try:
    flash.off()
    record('ambient', settle(4))
    started = time.monotonic()
    record('flash', flash.photo(camera))
    results['flash_photo_seconds'] = time.monotonic()-started
    record('recovered', settle(4))
finally:
    flash.off()
    camera.stop()

results['led_final'] = {name: (flash.LED/name).read_text().strip()
                        for name in ('brightness', 'flash_strobe', 'flash_fault')}
results['capture'] = camera.result()
results['passed'] = (results['led_final']['brightness'] == '0'
                     and results['led_final']['flash_strobe'] == '0'
                     and results['flash']['stddev'] > 5
                     and results['flash']['clipped_fraction'] < .25
                     and results['capture']['exit'] == 0
                     and results['capture']['error_frames'] == 0)
(output/'result.json').write_text(json.dumps(results, indent=2)+'\n')
print(json.dumps(results, indent=2))
raise SystemExit(0 if results['passed'] else 1)
