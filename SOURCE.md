# 源码、构建与来源

作者：zouhong <admin@proyy.com>。原创部分许可见 `LICENSE`；第三方许可见 `THIRD_PARTY_NOTICES.md`。

## 发布内容

Git 仓库和项目源码归档包含应用、安装器、驱动适配源、补丁、内核配置及构建辅助脚本。发布页还提供：

- `nabu-camera_1.2.0-3_arm64.deb`：可安装的软件包。
- `nabu-camera-1.2.0-3-source.tar.gz`：与发布提交对应的项目源码。
- `nabu-camera-1.2.0-upstream-sources.tar`：完整固定版本上游源码归档，解包后位于 `downloads/`。包含 Linux、libcamera、PipeWire 原始包及 Ubuntu 补丁包；没有私钥或实机照片。
- `nabu-camera-1.2.0-3-build-inputs.tar.gz`：可选的已编译打包输入，含同版内核、模块、libcamera/IPA、bridge 和 PipeWire 插件。用于快速重打包；不替代上游源码。
- `SHA256SUMS`：上述资产校验值。

从发布页下载并校验后，在仓库根目录解包：

```sh
sha256sum --check SHA256SUMS --ignore-missing
# 已经下载对应资产时：
tar -xf nabu-camera-1.2.0-upstream-sources.tar
python3 scripts/prepare_sources.py
```

需要 Python 3.12+ 和 Git。脚本先校验 `config/upstream-archives.json` 的 SHA-256，解包干净源码、按顺序应用补丁，再检查 libcamera 15 个修改文件的完整哈希。已有 `sources/linux` 或 `sources/libcamera` 时拒绝覆盖。

## 快速重打包（不编译驱动）

在 Linux 中完成上述源码准备，再执行：

```sh
tar -xzf nabu-camera-1.2.0-3-build-inputs.tar.gz
./scripts/repack.sh 1.2.0-3
```

需要 `dpkg-deb`。输出位于 `dist/`。打包脚本从 `packaging/` 读取当前应用和声明，从 `build/` 读取已编译输入。模块放在 `usr/lib/modules`，不创建顶层 `lib`。构建与 IPA 签名必须配套，不能混用旧库和新 IPA。

## 固定源码基线

| 组件 | 精确来源 |
| --- | --- |
| Linux | [maverickjb/linux-6.1.10](https://github.com/maverickjb/linux-6.1.10)，`0e784bd693e1940dee39483b0bc33c4c1a1ffe50` |
| Linux 修补 | `patches/series-camera3-focus.txt` 中 9 个补丁，配置 `config/config-6.1.10-nabu-camera3` |
| libcamera | [libcamera](https://git.libcamera.org/libcamera/libcamera.git)，`191e202178f02430b5942397c70d215cdd2056fa`（v0.7.2） |
| libcamera 修补 | `patches/series-libcamera-final.txt` 中 9 个补丁 |
| v4l2loopback | [umlaeute/v4l2loopback](https://github.com/umlaeute/v4l2loopback)，v0.15.1；构建所需源及许可证在 `sources/v4l2loopback/` |
| CN3927 | `sources/nabu-focus/`，协议参照 Rockchip CN3927v，板级数据参照本机 OEM 描述；运行时读本机 EEPROM |
| Qualcomm 闪光驱动 | Linux `3857c2fe5449541e24afc5efdb0f81a8a8f9a3a0` 的 `leds-qcom-flash.c`，适配 6.1 接口和释放顺序；完整适配源在 `sources/nabu-flash/` |
| 闪光 class | 与上述 Linux 6.1 基线相同的 LED flash / V4L2 flash class，以外置模块构建 |
| PM8150L 接线 | 小米 nabu-r-oss `71b6effd2dfa8f703dd1ea17acd1323b2fd3cf6c` 的 nabu/pm8150l 设备树；本项目 software node 只允许 nabu |
| PipeWire GStreamer | Ubuntu `pipewire 1.0.5-1ubuntu3.3`，外加 `patches/userspace/nabu-pipewire-device-copy.patch` |

PipeWire 完整上游包与 Ubuntu Debian 补丁包已在上游源码资产中提供；构建插件所需的全部 8 个 `.c`、头文件、`config.h`、构建脚本和 MIT 声明位于 `sources/pipewire-gst/`，内容已应用 Ubuntu 补丁及本项目修补。

## 从源码重新编译

以下命令面向 Ubuntu 24.04 ARM64 构建机，不要求在平板上编译。需要足够磁盘/内存，以及 C/C++、LLVM/Clang/lld、make、flex、bison、OpenSSL/ELF 开发库、Meson/Ninja、Python Jinja2/YAML/PLY、GnuTLS、libevent、libyaml、libtiff、EGL/GLES、libyuv 和 GStreamer/PipeWire 开发包。

本次发布内核使用 LLVM/Clang 20.1.8；配置中记录了编译器版本。重新构建会生成新的二进制哈希，不承诺逐位重现已发布内核。用户态本次使用 Ubuntu ARM64 工具链。安装器会拒绝用不同内容覆盖当前同名相机内核；不要直接在正在使用该内核的平板上安装自编译的不同构建。

### 内核与外置模块

先运行 `prepare_sources.py`，设置 PATH 指向合适 LLVM 工具链，然后在仓库根目录运行：

```sh
mkdir -p build/kernel
cp config/config-6.1.10-nabu-camera3 build/kernel/.config
make -C sources/linux O="$PWD/build/kernel" ARCH=arm64 LLVM=1 olddefconfig
make -C sources/linux O="$PWD/build/kernel" ARCH=arm64 LLVM=1 -j4 Image.gz qcom/sm8150-xiaomi-nabu-camera.dtb modules
make -C sources/linux O="$PWD/build/kernel" ARCH=arm64 LLVM=1 INSTALL_MOD_PATH="$PWD/build/module-staging" INSTALL_MOD_STRIP=1 modules_install
for module in nabu-focus nabu-flash v4l2loopback; do
  make -C "$PWD/build/kernel" ARCH=arm64 LLVM=1 M="$PWD/sources/$module" -j4 modules
done
mkdir -p build/system-camera-modules
cp sources/nabu-flash/*.ko sources/v4l2loopback/v4l2loopback.ko build/system-camera-modules/
llvm-strip --strip-debug sources/nabu-focus/nabu-cn3927.ko build/system-camera-modules/*.ko
python3 scripts/stage_kernel.py
```

外置模块必须使用同一内核构建目录、配置和 `Module.symvers`。四个闪光模块加一个 loopback 模块共五个扩展模块；CN3927 模块加入内核模块归档。`stage_kernel.py` 为本次构建生成启动文件和模块哈希清单。

`patches/host-macos-file2alias-uuid.patch` 仅供 macOS 交叉构建的主机工具适配参考，不是 Linux 目标机运行代码补丁；上述 Linux 原生构建不使用它。

### libcamera 与桥接程序

```sh
meson setup build/libcamera sources/libcamera \
  --prefix=/opt/nabu-camera/runtime --libdir=lib/aarch64-linux-gnu \
  --buildtype=release -Dpipelines=simple -Dipas=simple -Dcam=enabled \
  -Dcam-output-kms=disabled -Dcam-output-sdl2=disabled \
  -Dapps-output-dng=enabled -Dqcam=disabled -Dgstreamer=disabled \
  -Dv4l2=disabled -Dtest=false -Ddocumentation=disabled \
  -Dpycamera=disabled -Dsoftisp-gpu=enabled
ninja -C build/libcamera -j4
DESTDIR="$PWD/build/runtime-stage" ninja -C build/libcamera install
# 库和 IPA 由同一次构建生成；不要把签名私钥放入发行物。
tar -C build/runtime-stage -czf build/deb-runtime.tar.gz opt
export PKG_CONFIG_PATH="$PWD/build/runtime-stage/opt/nabu-camera/runtime/lib/aarch64-linux-gnu/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$PWD/build/runtime-stage"
g++ -O2 -std=c++17 packaging/camera_bridge.cpp -o build/camera-bridge \
  $(pkg-config --cflags --libs libcamera) -lyuv -pthread \
  -Wl,-rpath,/opt/nabu-camera/runtime/lib/aarch64-linux-gnu \
  -Wl,-rpath-link,"$PWD/build/runtime-stage/opt/nabu-camera/runtime/lib/aarch64-linux-gnu"
unset PKG_CONFIG_PATH PKG_CONFIG_SYSROOT_DIR
```

Meson 为本次构建生成 IPA 私钥，运行库嵌入对应公钥。私钥留在本机构建目录，不提交、不上传。`deb-runtime.tar.gz` 只打包 install 产生的运行库/IPA/SDK。

### PipeWire 插件与 deb

```sh
mkdir -p build/pipewire-gst
cp sources/pipewire-gst/* build/pipewire-gst/
sh build/pipewire-gst/build.sh
./scripts/repack.sh 1.2.0-3
```

此插件仅对名称为 Pad-Front-Camera / Pad-Rear-Camera 的 v4l2loopback 设备启用 always-copy，不替换 PipeWire 守护进程。

## 验证状态

发布使用此前实机验证的二进制输入，本次验证源码补丁应用、应用代码等价、deb 内容/哈希及远端发布完整性；没有为文档与署名更新重复执行完整内核编译或硬件长测。重新编译后的内核/库需要重新做设备验证，不能自动继承发布二进制的实测结果。
