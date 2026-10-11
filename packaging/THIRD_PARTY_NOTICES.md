# 版权和第三方许可证范围

项目作者：**zouhong**，邮箱：**admin@proyy.com**。

独立原创应用、安装器、打包工具、测试工具和项目文档使用 `LicenseRef-Nabu-Camera-NonCommercial-1.0`，全文见 `LICENSE`。这是一份非商业源码许可；不能将整个软件栈描述为禁止商业使用。

| 组件或路径 | 来源及许可 |
| --- | --- |
| `packaging/` 中有 LicenseRef 标识的原创代码和配置、项目文档、`scripts/` | Copyright (c) 2026 zouhong，非商业源码许可 |
| Linux 内核及 `patches/series-camera3-focus.txt` 指定的补丁 | maverickjb/Linux 及原始贡献者；以每个文件 SPDX 为准，主要为 GPL-2.0-only，设备树部分有双许可证 |
| `sources/nabu-focus/`、`sources/nabu-flash/` | CN3927/PM8150L 适配和 Linux 驱动；保持 GPL-2.0-only 及原文件通知。zouhong 的适配署名不改变原许可 |
| libcamera 运行库及其补丁 | libcamera 贡献者；库主要为 LGPL-2.1-or-later，cam/IPA 及其他文件分别为 GPL-2.0-or-later、BSD、CC0 等，按文件生效 |
| `sources/v4l2loopback/` | v4l2loopback v0.15.1，GPL-2.0 系列，详见 COPYING 和源文件 |
| `sources/pipewire-gst/`、PipeWire 补丁 | PipeWire 1.0.5 / Ubuntu 1.0.5-1ubuntu3.3，MIT，保留原作者声明 |
| GTK、GLib、GStreamer、FFmpeg、libyuv 等 Ubuntu 依赖 | 通过 APT 安装，适用各自 Ubuntu 包的版权文件；不由本项目重新许可 |

第三方组件允许的商业使用、修改和再分发不受本项目原创部分非商业条款限制。GPL 衍生驱动及补丁不能附加非商业限制。共享库动态链接，使用者可依据其许可证替换或调试这些库。

完整上游源码、固定版本及补丁取得方式见 `SOURCE.md` 和发布页；上游源码归档与 deb 一同提供。`licenses/` 收录许可证文本，源码文件中保留精确的作者和 SPDX 标识。GitHub 自动识别的单一许可证标签不能代表整个混合许可软件包。

参考：[OSI 开源定义第 6 条](https://opensource.org/osd)、[GNU GPL v2 第 6 条](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)。
