from __future__ import annotations

import atexit
import ctypes
import hashlib
import json
import os
import signal
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import serial
from serial.tools import list_ports

try:
    import win32api  # type: ignore
except ImportError:  # pragma: no cover - allows limited non-Windows debugging
    win32api = None


BAUD_RATE = 115200
SERIAL_TIMEOUT_SECONDS = 0.6
SCAN_STABILIZE_SECONDS = 1.2
PING_TIMEOUT_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 0.5
INITIAL_SILENT_SECONDS = 5.0
SLEEP_STEP_SECONDS = 0.05
SCAN_RETRY_ROUNDS = 3
SCAN_RETRY_DELAY_SECONDS = 1.5
SERIAL_RELEASE_DELAY_SECONDS = 0.25
LAUNCHER_WATCHDOG_INTERVAL_SECONDS = 0.5

OUTPUT_ACTIVE_HIGH = True

USB_PORT_KEYWORDS = (
    "usb",
    "ch340",
    "cp210",
    "silicon labs",
    "wch",
    "uart",
    "esp32",
    "serial",
)

LEGACY_PORT_KEYWORDS = (
    "acpi\\pnp0501",
    "standard serial over bluetooth link",
)

PIN_MAP = {
    "L2A": 12,
    "L2B": 14,
    "L2C": 27,
    "L2E": 25,
    "L2F": 33,
    "L2G": 32,
    "L1B": 13,
    "L2D": 26,
    "L3A": 2,
    "L3B": 0,
    "L4A": 17,
    "L4B": 5,
    "L4C": 18,
    "L4D": 19,
    "PWM": 21,
}

ALL_PINS = sorted(set(PIN_MAP.values()))

HOTKEY_ACTION_RESUME = "resume"
HOTKEY_ACTION_PAUSE = "pause"
HOTKEY_ACTION_RESTART = "restart"
HOTKEY_NUMPAD_SCAN_CODES = {
    80: HOTKEY_ACTION_RESUME,
    76: HOTKEY_ACTION_PAUSE,
    72: HOTKEY_ACTION_RESTART,
}

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01


@dataclass(frozen=True)
class ParallelStep:
    label: str
    pin: int
    duration: float


class ShutdownRequested(Exception):
    pass


class RestartRequested(Exception):
    pass


class ControlSignal:
    RUN = "run"
    PAUSE = "pause"
    RESTART = "restart"
    STOP = "stop"


class RunController:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = True
        self._restart_requested = False
        self._completed = False

    def request_resume(self) -> str:
        with self._condition:
            if self._completed:
                return "completed"
            if self._restart_requested:
                return "restart_pending"
            if not self._paused:
                return "already_running"
            self._paused = False
            self._condition.notify_all()
            return "resumed"

    def request_pause(self) -> str:
        with self._condition:
            if self._completed:
                return "completed"
            if self._paused:
                return "already_paused"
            self._paused = True
            self._condition.notify_all()
            return "paused"

    def request_restart(self) -> str:
        with self._condition:
            already_pending = self._restart_requested
            self._restart_requested = True
            self._paused = False
            self._completed = False
            self._condition.notify_all()
            return "restart_pending" if already_pending else "restart_requested"

    def wait_until_running(self) -> str:
        with self._condition:
            while True:
                if stop_event.is_set():
                    return ControlSignal.STOP
                if self._restart_requested:
                    return ControlSignal.RESTART
                if not self._paused and not self._completed:
                    return ControlSignal.RUN
                self._condition.wait(timeout=0.2)

    def current_signal(self) -> str:
        with self._condition:
            if stop_event.is_set():
                return ControlSignal.STOP
            if self._restart_requested:
                return ControlSignal.RESTART
            if self._paused:
                return ControlSignal.PAUSE
            return ControlSignal.RUN

    def consume_restart_request(self) -> bool:
        with self._condition:
            requested = self._restart_requested
            self._restart_requested = False
            if requested:
                self._paused = False
                self._completed = False
                self._condition.notify_all()
            return requested

    def mark_sequence_completed(self) -> None:
        with self._condition:
            self._completed = True
            self._paused = True
            self._restart_requested = False
            self._condition.notify_all()

    def notify_state_changed(self) -> None:
        with self._condition:
            self._condition.notify_all()


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


serial_conn: serial.Serial | None = None
serial_lock = threading.RLock()
stop_event = threading.Event()
heartbeat_stop_event = threading.Event()
heartbeat_thread: threading.Thread | None = None
launcher_watchdog_thread: threading.Thread | None = None
hotkey_listener_thread: threading.Thread | None = None
hotkey_hook_thread_id: int | None = None
hotkey_hook_handle: int | None = None
hotkey_hook_callback = None
cleanup_lock = threading.Lock()
cleanup_done = False
cleanup_complete = threading.Event()
cleanup_owner = None
run_controller = RunController()
instance_handle = None
controller_phase = "starting"
runtime_status_lock = threading.Lock()
DEPLOYMENT_LOG_DIR = Path(__file__).resolve().parents[3] / "deployment" / "logs"
STATUS_FILE = Path(os.getenv("ESP32_STATUS_FILE", str(DEPLOYMENT_LOG_DIR / "esp32-controller-status.json")))
STOP_FILE = Path(os.getenv("ESP32_STOP_FILE", str(DEPLOYMENT_LOG_DIR / "esp32-controller.stop")))


def claim_controller_instance() -> bool:
    global instance_handle
    if os.name != "nt":
        import fcntl

        DEPLOYMENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        handle = (DEPLOYMENT_LOG_DIR / "esp32-controller.lock").open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        instance_handle = handle
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    identity = hashlib.sha256(str(Path(__file__).resolve()).lower().encode()).hexdigest()[:16]
    handle = kernel32.CreateMutexW(None, False, "Local\\ESP32PresetController-" + identity)
    error = kernel32.GetLastError()
    if not handle:
        raise OSError("无法创建 ESP32 控制程序实例锁。")
    if error == 183:
        kernel32.CloseHandle(handle)
        return False
    instance_handle = handle
    return True


def publish_runtime_status() -> None:
    with runtime_status_lock:
        with run_controller._condition:
            sequence = {"paused": run_controller._paused, "completed": run_controller._completed,
                        "restartRequested": run_controller._restart_requested}
        connection = serial_conn
        status = {"pid": os.getpid(), "phase": controller_phase,
                  "connected": bool(connection and connection.is_open),
                  "port": connection.port if connection else None,
                  "hotkeysReady": bool(hotkey_hook_handle), "sequence": sequence,
                  "updatedAt": datetime.now().astimezone().isoformat()}
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATUS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(STATUS_FILE)


def runtime_status_worker() -> None:
    while not stop_event.is_set():
        if STOP_FILE.exists():
            STOP_FILE.unlink(missing_ok=True)
            cleanup_and_exit("停止演示服务")
            return
        try:
            publish_runtime_status()
        except OSError as exc:
            log(f"写入运行状态失败: {exc}")
        stop_event.wait(0.25)


def query_device_status() -> dict:
    """Read actual GPIO levels without changing outputs or refreshing the watchdog."""
    with serial_lock:
        connection = serial_conn
        if connection is None or not connection.is_open:
            raise RuntimeError("ESP32 未连接。")
        connection.write(b"STATUS\n")
        connection.flush()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            raw = connection.readline()
            if not raw:
                continue
            try:
                status = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                continue
            if isinstance(status, dict) and "activePins" in status and "firmware" in status:
                return status
        raise RuntimeError("ESP32 未返回 STATUS，请部署本项目的最新固件。")


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def com_sort_key(device_name: str) -> tuple[int, str]:
    upper_name = device_name.upper()
    if upper_name.startswith("COM"):
        suffix = upper_name[3:]
        if suffix.isdigit():
            return int(suffix), upper_name
    return 10**9, upper_name


def port_identity_text(port: list_ports.ListPortInfo) -> str:
    return " | ".join(
        part
        for part in (
            port.device,
            port.description or "",
            getattr(port, "manufacturer", "") or "",
            getattr(port, "product", "") or "",
            port.hwid or "",
        )
        if part
    ).lower()


def is_preferred_usb_port(port: list_ports.ListPortInfo) -> bool:
    identity = port_identity_text(port)
    return any(keyword in identity for keyword in USB_PORT_KEYWORDS)


def is_legacy_builtin_port(port: list_ports.ListPortInfo) -> bool:
    identity = port_identity_text(port)
    return any(keyword in identity for keyword in LEGACY_PORT_KEYWORDS)


def format_port_summary(port: list_ports.ListPortInfo) -> str:
    details = []
    if port.description:
        details.append(port.description)
    if getattr(port, "manufacturer", None):
        details.append(str(port.manufacturer))
    if getattr(port, "product", None):
        details.append(str(port.product))
    if port.hwid:
        details.append(port.hwid)
    return f"{port.device} ({', '.join(details)})" if details else port.device


def order_candidate_ports(
    ports: list[list_ports.ListPortInfo], preferred_port_name: str = ""
) -> list[list_ports.ListPortInfo]:
    normalized_preferred = preferred_port_name.strip().upper()

    def sort_key(port: list_ports.ListPortInfo) -> tuple[int, int, str]:
        device_upper = port.device.upper()
        if normalized_preferred and device_upper == normalized_preferred:
            priority_group = 0
        elif is_preferred_usb_port(port) and not is_legacy_builtin_port(port):
            priority_group = 1
        elif not is_legacy_builtin_port(port):
            priority_group = 2
        else:
            priority_group = 3
        numeric_order, name_order = com_sort_key(port.device)
        return priority_group, numeric_order, name_order

    return sorted(ports, key=sort_key)


def check_stop_requested() -> None:
    if stop_event.is_set():
        raise ShutdownRequested("检测到退出请求，流程中止。")


def is_windows_process_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return False

    exit_code = ctypes.c_ulong()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def release_serial_control_lines(connection: serial.Serial) -> None:
    try:
        connection.setDTR(False)
    except Exception:
        pass

    try:
        connection.setRTS(False)
    except Exception:
        pass


def prepare_serial_connection(connection: serial.Serial) -> None:
    release_serial_control_lines(connection)
    time.sleep(SERIAL_RELEASE_DELAY_SECONDS)
    connection.reset_input_buffer()
    connection.reset_output_buffer()


def launcher_watchdog_worker(launcher_pid: int) -> None:
    while not stop_event.is_set():
        if not is_windows_process_alive(launcher_pid):
            log(f"检测到启动窗口进程 PID={launcher_pid} 已结束，立即执行安全退出。")
            cleanup_and_exit("启动窗口已关闭")
            return
        time.sleep(LAUNCHER_WATCHDOG_INTERVAL_SECONDS)


def start_launcher_watchdog() -> None:
    global launcher_watchdog_thread

    launcher_pid_text = os.environ.get("ESP32_LAUNCHER_PID", "").strip()
    if not launcher_pid_text:
        return

    try:
        launcher_pid = int(launcher_pid_text)
    except ValueError:
        log(f"忽略无效的 ESP32_LAUNCHER_PID: {launcher_pid_text}")
        return

    launcher_watchdog_thread = threading.Thread(
        target=launcher_watchdog_worker,
        args=(launcher_pid,),
        name="launcher-watchdog",
        daemon=True,
    )
    launcher_watchdog_thread.start()
    log(f"已启动启动窗口监护线程，监视 PID={launcher_pid}。")


def wait_until_sequence_running() -> None:
    signal = run_controller.wait_until_running()
    if signal == ControlSignal.STOP:
        raise ShutdownRequested("检测到退出请求，流程中止。")
    if signal == ControlSignal.RESTART:
        raise RestartRequested("收到从头重新运行指令。")


def wait_active_duration(remaining: float) -> tuple[float, str]:
    last_tick = time.monotonic()
    while remaining > 0:
        signal = run_controller.current_signal()
        if signal != ControlSignal.RUN:
            return remaining, signal

        time.sleep(min(SLEEP_STEP_SECONDS, remaining))
        now = time.monotonic()
        remaining = max(0.0, remaining - (now - last_tick))
        last_tick = now

    return 0.0, ControlSignal.RUN


def handle_hotkey_action(action: str) -> None:
    if stop_event.is_set():
        return

    if action == HOTKEY_ACTION_RESUME:
        status = run_controller.request_resume()
        if status == "resumed":
            log("收到开始/继续命令（2），流程将从当前位置继续运行。")
        elif status == "completed":
            log("当前流程已完成，如需从头重新运行，请使用命令 8。")
    elif action == HOTKEY_ACTION_PAUSE:
        status = run_controller.request_pause()
        if status == "paused":
            log("收到暂停命令（5），立即关闭全部端口并保留当前位置。")
            best_effort_runtime_all_off("全局暂停按键")
        elif status == "completed":
            log("当前流程已完成，全部设备保持关闭。")
    elif action == HOTKEY_ACTION_RESTART:
        status = run_controller.request_restart()
        if status == "restart_requested":
            log("收到重新运行命令（8），将立刻关闭全部端口并从头运行流程。")
        stop_heartbeat()
        best_effort_runtime_all_off("全局重新运行按键")


def hotkey_listener_worker() -> None:
    global hotkey_hook_callback, hotkey_hook_handle, hotkey_hook_thread_id

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pressed_scan_codes: set[int] = set()

    low_level_keyboard_proc = ctypes.WINFUNCTYPE(
        wintypes.LPARAM,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        low_level_keyboard_proc,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HANDLE
    user32.CallNextHookEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = wintypes.LPARAM
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    @low_level_keyboard_proc
    def keyboard_proc(
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        if n_code == HC_ACTION:
            keyboard_data = ctypes.cast(
                l_param,
                ctypes.POINTER(KBDLLHOOKSTRUCT),
            ).contents
            action = HOTKEY_NUMPAD_SCAN_CODES.get(int(keyboard_data.scanCode))
            is_extended = bool(keyboard_data.flags & LLKHF_EXTENDED)

            if action and not is_extended:
                scan_code = int(keyboard_data.scanCode)
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if scan_code not in pressed_scan_codes:
                        pressed_scan_codes.add(scan_code)
                        threading.Thread(
                            target=handle_hotkey_action,
                            args=(action,),
                            name=f"hotkey-{action}",
                            daemon=True,
                        ).start()
                elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                    pressed_scan_codes.discard(scan_code)

        return int(user32.CallNextHookEx(None, n_code, w_param, l_param))

    hotkey_hook_callback = keyboard_proc
    hotkey_hook_thread_id = int(kernel32.GetCurrentThreadId())
    hotkey_hook_handle = user32.SetWindowsHookExW(
        WH_KEYBOARD_LL,
        keyboard_proc,
        kernel32.GetModuleHandleW(None),
        0,
    )

    if not hotkey_hook_handle:
        log("全局小键盘监听启动失败，控制程序保持暂停；请检查桌面权限后重新启动。")
        hotkey_hook_callback = None
        hotkey_hook_thread_id = None
        return

    msg = wintypes.MSG()
    while True:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result <= 0 or msg.message == WM_QUIT:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    if hotkey_hook_handle:
        user32.UnhookWindowsHookEx(hotkey_hook_handle)

    hotkey_hook_handle = None
    hotkey_hook_thread_id = None
    hotkey_hook_callback = None


def terminal_control_worker() -> None:
    """Read Linux commands in the foreground terminal; EOF never starts hardware."""
    commands = {"2": HOTKEY_ACTION_RESUME, "5": HOTKEY_ACTION_PAUSE, "8": HOTKEY_ACTION_RESTART}
    log("Linux 终端控制：输入 2、5、8 后按回车；输入 q 后按回车退出。")
    while not stop_event.is_set():
        line = sys.stdin.readline()
        if not line:
            log("终端输入已结束；控制程序继续等待停止信号。")
            return
        command = line.strip().lower()
        if command == "q":
            stop_event.set()
            run_controller.notify_state_changed()
            return
        action = commands.get(command)
        if action:
            handle_hotkey_action(action)
        elif command:
            log("未知命令：请输入 2、5、8 或 q。")


def start_global_hotkey_listener() -> None:
    global hotkey_listener_thread

    if os.name != "nt":
        hotkey_listener_thread = threading.Thread(
            target=terminal_control_worker, name="terminal-control-listener", daemon=True
        )
        hotkey_listener_thread.start()
        return

    if hotkey_listener_thread and hotkey_listener_thread.is_alive():
        return

    hotkey_listener_thread = threading.Thread(
        target=hotkey_listener_worker,
        name="global-hotkey-listener",
        daemon=True,
    )
    hotkey_listener_thread.start()
    log("已启用全局小键盘热键：2=开始/继续，5=暂停，8=从头重新运行。")


def stop_global_hotkey_listener() -> None:
    global hotkey_listener_thread

    if os.name != "nt":
        return

    if hotkey_hook_thread_id is not None:
        try:
            ctypes.windll.user32.PostThreadMessageW(hotkey_hook_thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass

    if hotkey_listener_thread and hotkey_listener_thread.is_alive():
        hotkey_listener_thread.join(timeout=1.0)

    hotkey_listener_thread = None


def safe_sleep(seconds: float) -> None:
    end_time = time.monotonic() + seconds
    while time.monotonic() < end_time:
        check_stop_requested()
        remaining = end_time - time.monotonic()
        time.sleep(min(SLEEP_STEP_SECONDS, max(0.0, remaining)))


def send_cmd(command: str, *, log_command: bool = True) -> None:
    global serial_conn

    check_stop_requested()
    payload = f"{command}\n".encode("utf-8")
    with serial_lock:
        check_stop_requested()
        connection = serial_conn
        if connection is None or not connection.is_open:
            raise RuntimeError("串口未连接，无法发送命令。")
        if log_command:
            log(f"发送串口命令: {command}")
        connection.write(payload)
        connection.flush()


def best_effort_runtime_all_off(reason: str) -> None:
    global serial_conn

    if serial_conn is None or not serial_conn.is_open:
        return

    try:
        log(f"执行运行时 ALL_OFF，原因: {reason}")
        with serial_lock:
            serial_conn.write(b"ALL_OFF\n")
            serial_conn.flush()
    except Exception as exc:  # pragma: no cover - best effort cleanup
        log(f"发送运行时 ALL_OFF 失败: {exc}")


def best_effort_all_off(reason: str) -> None:
    global serial_conn

    if serial_conn is None or not serial_conn.is_open:
        return

    try:
        log(f"执行安全关闭 ALL_OFF，原因: {reason}")
        with serial_lock:
            serial_conn.write(b"ALL_OFF\n")
            serial_conn.flush()
            release_serial_control_lines(serial_conn)
    except Exception as exc:  # pragma: no cover - best effort cleanup
        log(f"发送 ALL_OFF 失败: {exc}")


def all_off(reason: str) -> None:
    log(f"关闭全部端口，原因: {reason}")
    send_cmd("ALL_OFF", log_command=False)


def heartbeat_worker() -> None:
    while not stop_event.is_set() and not heartbeat_stop_event.is_set():
        try:
            send_cmd("HEARTBEAT", log_command=False)
        except Exception as exc:
            log(f"心跳线程异常，准备触发安全退出: {exc}")
            stop_event.set()
            run_controller.notify_state_changed()
            break

        for _ in range(int(HEARTBEAT_INTERVAL_SECONDS / SLEEP_STEP_SECONDS)):
            if stop_event.is_set() or heartbeat_stop_event.is_set():
                return
            time.sleep(SLEEP_STEP_SECONDS)


def start_heartbeat() -> None:
    global heartbeat_thread

    if heartbeat_thread and heartbeat_thread.is_alive():
        return

    heartbeat_stop_event.clear()
    heartbeat_thread = threading.Thread(
        target=heartbeat_worker,
        name="heartbeat-worker",
        daemon=True,
    )
    heartbeat_thread.start()
    log(f"已启动心跳线程，发送间隔 {HEARTBEAT_INTERVAL_SECONDS:.1f} 秒。")


def stop_heartbeat() -> None:
    global heartbeat_thread

    if heartbeat_thread and heartbeat_thread.is_alive():
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.5)
        log("心跳线程已停止。")
    else:
        heartbeat_stop_event.set()

    heartbeat_thread = None


def read_ping_response(connection: serial.Serial) -> bool:
    deadline = time.monotonic() + PING_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        raw = connection.readline()
        if not raw:
            continue

        response = raw.decode("utf-8", errors="ignore").strip()
        if not response:
            continue

        log(f"串口返回: {response}")
        if response == "PONG":
            return True
    return False


def scan_esp32() -> serial.Serial | None:
    preferred_port_name = os.environ.get("ESP32_COM_PORT", "").strip()

    if preferred_port_name:
        log(
            f"检测到手动指定串口环境变量 ESP32_COM_PORT={preferred_port_name}，将优先尝试。"
        )

    for round_index in range(1, SCAN_RETRY_ROUNDS + 1):
        log(f"正在扫描 COM 口，第 {round_index}/{SCAN_RETRY_ROUNDS} 轮...")
        raw_ports = list(list_ports.comports())
        ports = order_candidate_ports(raw_ports, preferred_port_name)

        if not ports:
            log("当前未发现任何可用 COM 口。")
        else:
            log("本轮发现串口: " + " ; ".join(format_port_summary(port) for port in ports))

        for port in ports:
            if is_preferred_usb_port(port) and not is_legacy_builtin_port(port):
                port_role = "优先候选 USB 串口"
            elif is_legacy_builtin_port(port):
                port_role = "低优先级内建串口"
            else:
                port_role = "常规候选串口"

            log(f"尝试连接串口: {format_port_summary(port)} [{port_role}]")
            connection: serial.Serial | None = None
            try:
                connection = serial.Serial(
                    port=port.device,
                    baudrate=BAUD_RATE,
                    timeout=SERIAL_TIMEOUT_SECONDS,
                    write_timeout=SERIAL_TIMEOUT_SECONDS,
                    rtscts=False,
                    dsrdtr=False,
                )
                prepare_serial_connection(connection)
                time.sleep(SCAN_STABILIZE_SECONDS)
                connection.write(b"PING\n")
                connection.flush()

                if read_ping_response(connection):
                    log(f"成功识别 ESP32 控制板，连接串口: {port.device}")
                    detected_connection = connection
                    connection = None
                    return detected_connection

                log(f"{port.device} 未返回 PONG，继续扫描。")
            except Exception as exc:
                log(f"{port.device} 识别失败: {exc}")
            finally:
                if connection is not None and connection.is_open:
                    try:
                        release_serial_control_lines(connection)
                        connection.close()
                    except Exception:
                        pass

        if round_index < SCAN_RETRY_ROUNDS:
            log(f"本轮未识别到 ESP32，等待 {SCAN_RETRY_DELAY_SECONDS:.1f} 秒后自动重扫。")
            safe_sleep(SCAN_RETRY_DELAY_SECONDS)

    return None


def run_single_controlled(pin: int, duration: float, label: str) -> None:
    remaining = duration
    first_start = True
    output_active = False

    while remaining > 0:
        wait_until_sequence_running()

        if not output_active:
            all_off(
                f"{label} 阶段开始前清空全部输出"
                if first_start
                else f"{label} 恢复运行前清空全部输出"
            )

            if first_start:
                log(f"启动端口 {label}，GPIO{pin}，计划运行 {duration:.1f} 秒。")
            else:
                log(f"恢复端口 {label}，GPIO{pin}，剩余 {remaining:.1f} 秒。")

            send_cmd(f"ON {pin}", log_command=False)
            output_active = True
            first_start = False

        remaining, signal = wait_active_duration(remaining)
        if signal == ControlSignal.RUN:
            log(f"关闭端口 {label}，GPIO{pin}。")
            send_cmd(f"OFF {pin}", log_command=False)
            return

        if output_active:
            all_off(f"{label} 阶段被中断时关闭全部输出")
            output_active = False

        if signal == ControlSignal.PAUSE:
            log(f"端口 {label} 已暂停，当前位置保留，剩余 {remaining:.1f} 秒。")
            continue
        if signal == ControlSignal.RESTART:
            raise RestartRequested("收到从头重新运行指令。")
        raise ShutdownRequested("检测到退出请求，流程中止。")


def run_parallel_controlled(schedule: Iterable[ParallelStep]) -> None:
    steps = list(schedule)
    if not steps:
        return

    step_by_pin = {step.pin: step for step in steps}
    remaining_seconds = {step.pin: step.duration for step in steps}
    initial_labels = ", ".join(
        f"{step.label}(GPIO{step.pin}, {step.duration:.1f}秒)"
        for step in steps
    )
    first_start = True

    while remaining_seconds:
        wait_until_sequence_running()
        all_off(
            "并行阶段开始前清空全部输出"
            if first_start
            else "并行阶段恢复前清空全部输出"
        )

        if first_start:
            log(f"并行阶段同步启动: {initial_labels}")
            first_start = False
        else:
            remaining_labels = ", ".join(
                f"{step_by_pin[pin].label}(GPIO{pin}, 剩余 {remaining_seconds[pin]:.1f}秒)"
                for pin in remaining_seconds
            )
            log(f"并行阶段恢复运行: {remaining_labels}")

        for pin, remaining in remaining_seconds.items():
            step = step_by_pin[pin]
            send_cmd(f"ON {step.pin}", log_command=False)
            log(f"已启动端口 {step.label}，GPIO{step.pin}，剩余 {remaining:.1f} 秒。")

        last_tick = time.monotonic()
        paused = False

        while remaining_seconds:
            signal = run_controller.current_signal()
            if signal == ControlSignal.PAUSE:
                all_off("并行阶段收到全局暂停指令")
                waiting_labels = ", ".join(
                    f"{step_by_pin[pin].label}(GPIO{pin}, 剩余 {remaining_seconds[pin]:.1f}秒)"
                    for pin in remaining_seconds
                )
                log(f"并行阶段已暂停，当前未完成端口: {waiting_labels}")
                paused = True
                break
            if signal == ControlSignal.RESTART:
                all_off("并行阶段收到从头重新运行指令")
                raise RestartRequested("收到从头重新运行指令。")
            if signal == ControlSignal.STOP:
                raise ShutdownRequested("检测到退出请求，流程中止。")

            next_due_seconds = min(remaining_seconds.values())
            sleep_seconds = min(
                SLEEP_STEP_SECONDS,
                next_due_seconds if next_due_seconds > 0 else SLEEP_STEP_SECONDS,
            )
            time.sleep(sleep_seconds)

            now = time.monotonic()
            elapsed = now - last_tick
            last_tick = now

            due_pins: list[int] = []
            for pin in list(remaining_seconds):
                remaining_seconds[pin] = max(0.0, remaining_seconds[pin] - elapsed)
                if remaining_seconds[pin] <= 1e-9:
                    due_pins.append(pin)

            for pin in due_pins:
                step = step_by_pin[pin]
                send_cmd(f"OFF {step.pin}", log_command=False)
                log(f"到时关闭端口 {step.label}，GPIO{step.pin}。")
                remaining_seconds.pop(pin, None)

        if paused:
            continue

    max_duration = max(step.duration for step in steps)
    log(f"并行阶段全部结束，最长端口运行 {max_duration:.1f} 秒已完成。")


def run_pauseable_delay(
    stage_name: str,
    duration: float,
    *,
    enter_with_all_off: bool,
) -> None:
    remaining = duration
    first_start = True

    while remaining > 0:
        wait_until_sequence_running()

        if enter_with_all_off:
            all_off(
                f"{stage_name} 开始前确保全部关闭"
                if first_start
                else f"{stage_name} 恢复前确保全部关闭"
            )

        if first_start:
            log(f"{stage_name}，持续 {duration:.1f} 秒。")
            first_start = False
        else:
            log(f"{stage_name} 恢复运行，剩余 {remaining:.1f} 秒。")

        remaining, signal = wait_active_duration(remaining)
        if signal == ControlSignal.RUN:
            return
        if signal == ControlSignal.PAUSE:
            log(f"{stage_name} 已暂停，当前位置保留，剩余 {remaining:.1f} 秒。")
            continue
        if signal == ControlSignal.RESTART:
            if enter_with_all_off:
                all_off(f"{stage_name} 收到从头重新运行指令")
            raise RestartRequested("收到从头重新运行指令。")
        raise ShutdownRequested("检测到退出请求，流程中止。")


def run_sequence_controlled() -> None:
    log("阶段 0：连接成功后的静默等待开始，持续 5 秒，期间不发送任何控制指令。")
    run_pauseable_delay(
        "阶段 0 连接成功后静默等待",
        INITIAL_SILENT_SECONDS,
        enter_with_all_off=False,
    )

    log("阶段 0 完成，开始发送 HEARTBEAT。")
    start_heartbeat()

    log("阶段 1：单独启动 L2D。")
    run_single_controlled(PIN_MAP["L2D"], 5.0, "L2D")

    log("阶段 2：全局静止间隔。")
    run_pauseable_delay("阶段 2 全局静止间隔", 2.0, enter_with_all_off=True)

    log("阶段 3：五个端口同步启动，各自独立计时。")
    run_parallel_controlled(
        [
            ParallelStep("L2A", PIN_MAP["L2A"], 10.0),
            ParallelStep("L2B", PIN_MAP["L2B"], 6.0),
            ParallelStep("L2E", PIN_MAP["L2E"], 8.0),
            ParallelStep("L2F", PIN_MAP["L2F"], 5.0),
            ParallelStep("L2D", PIN_MAP["L2D"], 2.0),
        ]
    )

    log("阶段 4：全局静止间隔。")
    run_pauseable_delay("阶段 4 全局静止间隔", 2.0, enter_with_all_off=True)

    log("阶段 5：单独启动 L3B。")
    run_single_controlled(PIN_MAP["L3B"], 15.0, "L3B")

    log("阶段 6：全局静止间隔。")
    run_pauseable_delay("阶段 6 全局静止间隔", 2.0, enter_with_all_off=True)

    log("阶段 7：五个端口同步运行 20 秒。")
    run_parallel_controlled(
        [
            ParallelStep("L4A", PIN_MAP["L4A"], 20.0),
            ParallelStep("L4B", PIN_MAP["L4B"], 20.0),
            ParallelStep("L4C", PIN_MAP["L4C"], 20.0),
            ParallelStep("L4D", PIN_MAP["L4D"], 20.0),
            ParallelStep("L1B", PIN_MAP["L1B"], 20.0),
        ]
    )

    log("阶段 8：全局静止间隔。")
    run_pauseable_delay("阶段 8 全局静止间隔", 2.0, enter_with_all_off=True)

    log("阶段 9：单独启动 L3A。")
    run_single_controlled(PIN_MAP["L3A"], 15.0, "L3A")

    log("阶段 10：全局静止间隔。")
    run_pauseable_delay("阶段 10 全局静止间隔", 2.0, enter_with_all_off=True)

    log("阶段 11：单独启动 L1B。")
    run_single_controlled(PIN_MAP["L1B"], 15.0, "L1B")

    log("阶段 12：流程结束后的安全静默状态。")
    all_off("阶段 12 结束动作")
    stop_heartbeat()
    log("预设流程已全部执行完毕，所有设备已关闭，系统进入静默静止状态。")


def run_sequence_loop() -> None:
    log("程序已就绪，等待命令 2 开始/继续，5 暂停，8 从头重新运行。")

    while not stop_event.is_set():
        signal = run_controller.wait_until_running()
        if signal == ControlSignal.STOP:
            raise ShutdownRequested("检测到退出请求，流程中止。")

        if signal == ControlSignal.RESTART:
            run_controller.consume_restart_request()
            stop_heartbeat()
            best_effort_runtime_all_off("全局重新运行前清空全部输出")
            log("将从流程起点重新开始运行。")

        try:
            run_sequence_controlled()
            run_controller.mark_sequence_completed()
            log("预设流程已执行完毕，使用命令 8 可从头再次运行。")
        except RestartRequested:
            run_controller.consume_restart_request()
            stop_heartbeat()
            best_effort_runtime_all_off("全局重新运行前清空全部输出")
            log("已切换到从头重新运行状态，流程将重新开始。")
            continue


def cleanup_and_exit(reason: str = "程序退出") -> None:
    global cleanup_done, serial_conn, launcher_watchdog_thread, controller_phase, instance_handle, cleanup_owner

    with cleanup_lock:
        if cleanup_done:
            if cleanup_owner != threading.get_ident():
                cleanup_complete.wait(timeout=6)
            return
        cleanup_done = True
        cleanup_owner = threading.get_ident()

    stop_event.set()
    heartbeat_stop_event.set()
    run_controller.notify_state_changed()

    try:
        stop_global_hotkey_listener()
    except Exception:
        pass

    try:
        stop_heartbeat()
    except Exception:
        pass

    try:
        best_effort_all_off(reason)
    finally:
        if serial_conn is not None and serial_conn.is_open:
            try:
                release_serial_control_lines(serial_conn)
                serial_conn.close()
            except Exception:
                pass
        serial_conn = None
        launcher_watchdog_thread = None
        controller_phase = "stopped"
        try:
            publish_runtime_status()
        except OSError:
            pass
        finally:
            if instance_handle is not None:
                if os.name == "nt":
                    ctypes.windll.kernel32.CloseHandle(instance_handle)
                else:
                    instance_handle.close()
                instance_handle = None
            cleanup_complete.set()


def console_ctrl_handler(ctrl_type: int) -> bool:
    event_names = {
        0: "CTRL_C_EVENT",
        1: "CTRL_BREAK_EVENT",
        2: "CTRL_CLOSE_EVENT",
        5: "CTRL_LOGOFF_EVENT",
        6: "CTRL_SHUTDOWN_EVENT",
    }
    log(f"收到控制台关闭事件: {event_names.get(ctrl_type, str(ctrl_type))}")
    cleanup_and_exit("控制台关闭事件")
    return False


def register_console_handler() -> None:
    if win32api is None:
        log("未导入 win32api，控制台关闭事件捕获将不可用。")
        return

    win32api.SetConsoleCtrlHandler(console_ctrl_handler, True)
    log("已注册 Windows 控制台关闭事件处理器。")


def keep_window_alive(message: str) -> None:
    log(message)
    log("脚本保持可见状态，可直接关闭窗口，或按 Ctrl+C 结束。")
    while not stop_event.is_set():
        time.sleep(0.2)


def print_mapping_table() -> None:
    log("GPIO 映射表如下：")
    for label, pin in sorted(PIN_MAP.items(), key=lambda item: item[1]):
        log(f"  {label} -> GPIO{pin}")
    log(
        "输出逻辑: 高电平启动 / 低电平关闭"
        if OUTPUT_ACTIVE_HIGH
        else "输出逻辑: 低电平启动 / 高电平关闭"
    )
    log("GPIO21(PWM) 本轮不主动启动，但始终纳入 ALL_OFF 全局关闭逻辑。")


def main() -> int:
    global serial_conn, controller_phase

    if not claim_controller_instance():
        log("ESP32 控制程序已在运行；请使用已有窗口控制。")
        return 0
    STOP_FILE.unlink(missing_ok=True)

    register_console_handler()
    if os.name != "nt":
        def request_stop(_signum, _frame):
            stop_event.set()
            run_controller.notify_state_changed()
        signal.signal(signal.SIGTERM, request_stop)
    atexit.register(cleanup_and_exit, "atexit 退出清理")
    start_launcher_watchdog()
    start_global_hotkey_listener()
    threading.Thread(target=runtime_status_worker, name="runtime-status", daemon=True).start()

    print_mapping_table()
    try:
        controller_phase = "connecting"
        while serial_conn is None and not stop_event.is_set():
            serial_conn = scan_esp32()
            if serial_conn is None:
                log("尚未连接 ESP32，将继续重试；插入设备后无需重新启动程序。")
                safe_sleep(2)
        check_stop_requested()
        controller_phase = "ready"
        publish_runtime_status()
        log(f"当前已连接 ESP32 控制板串口: {serial_conn.port}")
        run_sequence_loop()
        return 0
    except ShutdownRequested as exc:
        log(str(exc))
        return 0
    except KeyboardInterrupt:
        log("检测到 Ctrl+C，中止流程并准备安全关闭。")
        return 0
    except Exception as exc:
        log(f"运行异常: {exc}")
        return 1
    finally:
        cleanup_and_exit("main finally 退出清理")


if __name__ == "__main__":
    raise SystemExit(main())
