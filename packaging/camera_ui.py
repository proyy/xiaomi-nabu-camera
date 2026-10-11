# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Touch camera: icon actions, native resolution, focus, zoom and recording."""
import io
import json
from pathlib import Path
import threading
import time
from collections import deque
from PIL import Image, ImageStat
from nabu_camera import Capture, NativeLease, NATIVE_SIZES, ROOT
from camera_media import Recorder, save_still
import flash_control


def preview(side,smoke=None,smoke_seconds=15):
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk,GLib,Gdk,GdkPixbuf
    from preview_gl import PreviewGL
    if not Gtk.init_check()[0]:raise RuntimeError('预览需要 Ubuntu 桌面')
    lease=NativeLease()
    window=Gtk.Window(title='Pad 相机')
    window.set_default_size(1100,780)
    css=Gtk.CssProvider()
    css.load_from_data(b'window{background:#101318;color:#edf2fa;} button{background:#252c35;color:#edf2fa;border:0;border-radius:16px;padding:12px;min-width:30px;min-height:30px;} button:disabled{opacity:.35;} button.suggested-action{background:#2874db;} label{color:#edf2fa;} scale{min-width:180px;}')
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);root.set_border_width(12);window.add(root)
    top=Gtk.Box(spacing=8);root.pack_start(top,False,False,0)
    title=Gtk.Label(label='');top.pack_start(title,False,False,4)
    fps=Gtk.Label(label='');top.pack_end(fps,False,False,4)
    image=PreviewGL();root.pack_start(image,True,True,0)
    status=Gtk.Label(label='正在启动…');status.set_line_wrap(True);root.pack_start(status,False,False,0)
    bar=Gtk.Box(spacing=12);bar.set_halign(Gtk.Align.CENTER);root.pack_start(bar,False,False,0)
    paths={
      'photo':'<path d="M6 9h5l2-3h6l2 3h5v17H6z"/><circle cx="16" cy="17" r="5"/>',
      'record':'<circle cx="16" cy="16" r="9" fill="#fb5d61" stroke="none"/>',
      'stop':'<rect x="8" y="8" width="16" height="16" rx="2" fill="#fb5d61" stroke="none"/>',
      'switch':'<path d="M5 11a12 12 0 0 1 21-3M26 3v7h-7M27 21A12 12 0 0 1 6 24M6 29v-7h7"/><circle cx="16" cy="16" r="4"/>',
      'focus':'<path d="M10 5H5v5m17-5h5v5M5 22v5h5m12 0h5v-5"/><circle cx="16" cy="16" r="5"/>',
      'manual':'<path d="M5 7h22M5 16h22M5 25h22"/><circle cx="11" cy="7" r="3" fill="#101318"/><circle cx="22" cy="16" r="3" fill="#101318"/><circle cx="14" cy="25" r="3" fill="#101318"/>',
      'zoom':'<circle cx="13" cy="13" r="8"/><path d="m19 19 9 9M9 13h8m-4-4v8"/>',
      'rotate':'<path d="M25 12a10 10 0 1 0 0 10M25 5v8h-8"/>',
      'quality':'<rect x="4" y="6" width="24" height="20" rx="4"/><path d="M9 11v10m6-10v10M9 16h6m5-5v10m0-10c7 0 7 10 0 10"/>',
      'flash':'<path d="m18 3-12 16h9l-1 10 12-17h-9z"/>',
      'off':'<path d="m18 3-12 16h9l-1 10 12-17h-9zM4 4l24 24"/>',
      'auto':'<path d="m13 3-9 15h8l-1 10 11-17h-9zM20 28l4-10 4 10m-6-4h4"/>',
      'torch':'<path d="M10 5h12l-3 11v13h-6V16zM4 5 1 2m27 3 3-3M16 1V0"/>',
    }
    def icon(name):
        svg=('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><g fill="none" stroke="#edf2fa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'+paths[name]+'</g></svg>').encode()
        loader=GdkPixbuf.PixbufLoader.new_with_type('svg');loader.write(svg);loader.close()
        return Gtk.Image.new_from_pixbuf(loader.get_pixbuf())
    def button(name,label,callback):
        b=Gtk.Button();b.set_image(icon(name));b.set_tooltip_text(label);b.get_accessible().set_name(label)
        b.connect('clicked',callback);bar.pack_start(b,False,False,0);return b
    state={'capture':None,'side':side,'mode':'fast','flash':'off','rotation':0,'zoom':1.,'mtime':None,
           'busy':False,'closed':False,'recorder':None,'samples':{'front':[],'rear':[]},'failures':[],
           'results':[],'lastdraw':None,'lastfps':0.,'started':time.monotonic(),'actions':[], 'switches':[],
           'display_runs':[], 'active_display':None}
    def message(text):status.set_text(text)
    def update():
        c=state['capture'];which='后置' if state['side']=='rear' else '前置'
        title.set_text(f'{which} · {c.width}×{c.height}' if c else which)
        rear=state['side']=='rear';available=rear and flash_control.available()
        recording=state['recorder'] is not None
        for b in [switch,focus,manual,quality,rotate,zoom_button]:b.set_sensitive(not state['busy'] and not recording)
        focus.set_sensitive(rear and not state['busy'] and not recording)
        manual.set_sensitive(rear and not state['busy'] and not recording)
        flash.set_sensitive(available and not state['busy'])
        photo.set_sensitive(not state['busy'] and not recording)
        record.set_sensitive(not state['busy'])
        record.set_image(icon('stop' if recording else 'record'))
        flash.set_image(icon(state['flash'] if state['flash']!='photo' else 'flash'))
        flash.set_tooltip_text('闪光模式：'+{'off':'关闭','auto':'自动','photo':'强制闪光','torch':'补光常亮'}[state['flash']]+'；点击切换')
        quality.set_tooltip_text('当前：'+('高速高清' if state['mode']=='fast' else '最高分辨率')+'；点击切换')
    def run_work(work,done=None):
        if state['busy']:return
        state['busy']=True;update()
        def worker():
            result,error=None,None
            try:result=work()
            except BaseException as e:error=str(e)
            def finish():
                state['busy']=False
                if error:state['failures'].append(error);message(error)
                elif done:done(result)
                update();return False
            GLib.idle_add(finish)
        threading.Thread(target=worker,daemon=True).start()
    def begin(side,mode):
        before=time.monotonic();old=state['capture'];position=None
        flash_control.off()
        if old:
            if old.side=='rear':position=old.lens_position()
            old.stop();state['results'].append(old.result())
        state['capture']=None
        new=Capture(side,mode,lease).start()
        try:
            if side=='rear' and position is not None:new.lens_position(position)
            new.wait_frame(timeout=10)
        except BaseException:
            # A failed mode transition must release its pipeline before the
            # previous preview is restored, or the second manager sees BUSY.
            new.stop()
            raise
        state.update(capture=new,side=side,mode=mode,mtime=None,started=time.monotonic(),lastdraw=None)
        state['active_display']={'camera':side,'mode':mode,'times':[]}
        state['display_runs'].append(state['active_display'])
        state['switches'].append({'camera':side,'mode':mode,'seconds_to_first_frame':time.monotonic()-before})
        if side=='rear' and state['flash']=='torch':flash_control.torch(True)
        elif side=='front':state['flash']='off'
        return new
    def switch_camera(*_):
        if state['recorder'] or state['busy']:return
        message('切换摄像头…')
        run_work(lambda:begin('front' if state['side']=='rear' else 'rear',state['mode']),lambda _:message('已切换'))
    def mode_change(*_):
        if state['recorder'] or state['busy']:return
        run_work(lambda:begin(state['side'],'full' if state['mode']=='fast' else 'fast'),lambda _:message('模式已切换；拍照始终使用最高分辨率'))
    def destination(suffix):
        directory=Path.home()/('Videos/Pad Camera' if suffix=='.mp4' else 'Pictures/Pad Camera')
        return directory/(time.strftime('%Y%m%d-%H%M%S')+f'-{time.time_ns()%1000000000:09d}-{state["side"]}'+suffix)
    def take_photo(*_,path=None):
        if state['busy'] or state['recorder']:return
        path=path or destination('.jpg');message('正在拍摄最高分辨率照片…')
        mode=state['mode'];side=state['side'];rotation=state['rotation'];zoom=state['zoom'];flashmode=state['flash']
        def work():
            try:
                c=begin(side,'full') if mode!='full' else state['capture']
                end=time.monotonic()+2.0
                while time.monotonic()<end:raw,_=c.wait_frame(c.mtime,timeout=10)
                luma=ImageStat.Stat(Image.open(io.BytesIO(raw)).convert('L').resize((64,48))).mean[0]
                if side=='rear' and (flashmode=='photo' or (flashmode=='auto' and luma<85)):
                    raw=flash_control.photo(c)
                return save_still(raw,path,rotation,zoom)
            finally:
                flash_control.off()
                if mode!='full':begin(side,mode)
                if state['flash']=='torch' and side=='rear':flash_control.torch(True)
        run_work(work,lambda path:message('已保存：'+path))
    def autofocus(*_):
        if state['side']!='rear' or state['busy']:return
        message('自动对焦中…')
        run_work(lambda:state['capture'].autofocus(),lambda r:message('对焦完成' if r['focused'] else r['reason']))
    def popup(button,box):
        pop=Gtk.Popover.new(button);pop.add(box);pop.show_all();return pop
    def manual_focus(*_):
        if state['side']!='rear':return
        c=state['capture'];cal=json.loads(Path('/var/lib/nabu-camera/focus.json').read_text());low,high=cal['native_dac_bounds']
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);box.set_border_width(16)
        box.pack_start(Gtk.Label(label='手动对焦 · 远 ← → 近'),False,False,0)
        scale=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,low,high,1);scale.set_value(c.lens_position())
        scale.get_accessible().set_name('手动对焦位置');box.pack_start(scale,False,False,0)
        def changed(s):
            try:c.lens_position(round(s.get_value()))
            except Exception as e:message(str(e))
        scale.connect('value-changed',changed);popup(manual,box)
    def zoom_panel(*_):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);box.set_border_width(16)
        label=Gtk.Label(label='数字变焦（裁切）');box.pack_start(label,False,False,0)
        row=Gtk.Box(spacing=8);box.pack_start(row,False,False,0)
        scale=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,1,4,.1);scale.set_digits(1);scale.set_value(state['zoom'])
        scale.get_accessible().set_name('数字变焦倍数');box.pack_start(scale,False,False,0)
        for factor in [1.,2.,4.]:
            b=Gtk.Button(label=f'{factor:g}×');b.connect('clicked',lambda _,v=factor:scale.set_value(v));row.pack_start(b,True,True,0)
        def changed(s):state['zoom']=s.get_value();image.zoom=state['zoom'];image.queue_render()
        scale.connect('value-changed',changed);popup(zoom_button,box)
    def rotate_image(*_):state['rotation']=(state['rotation']+90)%360;state['mtime']=None
    def flash_cycle(*_):
        modes=['off','auto','photo','torch'];state['flash']=modes[(modes.index(state['flash'])+1)%4]
        flash_control.torch(state['flash']=='torch');update();message(flash.get_tooltip_text())
    def video(*_,path=None):
        if state['busy']:return
        if state['recorder']:
            recorder=state['recorder'];state['recorder']=None
            run_work(recorder.stop,lambda r:(state['actions'].append({'recording':r}),message('已保存：'+r['file'])))
        else:
            try:
                if state['capture'] is None:raise RuntimeError('相机尚未就绪')
                state['recorder']=Recorder(state['capture'],path or destination('.mp4'),state['rotation'],state['zoom'])
                message('录像中 · 可切换补光；停止后保存 MP4')
                update()
            except Exception as e:state['failures'].append(str(e));message(str(e))
    switch=button('switch','切换前后摄像头',switch_camera)
    flash=button('off','闪光模式',flash_cycle)
    quality=button('quality','分辨率与帧率',mode_change)
    focus=button('focus','自动对焦',autofocus)
    manual=button('manual','手动对焦',manual_focus)
    photo=button('photo','拍照',take_photo);photo.get_style_context().add_class('suggested-action')
    record=button('record','录像或停止',video)
    zoom_button=button('zoom','放大缩小',zoom_panel)
    rotate=button('rotate','旋转画面',rotate_image)
    def tick(*_):
        if state['closed']:return False
        c=state['capture']
        if state['busy'] or not c:return True
        try:
            if image.error or image.get_error():raise RuntimeError(image.error or str(image.get_error()))
            frame=c.snapshot(state['mtime'])
            if frame:
                data,state['mtime']=frame;header=f'P6\n{c.width} {c.height}\n255\n'.encode()
                image.submit(data[len(header):],c.width,c.height,state['mtime'],state['rotation'])
            elif time.monotonic()-c.received_at>3:raise RuntimeError('相机超过 3 秒没有新画面')
            if state['recorder'] and state['recorder'].error:raise RuntimeError(state['recorder'].error)
        except Exception as e:
            message(str(e));state['failures'].append(str(e));c.stop();state['capture']=None
        return True
    def painted(*_):
        if state['busy'] or not state['capture']:return
        if image.rendered_serial is not None and image.rendered_serial!=state['lastdraw']:
            now=time.monotonic();state['lastdraw']=image.rendered_serial
            samples=state['samples'][state['side']]
            if now-state['started']>2:
                samples.append(now)
                state['active_display']['times'].append(now)
            if now-state['lastfps']>1:
                recent=[t for t in samples if now-t<2]
                fps.set_text(f'{(len(recent)-1)/(recent[-1]-recent[0]):.0f} fps · {state["zoom"]:.1f}×' if len(recent)>1 else '')
                state['lastfps']=now
    def close(*_):
        if state['busy']:message('正在完成拍摄，请稍候…');return True
        if state['recorder']:
            video();return True
        state['closed']=True;flash_control.off()
        if state['capture']:state['capture'].stop();state['results'].append(state['capture'].result())
        lease.close();Gtk.main_quit();return False
    window.connect('delete-event',close);window.show_all();window.maximize()
    image.add_tick_callback(tick);window.get_frame_clock().connect('after-paint',painted)
    run_work(lambda:begin(side,'fast'),lambda _:message('拍照保存全像素；HD 图标切换预览模式'))
    if smoke:
        smoke.mkdir(parents=True,exist_ok=False)
        # A single complete scenario exercises the real callbacks and files.
        tasks=deque([
            lambda:take_photo(path=smoke/'rear-full.jpg'),
            lambda:video(path=smoke/'rear-video.mp4'),
            lambda:video(),
            lambda:flash_cycle(),lambda:flash_cycle(),lambda:take_photo(path=smoke/'rear-flash.jpg'),
            lambda:flash_cycle(),lambda:flash_cycle(),
            lambda:state['capture'].lens_position(350),
            lambda:(state.update(zoom=2.),setattr(image,'zoom',2.)),
            lambda:state['capture'].lens_position(265),
            lambda:(state.update(zoom=1.),setattr(image,'zoom',1.)),
            switch_camera,
            lambda:take_photo(path=smoke/'front-full.jpg'),
            lambda:video(path=smoke/'front-video.mp4'),lambda:video(),
            lambda:image.capture_next_frame(smoke/'front-preview.png'),
            switch_camera,lambda:image.capture_next_frame(smoke/'rear-preview.png'),close])
        if side=='front':
            tasks=deque([lambda:take_photo(path=smoke/'front-full.jpg'),
                         lambda:video(path=smoke/'front-video.mp4'),lambda:video(),
                         lambda:image.capture_next_frame(smoke/'front-preview.png'),close])
        next_action=[time.monotonic()+smoke_seconds]
        def step():
            if state['closed']:return False
            if state['busy'] or time.monotonic()<next_action[0]:return True
            if not tasks:return False
            try:tasks.popleft()()
            except Exception as e:state['failures'].append(str(e));message(str(e))
            next_action[0]=time.monotonic()+(6 if state['recorder'] else 2)
            return True
        GLib.timeout_add(250,step)
    Gtk.main()
    if smoke:
        performance=[]
        for run in state['display_runs']:
            times=run.pop('times')
            if len(times)>2:
                performance.append(dict(run,frames=len(times),fps=(len(times)-1)/(times[-1]-times[0]),
                                        max_interval_ms=max(b-a for a,b in zip(times,times[1:]))*1000))
        result={'failures':state['failures'],'renderer':image.renderer,'captures':state['results'],
                'switches':state['switches'],'actions':state['actions'],
                'display_performance':performance,
                'display_samples':{k:len(v) for k,v in state['samples'].items()},
                'files':sorted(p.name for p in smoke.iterdir()),'passed':not state['failures']}
        (smoke/'gui-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        return 0 if result['passed'] else 1
    return 0
