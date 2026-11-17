from __future__ import annotations

import os
from pathlib import Path


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT_DIR.parent
SHARED_ASSETS_DIR = PROJECT_ROOT / "shared_assets"
SHARED_MODELS_DIR = SHARED_ASSETS_DIR / "models"
SHARED_TEST_IMAGES_DIR = SHARED_ASSETS_DIR / "test_images"
SHARED_DOCUMENTS_DIR = SHARED_ASSETS_DIR / "documents"
CURRENT_MODELS_DIR = ROOT_DIR / "models"
LOG_DIR = Path(os.getenv("EMOTION_LOG_DIR", str(ROOT_DIR / "logs")))

HOST = os.getenv("EMOTION_APP_HOST", "127.0.0.1")
PORT = int(os.getenv("EMOTION_APP_PORT", "5050"))
AUTO_OPEN_BROWSER = _env_flag("EMOTION_AUTO_OPEN_BROWSER", True)

ACTIVATION_WINDOW_SECONDS = 10.0
ACTIVATION_REQUIRED_FACE_SECONDS = 6.0
ACTIVATION_PROGRESS_SHOW_SECONDS = 4.0
FACE_LOST_TOLERANCE_S = 1.2
BEST_FRAME_SAMPLING_S = 5.0
ACTIVATING_DURATION_MS = 5000
CAPTURE_FLASH_DURATION_MS = 1000
FACE_SCANNING_DURATION_MS = 5000
ANCHORED_TRANSITION_DURATION_MS = 9000
HIGH_QUALITY_SCORE_THRESHOLD = 0.68

FRAME_POST_INTERVAL_MS = 200
STATE_POLL_INTERVAL_MS = 220
CAMERA_FRAME_WIDTH = 960
CAMERA_FRAME_HEIGHT = 540

LOG_DIR.mkdir(parents=True, exist_ok=True)
SHARED_MODELS_DIR.mkdir(parents=True, exist_ok=True)
SHARED_TEST_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
SHARED_DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
