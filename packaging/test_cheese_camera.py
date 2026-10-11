#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Exercise the real Cheese desktop UI and its PipeWire camera devices."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
os.environ.update(XDG_RUNTIME_DIR='/run/user/1000',WAYLAND_DISPLAY='wayland-0',
                  GDK_BACKEND='wayland',DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus')
import gi
gi.require_version('Atspi','2.0')
from gi.repository import Atspi,GLib
from PIL import Image,ImageStat

assert subprocess.run(['pgrep','-x','cheese'],stdout=subprocess.DEVNULL).returncode!=0,'Close Cheese first'
output=Path(sys.argv[1]); output.mkdir(parents=True,exist_ok=False)
pics=Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES))/'Webcam'
process=subprocess.Popen(['/usr/bin/cheese'])
app=None; result={}

def walk(node,depth=0):
    yield node
    if depth<14:
        for i in range(node.get_child_count()):
            yield from walk(node.get_child_at_index(i),depth+1)

def click(node):
    assert node.get_action_iface().do_action(0)
    time.sleep(.3)

try:
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        root=Atspi.get_desktop(0)
        app=next((root.get_child_at_index(i) for i in range(root.get_child_count())
                  if root.get_child_at_index(i).get_name()=='cheese'),None)
        if app:break
        assert process.poll() is None,'Cheese exited during startup'
        time.sleep(.2)
    assert app,'Cheese accessibility window did not appear'
    time.sleep(2)
    for side in ['front','rear']:
        for label in ['Menu','Preferences']:
            click(next(n for n in walk(app) if n.get_name()==label))
        combo=next(n for n in walk(app) if n.get_role_name()=='combo box' and n.get_name().startswith('Pad '))
        choices=[n.get_name() for n in walk(combo) if n.get_role_name()=='menu item']
        assert all(any('Pad '+s.title()+' Camera' in x for x in choices) for s in ['front','rear']),choices
        click(combo)
        click(next(n for n in walk(combo) if n.get_role_name()=='menu item' and n.get_name().startswith('Pad '+side.title())))
        dialog=next(app.get_child_at_index(i) for i in range(app.get_child_count())
                    if app.get_child_at_index(i).get_role_name()=='dialog')
        click(next(n for n in walk(dialog) if n.get_name()=='Close'))
        time.sleep(3)
        before=set(pics.glob('*.jpg'))
        click(next(n for n in walk(app) if n.get_description()=='Take a photo using a webcam'))
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            added=set(pics.glob('*.jpg'))-before
            if added:break
            assert process.poll() is None,'Cheese exited while taking photo'
            time.sleep(.2)
        assert len(added)==1,'Cheese did not save exactly one photo'
        source=added.pop();time.sleep(.3)
        target=output/(side+'-cheese.jpg');shutil.copyfile(source,target)
        image=Image.open(target);image.load()
        assert image.size==({'front':(1600,1200),'rear':(1920,1080)}[side]),image.size
        result[side]={'device_choices':choices,'photo':str(source),'size':list(image.size),
                      'luma':ImageStat.Stat(image.convert('L')).mean[0]}
        print(side+' photo saved',flush=True)
        time.sleep(2)
    assert process.poll() is None,'Cheese exited unexpectedly'
finally:
    if app and process.poll() is None:
        click(next(n for n in walk(app) if n.get_name()=='Close'))
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
result['exit']=process.returncode
result['passed']=process.returncode==0 and all(s in result for s in ['front','rear'])
(output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
assert result['passed']
