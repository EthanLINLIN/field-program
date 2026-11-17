# Field Program：面部情绪识别与 ESP32 控制（Linux）

本项目通过本地摄像头采集画面，使用 YuNet 检测人脸、FERPlus 模型识别情绪，并在网页上显示识别结果。ESP32 控制器通过串口执行固定的泵和雾化器预设流程。**在当前测试阶段，硬件流程由人工触发，尚未根据情绪识别结果自动选择动作。EEG 波形目前为仿真展示，系统已预留真实 EEG 设备数据接入的扩展接口，具体设备对接与功能集成有待后续开发。**

## 安装

使用具备文件和终端操作能力的 AI 时，可发送：**请读取项目的 AI 安装与调试指南，自动识别我的操作系统，完成环境配置、依赖安装、模型下载和软件运行验证，并报告结果。** 对应指南为 [AGENTS.md](AGENTS.md)。

请先将整个 Linux 文件夹放到最终使用位置，再打开该文件夹或运行脚本。首次使用必须安装依赖；复制文件本身不会完成安装。如果安装后移动了文件夹，请在新位置重新运行 `bash setup.sh` 重建环境。

准备 64 位 Linux 桌面、Python 3.11（含 venv 和 pip）、现代浏览器和摄像头。Python 包为 Linux x86-64 准备；其他 CPU 架构请自行确认依赖是否提供兼容版本。某些发行版还需要安装 OpenCV 所需的系统图形库，例如 Debian/Ubuntu 的 `libgl1` 和 `libglib2.0-0`。如需硬件控制，还需连接已烧录本项目固件的 ESP32。

在本目录的交互式终端运行：

```bash
bash setup.sh
bash start.sh
```

安装依赖需要网络。程序启动识别页、展示页和串口控制器，并尝试打开默认浏览器。保持终端运行；按 Ctrl+C 或在终端输入 `q` 并按回车，等待设备关闭和程序退出。

只运行网页而不连接 ESP32：`bash start.sh --no-esp32`。加 `--no-browser` 可手动打开：

- 识别页：<http://127.0.0.1:5050/>
- 展示页：<http://127.0.0.1:8080/display/opening_ui_eeg_motion.html>

在识别页按数字 `1` 开始摄像头识别，`4` 重置，`Q` 显示或隐藏调试信息。这些网页按键只在识别页被激活时生效。

## ESP32 操作

Linux 版使用**启动终端**接收硬件操作命令，不提供 Windows 的全局热键。控制器自动扫描串口，通过 `PING`/`PONG` 寻找已刷入本项目固件的设备。连接后初始为暂停状态；在终端中输入命令并按回车：

| 命令 | 操作 |
| --- | --- |
| `2` | 开始或继续 |
| `5` | 暂停并关闭输出 |
| `8` | 从头运行 |
| `q` | 安全退出 |

开始后先等待 5 秒，再执行固定时序。设备缺失时程序会持续重试；请确认 USB 数据线、串口驱动、设备权限和串口未被其他程序占用。Linux 串口通常为 `/dev/ttyUSB0` 或 `/dev/ttyACM0`；需要按发行版规则授予普通用户串口访问权限。实际泵与电路的极性和连接应与固件一致。

要编译或烧录固件，可单独安装 PlatformIO：

```bash
.venv/bin/python -m pip install platformio==6.2.0
.venv/bin/python -m platformio run -d 'src/esp32_controller/esp32_firmware'
.venv/bin/python -m platformio run -d 'src/esp32_controller/esp32_firmware' -t upload --upload-port /dev/ttyUSB0
```

将 `/dev/ttyUSB0` 替换为实际端口。第一次编译需要联网下载平台工具链，烧录前请停止控制程序。当前固件和上位机映射在 `src/esp32_controller` 中；其心跳超时保护需要实际硬件验证。


FERPlus 权重不包含在仓库中。首次启动识别服务时，程序从 ONNX Model Zoo 固定版本下载约 19 MB 权重，校验 SHA-256 后保存在项目目录外的用户缓存；后续可离线使用。Windows 默认位于 %LOCALAPPDATA%\field-emotion\models，Linux 默认位于 ${XDG_CACHE_HOME:-~/.cache}/field-emotion/models，也可通过环境变量 FERPLUS_CACHE_DIR 指定缓存目录。下载失败时请检查网络并重试。上游对该权重的许可标注仍有冲突；请先查看 THIRD_PARTY_NOTICES.md，判断是否适合你的用途。

## 隐私与公开发布

首次启动不包含真实用户照片或历史结果。识别时会在本地产生当前人像和日志，`.gitignore` 排除这些运行数据；压缩文件夹前仍应手动检查，因为压缩工具不一定遵守 `.gitignore`。服务默认只监听本机。

项目自有代码及五项自有视频、图片素材采用 MIT 许可证，见 [LICENSE](LICENSE)。第三方模型和依赖适用各自许可证；FERPlus 模型的上游标注尚有冲突，详情见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
