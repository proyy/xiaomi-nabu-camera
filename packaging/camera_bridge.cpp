// SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
// Copyright (c) 2026 zouhong <admin@proyy.com>
// Noncommercial use only; see LICENSE. Third-party components retain their licenses.
// On-demand libcamera -> standard V4L2 front/rear devices. No sensor streams at idle.
#include <libcamera/libcamera.h>
#include <libyuv.h>
#include <linux/videodev2.h>
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

using namespace libcamera;
static volatile sig_atomic_t quit = 0;
static void signal_stop(int) { quit = 1; }
static constexpr uint32_t UsageEvent = V4L2_EVENT_PRIVATE_START + 0x08e00001;
static void require(bool ok, const std::string &message) {
    if (!ok) throw std::runtime_error(message + ": " + strerror(errno));
}
static int xioctl(int fd, unsigned long op, void *data) {
    int result;
    do { result = ioctl(fd, op, data); } while (result < 0 && errno == EINTR && !quit);
    return result;
}
static void notify_ready() {
    const char *path=getenv("NOTIFY_SOCKET");
    if (!path) return;
    sockaddr_un address{}; address.sun_family=AF_UNIX;
    size_t size=strlen(path);
    require(size<sizeof(address.sun_path),"systemd socket path too long");
    memcpy(address.sun_path,path,size+1);
    if (path[0]=='@') address.sun_path[0]='\0';
    int socketFd=socket(AF_UNIX,SOCK_DGRAM|SOCK_CLOEXEC,0);
    require(socketFd>=0,"open systemd notification socket");
    ssize_t result=sendto(socketFd,"READY=1",7,MSG_NOSIGNAL,
        reinterpret_cast<sockaddr *>(&address),offsetof(sockaddr_un,sun_path)+size+(path[0]=='@'?0:1));
    close(socketFd); require(result==7,"notify camera readiness");
}
static void set_control(int fd, uint32_t id, int value) {
    v4l2_control control{}; control.id=id; control.value=value;
    require(xioctl(fd,VIDIOC_S_CTRL,&control)==0,"set virtual camera control");
}

struct CameraOutput {
    std::string side, sensorId;
    int width, height, nativeWidth, nativeHeight, fps, fd=-1;
    bool wanted=false, acquired=false;
    std::atomic<bool> running{false};
    std::atomic<uint64_t> frames{0}, errors{0};
    std::atomic<int64_t> lastDelivery{0};
    uint64_t missing=0, nonmonotonic=0, firstStamp=0, lastStamp=0;
    uint64_t sensorFrames=0,lastPublishStamp=0;
    uint32_t lastSequence=0;
    std::shared_ptr<Camera> camera;
    std::unique_ptr<CameraConfiguration> config;
    std::unique_ptr<FrameBufferAllocator> allocator;
    std::vector<std::unique_ptr<Request>> requests;
    struct Mapping { void *base; size_t length; uint8_t *pixels; int fd; };
    std::map<FrameBuffer *,Mapping> mappings;
    std::vector<uint8_t> yuv;
    std::vector<uint8_t> scaled;

    CameraOutput(CameraManager &manager, std::string name, int bus, int number,
                 int w, int h, int rate):side(name),width(w),
                 height(h),nativeWidth(w),nativeHeight(h),fps(rate) {
        sensorId="/base/soc@0/cci@ac4a000/i2c-bus@"+std::to_string(bus)+"/camera@10";
        attach(manager);
        fd=open(("/dev/video"+std::to_string(number)).c_str(),O_RDWR|O_NONBLOCK|O_CLOEXEC);
        require(fd>=0,"open "+side+" V4L2 device");
        v4l2_capability caps{};
        require(!xioctl(fd,VIDIOC_QUERYCAP,&caps) &&
                std::string(reinterpret_cast<char *>(caps.driver))=="v4l2 loopback",
                "refusing a non-loopback output");
        set_control(fd,V4L2_CID_USER_BASE|0xf000,0);
        v4l2_format format{}; format.type=V4L2_BUF_TYPE_VIDEO_OUTPUT;
        format.fmt.pix.width=width; format.fmt.pix.height=height;
        format.fmt.pix.pixelformat=V4L2_PIX_FMT_YUYV;
        format.fmt.pix.field=V4L2_FIELD_NONE;
        format.fmt.pix.colorspace=V4L2_COLORSPACE_SMPTE170M;
        format.fmt.pix.ycbcr_enc=V4L2_YCBCR_ENC_601;
        format.fmt.pix.quantization=V4L2_QUANTIZATION_LIM_RANGE;
        require(!xioctl(fd,VIDIOC_S_FMT,&format),"set "+side+" output format");
        require(format.fmt.pix.width==unsigned(width) && format.fmt.pix.height==unsigned(height)
                && format.fmt.pix.pixelformat==V4L2_PIX_FMT_YUYV,"output format was changed");
        v4l2_streamparm parm{}; parm.type=V4L2_BUF_TYPE_VIDEO_OUTPUT;
        parm.parm.output.timeperframe.numerator=1;
        parm.parm.output.timeperframe.denominator=rate;
        require(!xioctl(fd,VIDIOC_S_PARM,&parm),"set frame rate");
        yuv.resize(width*height*2); scaled.resize(width*height*4);
        blank(); // Claim OUTPUT, advertise CAPTURE to normal applications.
        set_control(fd,V4L2_CID_USER_BASE|0xf000,1); // v4l2loopback keep_format
        v4l2_event_subscription sub{}; sub.type=UsageEvent;
        sub.flags=V4L2_EVENT_SUB_FL_SEND_INITIAL;
        require(!xioctl(fd,VIDIOC_SUBSCRIBE_EVENT,&sub),"subscribe camera usage");
    }
    void attach(CameraManager &manager) {
        camera=manager.get(sensorId);
        require(bool(camera),side+" sensor not found");
        camera->requestCompleted.connect(this,&CameraOutput::completed);
    }
    void detach() {
        release();
        if (camera) camera->requestCompleted.disconnect(this);
        camera.reset();
    }
    ~CameraOutput() {
        detach();
        if (fd>=0) close(fd);
    }
    void blank() {
        for (size_t i=0;i<yuv.size();i+=2) { yuv[i]=16; yuv[i+1]=128; }
        require(write(fd,yuv.data(),yuv.size())==ssize_t(yuv.size()),"publish idle frame");
    }
    void prepare() {
        if (acquired) return;
        require(camera->acquire()==0,"acquire "+side); acquired=true;
        try {
            config=camera->generateConfiguration({StreamRole::Viewfinder});
            require(bool(config),"generate camera configuration");
            auto &stream=config->at(0);
            stream.size=Size(nativeWidth,nativeHeight); stream.pixelFormat=formats::XRGB8888;
            stream.bufferCount=4;
            require(config->validate()!=CameraConfiguration::Invalid &&
                    stream.size==Size(nativeWidth,nativeHeight) && stream.pixelFormat==formats::XRGB8888,
                    "exact camera mode unavailable");
            require(camera->configure(config.get())==0,"configure "+side);
            allocator=std::make_unique<FrameBufferAllocator>(camera);
            require(allocator->allocate(stream.stream())>=0,"allocate camera buffers");
            for (const auto &buffer:allocator->buffers(stream.stream())) {
                require(buffer->planes().size()==1,"unexpected RGB buffer planes");
                auto p=buffer->planes()[0];
                size_t length=p.offset+p.length;
                void *base=mmap(nullptr,length,PROT_READ,MAP_SHARED,p.fd.get(),0);
                require(base!=MAP_FAILED,"map RGB camera buffer");
                mappings[buffer.get()]={base,length,static_cast<uint8_t *>(base)+p.offset,p.fd.get()};
                auto request=camera->createRequest();
                require(bool(request) && request->addBuffer(stream.stream(),buffer.get())==0,
                        "create capture request");
                requests.push_back(std::move(request));
            }
        } catch (...) { release(); throw; }
    }
    void start() {
        if (running) return;
        require(acquired,"camera pipeline was not prepared");
        try {
            require(camera->start()==0,"start "+side);
            running=true; frames=0; errors=0;
            missing=nonmonotonic=firstStamp=lastStamp=0; lastSequence=0;
            sensorFrames=lastPublishStamp=0;
            lastDelivery=std::chrono::steady_clock::now().time_since_epoch().count();
            for (auto &request:requests)
                require(camera->queueRequest(request.get())==0,"queue capture request");
            std::cout<<side<<" started"<<std::endl;
        } catch (...) { release(); throw; }
    }
    void stop() {
        if (running.exchange(false)) {
            if (side=="rear") {
                // Also clear a torch when its GUI/client crashes or is killed.
                std::ofstream("/sys/class/leds/nabu:white:flash/brightness")<<0;
                std::ofstream("/sys/class/leds/nabu:white:flash/flash_strobe")<<0;
            }
            camera->stop();
            double seconds=lastStamp>firstStamp ? (lastStamp-firstStamp)/1e9 : 0;
            std::cout<<side<<" stopped frames="<<frames<<" errors="<<errors
                     <<" missing="<<missing<<" nonmonotonic="<<nonmonotonic
                     <<" sensor_frames="<<sensorFrames
                     <<" sensor_fps="<<(seconds>0 ? (sensorFrames-1)/seconds : 0)<<std::endl;
        }
    }
    void release() {
        stop();
        requests.clear();
        for (auto &[buffer,map]:mappings) munmap(map.base,map.length);
        mappings.clear(); allocator.reset(); config.reset();
        if (acquired) { camera->release(); acquired=false; }
    }
    void completed(Request *request) {
        if (!running || request->status()==Request::RequestCancelled) return;
        lastDelivery=std::chrono::steady_clock::now().time_since_epoch().count();
        FrameBuffer *buffer=request->buffers().begin()->second;
        if (buffer->metadata().status!=FrameMetadata::FrameSuccess) { ++errors; }
        else {
            const auto &meta=buffer->metadata();
            if (lastStamp) {
                if (meta.sequence>lastSequence+1) missing+=meta.sequence-lastSequence-1;
                if (meta.sequence<=lastSequence || meta.timestamp<=lastStamp) ++nonmonotonic;
            } else firstStamp=meta.timestamp;
            lastStamp=meta.timestamp; lastSequence=meta.sequence;
            ++sensorFrames;
            // Standard webcam clients (including Cheese) accept <=30 fps.
            // Rear sensor stays at its native rate; decimate before conversion.
            if (!lastPublishStamp || meta.timestamp-lastPublishStamp>=900000000ULL/fps) {
            const Mapping &map=mappings.at(buffer);
            dma_buf_sync sync{DMA_BUF_SYNC_START|DMA_BUF_SYNC_READ};
            if (xioctl(map.fd,DMA_BUF_IOCTL_SYNC,&sync)) { ++errors; }
            else {
                int converted=libyuv::ARGBScale(map.pixels,config->at(0).stride,nativeWidth,nativeHeight,
                    scaled.data(),width*4,width,height,libyuv::kFilterBilinear);
                converted |= libyuv::ARGBToYUY2(scaled.data(),width*4,yuv.data(),width*2,width,height);
                sync.flags=DMA_BUF_SYNC_END|DMA_BUF_SYNC_READ;
                int synced=xioctl(map.fd,DMA_BUF_IOCTL_SYNC,&sync);
                if (converted || synced || write(fd,yuv.data(),yuv.size())!=ssize_t(yuv.size()))
                    ++errors;
                else { ++frames; lastPublishStamp=meta.timestamp; }
            }
            }
        }
        if (running) {
            request->reuse(Request::ReuseBuffers);
            if (camera->queueRequest(request)) ++errors;
        }
    }
    void usage() {
        v4l2_event event{};
        while (xioctl(fd,VIDIOC_DQEVENT,&event)==0)
            if (event.type==UsageEvent) {
                uint32_t users=0; memcpy(&users,event.u.data,sizeof(users));
                wanted=users>0;
            }
    }
};

// Pad Camera consumes the same V4L2 endpoint as other applications. This adapter
// keeps the existing complete-PPM/metadata interface and fast GLES preview.
static int capture_client(const char *device, const char *destination) {
    int fd=open(device,O_RDWR|O_NONBLOCK|O_CLOEXEC);
    require(fd>=0,"open capture endpoint");
    v4l2_format format{}; format.type=V4L2_BUF_TYPE_VIDEO_CAPTURE;
    require(!xioctl(fd,VIDIOC_G_FMT,&format) && format.fmt.pix.pixelformat==V4L2_PIX_FMT_YUYV,
            "camera service has not configured YUYV");
    int w=format.fmt.pix.width,h=format.fmt.pix.height;
    std::vector<uint8_t> data(format.fmt.pix.sizeimage), rgb(w*h*3), argb(w*h*4);
    uint64_t sequence=0;
    bool initial=true;
    // read() initiates V4L2 streaming, which wakes the matching real sensor.
    while (!quit) {
        ssize_t size=read(fd,data.data(),data.size());
        if (size<0 && (errno==EAGAIN || errno==EINTR)) {
            pollfd p{fd,POLLIN,0}; poll(&p,1,100); continue;
        }
        require(size==ssize_t(data.size()),"incomplete V4L2 frame");
        if (initial) { initial=false; continue; }
        require(!libyuv::YUY2ToARGB(data.data(),w*2,argb.data(),w*4,w,h) &&
                !libyuv::ARGBToRAW(argb.data(),w*4,rgb.data(),w*3,w,h),"convert frame to RGB");
        std::string pending=std::string(destination)+".pending";
        {
            std::ofstream output(pending,std::ios::binary|std::ios::trunc);
            output<<"P6\n"<<w<<" "<<h<<"\n255\n";
            output.write(reinterpret_cast<char *>(rgb.data()),rgb.size());
            output.close(); require(bool(output),"write complete preview frame");
        }
        require(!rename(pending.c_str(),destination),"publish preview frame");
        auto now=std::chrono::steady_clock::now().time_since_epoch();
        double seconds=std::chrono::duration<double>(now).count();
        std::cout<<std::fixed<<std::setprecision(6)<<seconds
                 <<" cam0-stream0 status: 0 seq: "<<sequence++<<" bytesused: "<<rgb.size()<<std::endl;
    }
    close(fd); return 0;
}

int main(int argc,char **argv) {
    signal(SIGINT,signal_stop); signal(SIGTERM,signal_stop);
    try {
        if (argc>=3 && std::string(argv[1])=="--parent-bound") {
            pid_t parent=getppid();
            // PR_SET_PDEATHSIG follows the creating thread's lifetime. GTK
            // starts capture on a worker, so monitor the whole process pidfd.
            int parentFd=syscall(SYS_pidfd_open,parent,0);
            require(parentFd>=0,"monitor camera owner");
            pid_t child=fork(); require(child>=0,"fork native camera");
            if (!child) { close(parentFd); execv(argv[2],argv+2); _exit(127); }
            int status=0; bool stopping=false;
            auto deadline=std::chrono::steady_clock::time_point::max();
            while (waitpid(child,&status,WNOHANG)==0) {
                pollfd owner{parentFd,POLLIN,0}; poll(&owner,1,50);
                if (!stopping && (quit || owner.revents)) {
                    kill(child,SIGINT); stopping=true;
                    deadline=std::chrono::steady_clock::now()+std::chrono::seconds(4);
                }
                if (stopping && std::chrono::steady_clock::now()>deadline) kill(child,SIGKILL);
            }
            close(parentFd);
            return WIFEXITED(status)?WEXITSTATUS(status):1;
        }
        if (argc==4 && std::string(argv[1])=="--capture")
            return capture_client(argv[2],argv[3]);
        require(argc==1,"usage: camera-bridge [--capture /dev/video42 FILE.ppm]");
        auto manager=std::make_unique<CameraManager>();
        require(!manager->start(),"start camera manager");
        {
            CameraOutput front(*manager,"front",1,42,1600,1200,30);
            CameraOutput rear(*manager,"rear",0,43,1920,1080,30);
            const char *socketPath="/run/nabu-camera/native.sock";
            int server=socket(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0), lease=-1;
            require(server>=0,"create native lease socket");
            sockaddr_un address{}; address.sun_family=AF_UNIX;
            strcpy(address.sun_path,socketPath); unlink(socketPath);
            require(!bind(server,reinterpret_cast<sockaddr *>(&address),sizeof(address)),"bind native lease socket");
            require(!chmod(socketPath,0660) && !listen(server,4),"listen for native camera clients");
            std::cout<<"camera bridge ready; sensors start on consumer streaming"<<std::endl;
            notify_ready();
            while (!quit) {
                pollfd descriptors[]={{front.fd,POLLPRI,0},{rear.fd,POLLPRI,0},
                                      {server,POLLIN,0},{lease,POLLIN|POLLHUP,0}};
                int result=poll(descriptors,4,100);
                if (result<0 && errno!=EINTR) throw std::runtime_error("poll failed");
                if (descriptors[0].revents&POLLPRI) front.usage();
                if (descriptors[1].revents&POLLPRI) rear.usage();
                if (lease>=0 && descriptors[3].revents) {
                    char data[16];
                    if (recv(lease,data,sizeof(data),MSG_DONTWAIT)<=0) {
                        close(lease); lease=-1;
                        std::ofstream("/sys/class/leds/nabu:white:flash/brightness")<<0;
                        std::ofstream("/sys/class/leds/nabu:white:flash/flash_strobe")<<0;
                        std::cout<<"native lease released"<<std::endl;
                    }
                }
                if (descriptors[2].revents&POLLIN) {
                    int client=accept4(server,nullptr,nullptr,SOCK_CLOEXEC);
                    if (client>=0) {
                        if (lease>=0 || front.wanted || rear.wanted) {
                            send(client,"BUSY\n",5,MSG_NOSIGNAL); close(client);
                        } else {
                            front.detach(); rear.detach();
                            if (manager) manager->stop();
                            manager.reset(); lease=client;
                            send(client,"OK\n",3,MSG_NOSIGNAL);
                            std::cout<<"native lease acquired"<<std::endl;
                        }
                    }
                }
                if (lease>=0) continue;
                if (front.wanted!=front.running || rear.wanted!=rear.running) {
                    // Reconfigure the shared CAMSS graph only while stopped.
                    // Consumer FDs stay open during this brief mode transition.
                    // The 6.1 CAMSS/simple pipeline also retains state across
                    // stop/start. Recreate its manager like the native cam tool,
                    // without closing either standard webcam producer FD.
                    front.detach(); rear.detach();
                    if (manager) manager->stop();
                    manager.reset();
                    if (front.wanted || rear.wanted) {
                        manager=std::make_unique<CameraManager>();
                        require(!manager->start(),"restart camera manager");
                        front.attach(*manager); rear.attach(*manager);
                    }
                    for (CameraOutput *camera:{&front,&rear})
                        if (camera->wanted) camera->prepare(); else camera->blank();
                    for (CameraOutput *camera:{&front,&rear})
                        if (camera->wanted) camera->start();
                }
                auto now=std::chrono::steady_clock::now().time_since_epoch().count();
                for (CameraOutput *camera:{&front,&rear})
                    if (camera->running && now-camera->lastDelivery>3000000000LL)
                        throw std::runtime_error(camera->side+" sensor stalled for 3 seconds");
            }
            if (lease>=0) close(lease);
            close(server); unlink(socketPath);
        }
        if (manager) manager->stop();
        return 0;
    } catch (const std::exception &error) {
        std::cerr<<"camera bridge: "<<error.what()<<std::endl;
        return 1;
    }
}
