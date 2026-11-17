# AI 安装与调试指南：Windows

本指南适用于用户要求 AI 自动安装、运行或调试本项目的情形。以用户的具体要求和运行环境权限为准；不要因为阅读了本文件就自动操作。请先读取本文件、`README.md` 和 `THIRD_PARTY_NOTICES.md`。如果用户交付的是包含 Windows、Linux 两个目录的完整公开版，先识别操作系统，再进入对应目录；不要把 Linux 目录当作 Windows 版本运行。

## 1. 检查环境

- 确认当前系统为 Windows，并在本文件所在目录执行以下命令。确认项目已经放在最终使用位置；移动已安装的项目后需要重建或修复 `.venv`。
- 检查 64 位 Python 3.11、Python Launcher、pip 和可用磁盘空间。运行 `py -3.11 -c "import sys,struct; print(sys.version); print(struct.calcsize('P')*8)"`，确认输出为 Python 3.11 和 64 位。
- 若 Python Launcher、Python 或必要权限不可用，依据实际环境尝试用户授权范围内的安装或修复；若受权限、网络或环境限制，说明阻碍及所需操作，不要声称安装成功。
- 安装 Python 依赖和首次下载模型需要访问网络。FERPlus 上游的 MIT/Apache-2.0 标注存在冲突，来源及范围见 `THIRD_PARTY_NOTICES.md`；下载不意味着该冲突已解决。

## 2. 安装

在 PowerShell 中使用非交互式命令（`setup.bat` 末尾有 `pause`，不适合无人值守调用）：

```powershell
py -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed' }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
.\.venv\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
```

如果已有环境可用，先检查再决定是否重新创建；不要删除用户数据或覆盖不相关的环境。依赖安装失败时查看完整错误，针对实际问题修复并复测。

## 3. 软件验证

1. 检查 `127.0.0.1:5050` 和 `127.0.0.1:8080` 是否被占用；不要结束不属于本次任务的进程。端口冲突时查明并向用户报告。
2. 使用 `.\.venv\Scripts\python.exe run.py --no-esp32 --no-browser` 启动本次验证的服务，保留进程及输出。该命令不会启动 ESP32 控制器或自动打开浏览器。首次运行将从上游下载 FERPlus 到项目目录外的缓存，并校验 SHA-256；失败时检查网络、缓存路径及错误信息。不要绕过哈希校验或声称真实下载成功，除非已实际验证。
3. 分别检查 `http://127.0.0.1:5050/`、`http://127.0.0.1:5050/api/state`、`http://127.0.0.1:5050/api/field-emotion/latest` 和 `http://127.0.0.1:8080/display/opening_ui_eeg_motion.html` 返回正常；必要时检查 `display/videos/` 中三个视频资源。核对服务确实由本次启动的进程提供，避免把其他服务误判为通过。
4. 按请求保留运行中的服务，或通过受控的 Ctrl+C 停止本次启动的启动器并确认其子进程退出；不要强制终止其他应用。报告实际做过的检查及结果。

## 4. 硬件边界与结果

- 默认只执行软件验证：不连接或占用 ESP32 串口、不烧录或擦除固件、不触发 GPIO、水泵或雾化器，也不启动摄像头采集。硬件识别和实体动作需要用户明确提出后再进行。
- 如果用户明确要求硬件联调，先核对串口、已烧录固件、供电及实际接线，再按用户授权和 README 操作。真实设备未接入、摄像头未检查或未执行 GPIO 动作时，应分别标明未验证，不能由模拟结果推断实机通过。
- 如果需要用户提供缺失信息或系统权限，先完成不依赖该信息的检查，再说明具体阻碍。报告系统、安装结果、模型下载或缓存结果、接口检查、硬件检查范围、服务状态及尚未解决的问题。
