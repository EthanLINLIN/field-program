from __future__ import annotations

import io
import logging
from logging.handlers import RotatingFileHandler
import os
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file


FACIAL_EMOTION_ROOT = Path(__file__).resolve().parent.parent
if str(FACIAL_EMOTION_ROOT) not in sys.path:
    sys.path.insert(0, str(FACIAL_EMOTION_ROOT))

from modules.config import (
    ACTIVATING_DURATION_MS,
    ACTIVATION_PROGRESS_SHOW_SECONDS,
    ACTIVATION_REQUIRED_FACE_SECONDS,
    ACTIVATION_WINDOW_SECONDS,
    ANCHORED_TRANSITION_DURATION_MS,
    AUTO_OPEN_BROWSER,
    BEST_FRAME_SAMPLING_S,
    CAMERA_FRAME_HEIGHT,
    CAMERA_FRAME_WIDTH,
    CAPTURE_FLASH_DURATION_MS,
    FACE_SCANNING_DURATION_MS,
    FRAME_POST_INTERVAL_MS,
    HOST,
    LOG_DIR,
    PORT,
    SHARED_ASSETS_DIR,
    STATE_POLL_INTERVAL_MS,
)
from field_emotion_bridge import get_field_emotion_result
from modules.emotion_recognizer import EmotionRecognizer
from modules.face_pipeline import FaceDetector
from modules.model_assets import ensure_required_models
from modules.system_controller import SystemController


def configure_logging() -> None:
    log_path = LOG_DIR / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


configure_logging()
LOGGER = logging.getLogger(__name__)

model_paths = ensure_required_models(LOGGER)
face_detector = FaceDetector(str(model_paths["yunet_face_detector"]))
emotion_recognizer = EmotionRecognizer(str(model_paths["emotion_ferplus"]))
controller = SystemController(face_detector, emotion_recognizer)

app = Flask(__name__, template_folder="templates", static_folder="static")
FIELD_UI_BRIDGE_PATH = (Path(__file__).resolve().parent.parent / "field_ui_bridge.js").resolve()


@app.after_request
def add_field_ui_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/")
def index() -> str:
    opening_ui_asset_path = "shared_assets/ui_reference/opening_ui_reference_02.png"
    opening_ui_asset = SHARED_ASSETS_DIR / "ui_reference" / "opening_ui_reference_02.png"
    opening_ui_asset_note: str | None = None

    if not opening_ui_asset.is_file():
        opening_ui_asset_note = "opening_ui_reference_02.png not found"
        LOGGER.warning(opening_ui_asset_note)

    return render_template(
        "index.html",
        ui_config={
            "statePollIntervalMs": STATE_POLL_INTERVAL_MS,
            "framePostIntervalMs": FRAME_POST_INTERVAL_MS,
            "cameraFrameWidth": CAMERA_FRAME_WIDTH,
            "cameraFrameHeight": CAMERA_FRAME_HEIGHT,
            "bestFrameSamplingSeconds": BEST_FRAME_SAMPLING_S,
            "activatingDurationMs": ACTIVATING_DURATION_MS,
            "captureFlashDurationMs": CAPTURE_FLASH_DURATION_MS,
            "faceScanningDurationMs": FACE_SCANNING_DURATION_MS,
            "anchoredTransitionDurationMs": ANCHORED_TRANSITION_DURATION_MS,
            "activationWindowSeconds": ACTIVATION_WINDOW_SECONDS,
            "activationRequiredFaceSeconds": ACTIVATION_REQUIRED_FACE_SECONDS,
            "activationProgressShowSeconds": ACTIVATION_PROGRESS_SHOW_SECONDS,
            "openingUiAssetUrl": f"/{opening_ui_asset_path}",
            "openingUiAssetPath": opening_ui_asset_path,
            "openingUiAssetNote": opening_ui_asset_note,
            "openingUiReferenceId": "opening_ui_reference_02",
            "openingUiAnimationEnabled": True,
            "openingUiMode": "reference_02_static_local_overlays",
            "openingUiBackgroundStatic": True,
            "openingUiLocalMasking": True,
            "openingUiPromptPulse": True,
            "openingUiSensorLabelPulse": True,
            "openingUiFieldDotMotion": True,
            "openingUiWelcomeDotMotion": True,
        },
    )


@app.route("/field_ui_bridge.js", methods=["GET"])
def field_ui_bridge_asset():
    if not FIELD_UI_BRIDGE_PATH.is_file():
        abort(404)
    return send_file(FIELD_UI_BRIDGE_PATH, mimetype="application/javascript", max_age=0)


@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(controller.get_state_snapshot())


@app.route("/api/field-emotion/latest", methods=["GET"])
def api_field_emotion_latest():
    return jsonify(get_field_emotion_result())


@app.route("/api/reset", methods=["POST"])
def api_reset():
    return jsonify(controller.reset_system())


@app.route("/api/start", methods=["POST"])
def api_start():
    return jsonify(controller.start_interaction())


@app.route("/api/camera-status", methods=["POST"])
def api_camera_status():
    payload = request.get_json(silent=True) or {}
    camera_active = bool(payload.get("camera_active", False))
    camera_permission_status = str(payload.get("camera_permission_status", "idle"))
    error = payload.get("error")
    if error is not None:
        error = str(error)
    return jsonify(
        controller.update_camera_status(
            camera_active=camera_active,
            camera_permission_status=camera_permission_status,
            error=error,
        )
    )


@app.route("/api/frame", methods=["POST"])
def api_frame():
    file_storage = request.files.get("frame")
    if file_storage is None:
        return jsonify({"ok": False, "error": "missing frame"}), 400

    timestamp_ms_raw = request.form.get("timestamp_ms")
    timestamp_ms = int(timestamp_ms_raw) if timestamp_ms_raw else None
    try:
        snapshot = controller.process_frame(file_storage.read(), timestamp_ms)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Frame processing failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "snapshot": snapshot})


@app.route("/api/selected-frame", methods=["GET"])
def api_selected_frame():
    frame_bytes = controller.get_selected_frame_bytes()
    if frame_bytes is None:
        return jsonify({"ok": False, "error": "no selected frame"}), 404
    return send_file(io.BytesIO(frame_bytes), mimetype="image/jpeg", max_age=0)


@app.route("/shared_assets/ui_reference/<path:filename>", methods=["GET"])
def shared_ui_reference_asset(filename: str):
    ui_reference_root = (SHARED_ASSETS_DIR / "ui_reference").resolve()
    requested_path = (ui_reference_root / filename).resolve()

    if ui_reference_root not in requested_path.parents and requested_path != ui_reference_root:
        abort(404)
    if not requested_path.is_file():
        abort(404)

    return send_file(Path(requested_path), max_age=0)


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return ("", 204)


def maybe_open_browser() -> None:
    if not AUTO_OPEN_BROWSER:
        return
    if os.getenv("NO_BROWSER") == "1":
        return

    def _open() -> None:
        candidates = [
            Path(os.getenv("FIREFOX_EXE", "C:/Program Files/Mozilla Firefox/firefox.exe")),
            Path("C:/Program Files (x86)/Mozilla Firefox/firefox.exe"),
        ]
        for browser in candidates:
            if browser.is_file():
                subprocess.Popen([str(browser), "-new-tab", f"http://{HOST}:{PORT}"])
                return
        logging.getLogger(__name__).error("Firefox was not found. Open http://%s:%s in Firefox.", HOST, PORT)

    threading.Timer(1.4, _open).start()


if __name__ == "__main__":
    maybe_open_browser()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)
