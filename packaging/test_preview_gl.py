#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Exercise the installed GLES renderer on the real desktop, without a camera."""
import json
from pathlib import Path
import sys

from PIL import Image
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
sys.path.insert(0, '/opt/nabu-camera')
from preview_gl import PreviewGL

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=False)
source = Image.new('RGB', (400,240))
for box, color in [((0,0,200,120),(255,0,0)),((200,0,400,120),(0,255,0)),
                   ((0,120,200,240),(0,0,255)),((200,120,400,240),(255,255,0))]:
    source.paste(color,box)
window = Gtk.Window()
window.set_default_size(900,700)
preview = PreviewGL()
window.add(preview)
window.show_all()
results = []

def display(angle):
    preview.submit(source.tobytes(),*source.size,angle,angle)
    preview.capture_next_frame(output/f'rotation-{angle}.png')
    return False

def check(angle):
    try:
        assert not preview.error and not preview.get_error(), preview.error or str(preview.get_error())
        actual = Image.open(output/f'rotation-{angle}.png').convert('RGB')
        expected = source.rotate(angle,expand=True)
        aw,ah=actual.size
        ratio=min(aw/expected.width,ah/expected.height)
        w,h=int(expected.width*ratio),int(expected.height*ratio)
        left,top=(aw-w)//2,(ah-h)//2
        for x,y in ((.25,.25),(.75,.25),(.25,.75),(.75,.75)):
            got=actual.getpixel((left+int(x*w),top+int(y*h)))
            want=expected.getpixel((int(x*expected.width),int(y*expected.height)))
            assert max(abs(a-b) for a,b in zip(got,want)) <= 3, (got,want)
        results.append({'rotation':angle,'passed':True})
    except Exception as error:
        results.append({'rotation':angle,'passed':False,'error':str(error)})
    if angle == 270:
        Gtk.main_quit()
    return False

for index,angle in enumerate((0,90,180,270)):
    GLib.timeout_add(200+index*1500,display,angle)
    GLib.timeout_add(1400+index*1500,check,angle)
Gtk.main()
result={'renderer':preview.renderer,'tests':results,'passed':len(results)==4 and all(x['passed'] for x in results)}
(output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
window.destroy()
raise SystemExit(0 if result['passed'] else 1)
