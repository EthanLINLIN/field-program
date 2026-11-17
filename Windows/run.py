from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EMOTION = ROOT / "src" / "emotion_system"
CONTROLLER = ROOT / "src" / "esp32_controller" / "pc_controller" / "main.py"
LOGS = ROOT / "deployment" / "logs"
STOP = LOGS / "esp32-controller.stop"
PAGES = ["http://127.0.0.1:5050/", "http://127.0.0.1:8080/display/opening_ui_eeg_motion.html"]


def wait_ready(process: subprocess.Popen, url: str) -> None:
    # First startup can download the pinned FERPlus model over the network.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服务启动失败（退出码 {process.returncode}）：{url}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    time.sleep(0.3)
                    if process.poll() is None:
                        return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"服务启动超时：{url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本地识别、展示和 ESP32 控制")
    parser.add_argument("--no-esp32", action="store_true", help="仅启动网页，不连接 ESP32")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    if os.name != "nt" and not args.no_esp32 and not sys.stdin.isatty():
        parser.error("Linux ESP32 控制需要交互式终端；仅启动网页可加 --no-esp32")

    LOGS.mkdir(parents=True, exist_ok=True)
    state = EMOTION / "display" / "data" / "emotion_state.json"
    if not state.exists():
        shutil.copyfile(state.with_name("emotion_state.example.json"), state)
    children: list[subprocess.Popen] = []
    controller = None
    env = dict(os.environ, PYTHONUTF8="1", EMOTION_AUTO_OPEN_BROWSER="0", PYTHONUNBUFFERED="1")
    try:
        app = EMOTION / "facial_emotion" / "current_best" / "app.py"
        recognition = subprocess.Popen([sys.executable, str(app)], cwd=app.parent, env=env)
        children.append(recognition)
        wait_ready(recognition, "http://127.0.0.1:5050/api/state")
        display = subprocess.Popen(
            [sys.executable, "-m", "http.server", "8080", "--bind", "127.0.0.1", "--directory", str(EMOTION)],
            cwd=ROOT, env=env,
        )
        children.append(display)
        wait_ready(display, PAGES[1])
        if not args.no_esp32:
            controller = subprocess.Popen([sys.executable, str(CONTROLLER)], cwd=ROOT, env=env)
            children.append(controller)
        print("服务已就绪：", *PAGES, sep="\n", flush=True)
        print("按 Ctrl+C 关闭服务。", flush=True)
        if not args.no_browser:
            for url in PAGES:
                webbrowser.open_new_tab(url)
        while True:
            for process in children:
                if process.poll() is not None:
                    if process is controller and process.returncode == 0:
                        print("ESP32 控制已退出，正在停止网页服务。", flush=True)
                        return 0
                    raise RuntimeError(f"服务提前退出（退出码 {process.returncode}）")
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("正在停止服务……", flush=True)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"启动或运行失败：{exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if controller is not None and controller.poll() is None:
            STOP.touch()
            try:
                controller.wait(timeout=7)
            except subprocess.TimeoutExpired:
                controller.terminate()
                try:
                    controller.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    controller.kill()
                    controller.wait()
        for process in reversed(children):
            if process is not controller and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
