// SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
// Copyright (c) 2026 zouhong <admin@proyy.com>
// Noncommercial use only; see LICENSE. Third-party components retain their licenses.
// Verify padded RAW10 low bits, Bayer phase and full-width mapping on the real GPU.
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <fstream>
#include <sstream>
#include <vector>
#include <cmath>
#include <iostream>
#include <stdexcept>
std::string read(const char *p){std::ifstream f(p);return {std::istreambuf_iterator<char>(f),{}};}
void check(bool v,const char *s){if(!v)throw std::runtime_error(s);}
GLuint shader(GLenum type,std::string code){GLuint s=glCreateShader(type);const char *p=code.c_str();glShaderSource(s,1,&p,nullptr);glCompileShader(s);GLint ok;glGetShaderiv(s,GL_COMPILE_STATUS,&ok);if(!ok){char log[8192];glGetShaderInfoLog(s,sizeof(log),nullptr,log);throw std::runtime_error(log);}return s;}
int main(int argc,char **argv){try{
check(argc==3,"shader paths required");EGLDisplay d=eglGetDisplay(EGL_DEFAULT_DISPLAY);check(eglInitialize(d,nullptr,nullptr),"egl initialize");
EGLint attrs[]={EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_NONE};EGLConfig config;EGLint n;check(eglChooseConfig(d,attrs,&config,1,&n)&&n,"egl config");
EGLint contextattrs[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};EGLContext c=eglCreateContext(d,config,EGL_NO_CONTEXT,contextattrs);EGLint pbuffer[]={EGL_WIDTH,32,EGL_HEIGHT,16,EGL_NONE};EGLSurface s=eglCreatePbufferSurface(d,config,pbuffer);check(eglMakeCurrent(d,s,s,c),"make current");
GLuint program=glCreateProgram();glAttachShader(program,shader(GL_VERTEX_SHADER,"#version 100\n"+read(argv[1])));glAttachShader(program,shader(GL_FRAGMENT_SHADER,"#version 100\n#define RAW10P\n"+read(argv[2])));glLinkProgram(program);GLint linked;glGetProgramiv(program,GL_LINK_STATUS,&linked);check(linked,"link program");glUseProgram(program);
const int w=32,h=16,stride=64;std::vector<unsigned char> packed(stride*h,231);std::vector<int> raw(w*h);
for(int y=0;y<h;y++)for(int x=0;x<w;x++){int color=(y%2==0?(x%2==0?2:1):(x%2==0?1:0));raw[y*w+x]=color==0?100+18*x+3*y:color==1?240+3*x+9*y:500+7*x+2*y;}
for(int y=0;y<h;y++)for(int x=0;x<w;x+=4){int offset=y*stride+x/4*5;packed[offset+4]=0;for(int i=0;i<4;i++){int v=raw[y*w+x+i];packed[offset+i]=v>>2;packed[offset+4]|=(v&3)<<(i*2);}}
GLuint textures[2];glGenTextures(2,textures);glActiveTexture(GL_TEXTURE0);glBindTexture(GL_TEXTURE_2D,textures[0]);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);glTexImage2D(GL_TEXTURE_2D,0,GL_LUMINANCE,stride,h,0,GL_LUMINANCE,GL_UNSIGNED_BYTE,packed.data());
glActiveTexture(GL_TEXTURE1);glBindTexture(GL_TEXTURE_2D,textures[1]);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);GLuint fbo;glGenFramebuffers(1,&fbo);glBindFramebuffer(GL_FRAMEBUFFER,fbo);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,textures[1],0);check(glCheckFramebufferStatus(GL_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE,"framebuffer");
float verts[]={-1,-1,-1,1,1,1,1,-1},uv[]={0,0,0,1,1,1,1,0};auto a=glGetAttribLocation(program,"vertexIn");glEnableVertexAttribArray(a);glVertexAttribPointer(a,2,GL_FLOAT,GL_FALSE,0,verts);a=glGetAttribLocation(program,"textureIn");glEnableVertexAttribArray(a);glVertexAttribPointer(a,2,GL_FLOAT,GL_FALSE,0,uv);
auto loc=[&](const char *name){return glGetUniformLocation(program,name);};float m4[]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1},m3[]={1,0,0,0,1,0,0,0,1};glUniformMatrix4fv(loc("proj_matrix"),1,GL_FALSE,m4);glUniformMatrix3fv(loc("ccm"),1,GL_FALSE,m3);glUniform1i(loc("tex_y"),0);glUniform1f(loc("stride_factor"),1);glUniform2f(loc("tex_step"),1.f/(stride-1),1.f/(h-1));glUniform2f(loc("tex_size"),w,h);glUniform2f(loc("tex_bayer_first_red"),1,1);glUniform3f(loc("awb"),1,1,1);glUniform3f(loc("blacklevel"),0,0,0);glUniform1f(loc("gamma"),1);glUniform1f(loc("contrastExp"),1);glViewport(0,0,w,h);glDrawArrays(GL_TRIANGLE_FAN,0,4);glFinish();std::vector<unsigned char> pixels(w*h*4);glReadPixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());check(glGetError()==GL_NO_ERROR,"GL readback");
double maxError=0;for(int y=2;y<h-2;y++)for(int x=2;x<w-2;x++){double C=raw[y*w+x],V=(raw[(y-1)*w+x]+raw[(y+1)*w+x])*.5,H=(raw[y*w+x-1]+raw[y*w+x+1])*.5,D=(raw[(y-1)*w+x-1]+raw[(y-1)*w+x+1]+raw[(y+1)*w+x-1]+raw[(y+1)*w+x+1])*.25;double expected[3];bool redx=(x%2)==1,redy=(y%2)==1;if(redx&&redy){expected[0]=C;expected[1]=(V+H)*.5;expected[2]=D;}else if(redx){expected[0]=V;expected[1]=C;expected[2]=H;}else if(redy){expected[0]=H;expected[1]=C;expected[2]=V;}else{expected[0]=D;expected[1]=(V+H)*.5;expected[2]=C;}for(int k=0;k<3;k++)maxError=std::max(maxError,std::abs(pixels[(y*w+x)*4+k]-expected[k]*255/1023.));}
std::cout<<"{\"renderer\":\""<<glGetString(GL_RENDERER)<<"\",\"max_channel_error_8bit\":"<<maxError<<",\"passed\":"<<(maxError<=1?"true":"false")<<"}"<<std::endl;return maxError<=1?0:1;
}catch(std::exception &e){std::cerr<<e.what()<<std::endl;return 1;}}
