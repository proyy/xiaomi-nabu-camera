#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p include/gst
cp ./*.h include/gst/
gcc -shared -fPIC -O2 -I. -Iinclude ./*.c -o libgstpipewire.so $(pkg-config --cflags --libs gstreamer-1.0 gstreamer-video-1.0 gstreamer-audio-1.0 gstreamer-allocators-1.0 gstreamer-net-1.0 libpipewire-0.3) -pthread
