# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""GTK GLES preview: one texture upload, GPU rotation/scaling, no image decoder."""
import ctypes as C
import math
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


class PreviewGL(Gtk.GLArea):
    def __init__(self):
        super().__init__()
        self.set_use_es(True)
        self.set_required_version(3, 0)
        self.set_auto_render(False)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.gl = C.CDLL('libGLESv2.so.2')
        self._functions()
        self.program = self.texture = self.vao = 0
        self.texture_size = None
        self.frame = None
        self.pending = False
        self.rotation = 0
        self.zoom = 1.0
        self.error = None
        self.renderer = None
        self.rendered_serial = None
        self.screenshot_path = None
        self.connect('render', self._render)
        self.connect('unrealize', self._cleanup)

    def _functions(self):
        specs = {
            'glCreateShader':(C.c_uint,[C.c_uint]),
            'glShaderSource':(None,[C.c_uint,C.c_int,C.POINTER(C.c_char_p),C.POINTER(C.c_int)]),
            'glCompileShader':(None,[C.c_uint]),
            'glGetShaderiv':(None,[C.c_uint,C.c_uint,C.POINTER(C.c_int)]),
            'glGetShaderInfoLog':(None,[C.c_uint,C.c_int,C.POINTER(C.c_int),C.c_void_p]),
            'glDeleteShader':(None,[C.c_uint]),
            'glCreateProgram':(C.c_uint,[]),
            'glAttachShader':(None,[C.c_uint,C.c_uint]),
            'glLinkProgram':(None,[C.c_uint]),
            'glGetProgramiv':(None,[C.c_uint,C.c_uint,C.POINTER(C.c_int)]),
            'glGetProgramInfoLog':(None,[C.c_uint,C.c_int,C.POINTER(C.c_int),C.c_void_p]),
            'glUseProgram':(None,[C.c_uint]),
            'glGetUniformLocation':(C.c_int,[C.c_uint,C.c_char_p]),
            'glUniform1i':(None,[C.c_int,C.c_int]),
            'glUniform1f':(None,[C.c_int,C.c_float]),
            'glUniform2f':(None,[C.c_int,C.c_float,C.c_float]),
            'glGenTextures':(None,[C.c_int,C.POINTER(C.c_uint)]),
            'glBindTexture':(None,[C.c_uint,C.c_uint]),
            'glActiveTexture':(None,[C.c_uint]),
            'glTexParameteri':(None,[C.c_uint,C.c_uint,C.c_int]),
            'glTexImage2D':(None,[C.c_uint,C.c_int,C.c_int,C.c_int,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_void_p]),
            'glTexSubImage2D':(None,[C.c_uint,C.c_int,C.c_int,C.c_int,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_char_p]),
            'glPixelStorei':(None,[C.c_uint,C.c_int]),
            'glGenVertexArrays':(None,[C.c_int,C.POINTER(C.c_uint)]),
            'glBindVertexArray':(None,[C.c_uint]),
            'glViewport':(None,[C.c_int,C.c_int,C.c_int,C.c_int]),
            'glClearColor':(None,[C.c_float]*4),
            'glClear':(None,[C.c_uint]),
            'glDrawArrays':(None,[C.c_uint,C.c_int,C.c_int]),
            'glDeleteTextures':(None,[C.c_int,C.POINTER(C.c_uint)]),
            'glDeleteVertexArrays':(None,[C.c_int,C.POINTER(C.c_uint)]),
            'glDeleteProgram':(None,[C.c_uint]),
            'glGetString':(C.c_char_p,[C.c_uint]),
            'glGetError':(C.c_uint,[]),
            'glReadPixels':(None,[C.c_int,C.c_int,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_void_p]),
        }
        for name, (result,args) in specs.items():
            function = getattr(self.gl,name)
            function.restype, function.argtypes = result,args

    def _shader(self, kind, source):
        gl = self.gl
        shader = gl.glCreateShader(kind)
        code = C.c_char_p(source.encode())
        gl.glShaderSource(shader,1,C.byref(code),None)
        gl.glCompileShader(shader)
        status=C.c_int()
        gl.glGetShaderiv(shader,0x8B81,C.byref(status))
        if not status.value:
            log=C.create_string_buffer(4096)
            gl.glGetShaderInfoLog(shader,4096,None,log)
            gl.glDeleteShader(shader)
            raise RuntimeError(log.value.decode(errors='replace'))
        return shader

    def _initialize(self):
        gl=self.gl
        self.renderer=gl.glGetString(0x1F01).decode()
        vertex=self._shader(0x8B31, '''#version 300 es
out vec2 uv;
const vec2 points[4]=vec2[4](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(1,1));
void main(){vec2 p=points[gl_VertexID];gl_Position=vec4(p,0,1);uv=vec2((p.x+1.)*.5,(1.-p.y)*.5);}
''')
        fragment=self._shader(0x8B30, '''#version 300 es
precision mediump float;
in vec2 uv;
uniform sampler2D frame;
uniform vec2 rotation;
uniform float zoom;
out vec4 color;
void main(){vec2 p=(uv-.5)/zoom;vec2 q=vec2(rotation.x*p.x-rotation.y*p.y,rotation.y*p.x+rotation.x*p.y)+.5;color=vec4(texture(frame,q).rgb,1.);}
''')
        self.program=gl.glCreateProgram()
        gl.glAttachShader(self.program,vertex)
        gl.glAttachShader(self.program,fragment)
        gl.glLinkProgram(self.program)
        gl.glDeleteShader(vertex)
        gl.glDeleteShader(fragment)
        status=C.c_int()
        gl.glGetProgramiv(self.program,0x8B82,C.byref(status))
        if not status.value:
            log=C.create_string_buffer(4096)
            gl.glGetProgramInfoLog(self.program,4096,None,log)
            raise RuntimeError(log.value.decode(errors='replace'))
        texture,vao=C.c_uint(),C.c_uint()
        gl.glGenTextures(1,C.byref(texture))
        gl.glGenVertexArrays(1,C.byref(vao))
        self.texture,self.vao=texture.value,vao.value
        gl.glBindTexture(0x0DE1,self.texture)
        for pname,value in [(0x2801,0x2601),(0x2800,0x2601),(0x2802,0x812F),(0x2803,0x812F)]:
            gl.glTexParameteri(0x0DE1,pname,value)
        self.rotation_location=gl.glGetUniformLocation(self.program,b'rotation')
        self.zoom_location=gl.glGetUniformLocation(self.program,b'zoom')
        gl.glUseProgram(self.program)
        gl.glUniform1i(gl.glGetUniformLocation(self.program,b'frame'),0)

    def submit(self, rgb, width, height, serial, rotation):
        if len(rgb)!=width*height*3:
            raise ValueError('Invalid complete RGB frame')
        self.frame=(rgb,width,height,serial)
        self.rotation=rotation
        self.pending=True
        self.queue_render()

    def capture_next_frame(self, path):
        """Read our own rendered framebuffer; Wayland window screenshots are blank."""
        self.screenshot_path = path
        self.queue_render()

    def _render(self, area, context):
        try:
            if self.get_error():
                raise RuntimeError(str(self.get_error()))
            if not self.program:
                self._initialize()
            gl=self.gl
            gl.glClearColor(.04,.04,.04,1.)
            gl.glClear(0x4000)
            if self.frame is None:
                return True
            rgb,width,height,serial=self.frame
            gl.glUseProgram(self.program)
            gl.glBindVertexArray(self.vao)
            gl.glActiveTexture(0x84C0)
            gl.glBindTexture(0x0DE1,self.texture)
            gl.glPixelStorei(0x0CF5,1)
            if self.texture_size!=(width,height):
                gl.glTexImage2D(0x0DE1,0,0x1907,width,height,0,0x1907,0x1401,None)
                self.texture_size=(width,height)
            if self.pending:
                gl.glTexSubImage2D(0x0DE1,0,0,0,width,height,0x1907,0x1401,rgb)
                self.pending=False
            angle=math.radians(self.rotation)
            gl.glUniform2f(self.rotation_location,math.cos(angle),math.sin(angle))
            gl.glUniform1f(self.zoom_location,self.zoom)
            frame_width,frame_height=(height,width) if self.rotation%180 else (width,height)
            scale=self.get_scale_factor()
            aw,ah=self.get_allocated_width()*scale,self.get_allocated_height()*scale
            ratio=min(aw/frame_width,ah/frame_height)
            vw,vh=max(1,int(frame_width*ratio)),max(1,int(frame_height*ratio))
            gl.glViewport((aw-vw)//2,(ah-vh)//2,vw,vh)
            gl.glDrawArrays(0x0005,0,4)
            if self.screenshot_path:
                from PIL import Image
                pixels = C.create_string_buffer(aw*ah*4)
                gl.glReadPixels(0,0,aw,ah,0x1908,0x1401,pixels)
                Image.frombytes('RGBA',(aw,ah),pixels.raw).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).save(self.screenshot_path)
                self.screenshot_path = None
            error=gl.glGetError()
            if error:
                raise RuntimeError(f'GLES preview error 0x{error:x}')
            self.rendered_serial=serial
            return True
        except Exception as error:
            self.error=str(error)
            return False

    def _cleanup(self,*_):
        self.make_current()
        if self.get_error():
            return
        if self.texture:
            self.gl.glDeleteTextures(1,C.byref(C.c_uint(self.texture)))
        if self.vao:
            self.gl.glDeleteVertexArrays(1,C.byref(C.c_uint(self.vao)))
        if self.program:
            self.gl.glDeleteProgram(self.program)
        self.program=self.texture=self.vao=0
