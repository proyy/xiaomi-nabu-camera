# Xiaomi Pad 5 Ubuntu Camera

**作者：zouhong · 邮箱：[admin@proyy.com](mailto:admin@proyy.com)**  
Copyright © 2026 zouhong。原创部分源码公开、仅限非商业使用；第三方组件保持原许可证。详见 [LICENSE](LICENSE) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。

为 **Xiaomi Pad 5 / nabu、Ubuntu 24.04 ARM64、Simple Init UEFI** 提供前后摄像头、后置对焦与闪光灯，以及桌面相机应用和标准 V4L2 设备。

[下载 v1.2.0-3](https://github.com/proyy/xiaomi-nabu-camera/releases/tag/v1.2.0-3) · [构建与来源](SOURCE.md) · [验证记录](VALIDATION.md)

## 安装到原 Ubuntu

下载发布页中的 `nabu-camera_1.2.0-3_arm64.deb`，在平板运行：

```sh
sudo apt install ./nabu-camera_1.2.0-3_arm64.deb
```

首次安装需要网络获取尚未安装的 Ubuntu 依赖。deb 已包含修补后的内核、设备树、661 个配套模块、私有 libcamera/IPA、通用相机服务和 Pad 相机应用，无需现场编译驱动。其他型号、Ubuntu 版本或不兼容启动布局会被安装检查拒绝。

安装器更新原 **Linux** 启动项并将其设为默认项。首次安装完成后重启，按原来的方式进入 Linux；原 Ubuntu 桌面和数据继续使用。相机内核版本是 `6.1.10-nabu-camera3`，名称中的 camera3 是内核构建标记，并不要求进入独立的 Camera Test v3 系统。不要混装其他版本的内核、设备树和模块。

从已安装的 1.2.0-2 更新到 1.2.0-3 只涉及版权、文档和打包元数据，不改变驱动或相机功能，无需因此重启。安装/升级前关闭所有相机应用。

## Pad 相机

在 Ubuntu 应用列表打开 **Pad 相机**。底部图标提供拍照、录像启停、前后切换、HD/全像素预览、旋转、自动/手动对焦、数字缩放和闪光模式；悬停可见操作说明，手动对焦和缩放按钮打开滑条。

| 功能 | 前置 OV8856 | 后置 OV13B10 + CN3927 |
| --- | --- | --- |
| 全像素照片 | 3272 × 2464，约 8 MP | 4200 × 3120，约 13 MP |
| 高速预览采集模式 | 1632 × 1232，约 30 fps | 1920 × 1080，约 60 fps |
| 本机 GPU 预览实测 | 约 30 fps | 约 54 fps |
| 无声 H.264 MP4 实测 | 平均约 29.5 fps | 平均约 48.7 fps |
| 对焦 | 固定焦点 | 单次自动对焦、手动滑条 |
| 数字缩放 | 1–4× | 1–4× |
| 硬件闪光灯 | 无 | 关闭 / 自动 / 强制闪光 / 常亮补光 |

照片保存到 `~/Pictures/Pad Camera/`。拍照会切到全像素模式，保存后恢复预览。录像使用当前预览分辨率、旋转和缩放，录制期间锁定这些设置，停止后可调整。全像素与最高帧率是不同模式；帧率受光线、分辨率、显示和编码负载影响。数字缩放为裁切放大，不增加光学细节。

后置自动对焦评估画面中央；暗处或缺少纹理时可手动对焦。校准从当前平板的 EEPROM 读取，不使用其他机器的校准数据。闪光灯常亮上限 100 mA、闪光上限 250 mA、硬件超时 300 ms；拍照后自动关灯。

## 其他相机软件

Cheese 等支持 V4L2 的软件可选择以下设备：

| 设备名称 | 节点 / 稳定链接 | 格式 |
| --- | --- | --- |
| Pad-Front-Camera | `/dev/video42` / `/dev/nabu-camera-front` | YUYV，1600 × 1200，30 fps |
| Pad-Rear-Camera | `/dev/video43` / `/dev/nabu-camera-rear` | YUYV，1920 × 1080，约 30 fps |

通用接口采用 30 fps 模式以兼容应用的设备筛选；Pad 相机原生模式可使用更高帧率。**使用其他相机软件前先关闭 Pad 相机**：Pad 相机打开期间独占原生采集，退出后恢复通用接口。通用服务按需启动传感器，空闲不取流。

标准硬件闪光灯入口为 `/sys/class/leds/nabu:white:flash`；第三方软件能否显示闪光按钮取决于其自身实现，V4L2 取流不等于应用自动支持闪光控制。

## 命令行与诊断

```sh
nabu-camera --list
nabu-camera --camera front --capture front.jpg
nabu-camera --camera rear --autofocus --flash --capture rear.jpg
nabu-camera --diagnose
nabu-camera --self-test ./camera-check --seconds 20
```

自检使用新的输出目录。桌面用户通过 udev/uaccess 获得权限；首次安装后如权限尚未生效，注销再登录。SSH 用户可执行 `sudo usermod -aG video,render "$USER"` 后重新登录。

高级原生调试前关闭相机应用并运行 `sudo systemctl stop nabu-camera-bridge`，再使用 `nabu-camera-cam`。结束后运行 `sudo systemctl start nabu-camera-bridge`。避免同时启动多个独立的 libcamera CameraManager。

## 安装行为与卸载

- 私有 libcamera 位于 `/opt/nabu-camera/runtime`，不替换系统 libcamera 共享库。
- 通过 `dpkg-divert` 暂停旧版 GStreamer libcamera provider，并替换带有两颗 Pad 相机专用 `always-copy` 修补的 PipeWire GStreamer 插件；卸载时恢复系统插件。
- 模块安装到 `/usr/lib/modules`，不创建顶层 `/lib`，检查 `/lib -> usr/lib`，避免破坏 merged-usr 布局。
- `nabu-camera-prepare.service` 负责开机模块/权限/校准；`nabu-camera-bridge.service` 提供通用设备。

退回系统原有内核：

```sh
sudo nabu-camera-restore-kernel
sudo reboot
# 进入原有内核后再卸载：
sudo apt remove nabu-camera
```

相机内核运行时拒绝卸载其模块。恢复命令指向系统原有内核，不进行整机备份/恢复。ESP 中的相机内核文件和本机校准记录会保留。

## 画质与已知限制

1.2.0 修复 packed RAW10/12 的纹理坐标、行填充与低位解包，使用 GPU 软件 ISP 和轻量照片降噪。低照度高增益仍会有噪声；没有完整原厂硬件 ISP 调色、标准色卡/镜头阴影标定或标准 libcamera 连续 AF。前置固定焦点，录像目前没有音轨。

帧率、对焦、闪光灯及通用相机验证结果见 [VALIDATION.md](VALIDATION.md)。这些结果来自一台 Pad 5，不能替代对所有硬件批次、任意第三方应用和所有拍摄环境的验证。

## 源码与版权

仓库包含原创应用、安装器、服务、驱动源、补丁、内核配置、构建脚本和验证摘要。发布页同时提供 deb、项目源码归档、固定版本上游源码、可选的已编译打包输入和 SHA-256 校验文件。源码构建见 [SOURCE.md](SOURCE.md)。

**作者：zouhong；邮箱：admin@proyy.com。** 原创部分允许非商业学习、使用、修改和再分发；商业使用需另行书面授权。非商业限制不符合 [OSI 开源定义](https://opensource.org/osd)，因此本项目明确称为“源码公开、仅限非商业使用”。Linux、libcamera、v4l2loopback、PipeWire 及其衍生驱动/补丁继续遵守原有 GPL/LGPL/MIT 等许可；不额外限制它们的商业使用。
