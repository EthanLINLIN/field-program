from __future__ import annotations

import logging
import hashlib
import os
import shutil
import urllib.request
from pathlib import Path

from .config import SHARED_MODELS_DIR


LOGGER = logging.getLogger(__name__)


MODEL_SPECS = {
    "yunet_face_detector": {
        "filename": "face_detection_yunet_2023mar.onnx",
        "subdir": "opencv",
        "urls": [
            "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx?download=true",
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        ],
    },
    "emotion_ferplus": {
        "filename": "emotion-ferplus-12-int8.onnx",
        "subdir": "onnx",
        "sha256": "3e47195d79e9593294df9e81a6d296a1e10969b68a717284081c29493a0ff5f1",
        "urls": [
            "https://huggingface.co/onnxmodelzoo/emotion-ferplus-12-int8/resolve/26e8ef3e99684a3928558f78f7a8f4d17b26b0de/emotion-ferplus-12-int8.onnx?download=true",
        ],
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_with_fallback(urls: list[str], target_path: Path, logger: logging.Logger | None = None, expected_sha256: str | None = None) -> None:
    active_logger = logger or LOGGER
    target_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for url in urls:
        temp_path = target_path.with_suffix(target_path.suffix + ".part")
        try:
            active_logger.info("Downloading model asset from %s", url)
            with urllib.request.urlopen(url, timeout=90) as response, temp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            if expected_sha256 and _sha256(temp_path) != expected_sha256:
                raise ValueError("Downloaded model SHA-256 does not match the pinned upstream file")
            temp_path.replace(target_path)
            active_logger.info("Saved model asset to %s", target_path)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            active_logger.warning("Failed to download %s: %s", url, exc)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    raise RuntimeError(f"Unable to download required model asset: {target_path.name}") from last_error


def ensure_required_models(logger: logging.Logger | None = None) -> dict[str, Path]:
    active_logger = logger or LOGGER
    resolved_paths: dict[str, Path] = {}

    for model_name, spec in MODEL_SPECS.items():
        if model_name == "emotion_ferplus":
            default_cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) if os.name == "nt" else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            cache = Path(os.environ.get("FERPLUS_CACHE_DIR", default_cache / "field-emotion" / "models")).expanduser()
            target_path = cache / spec["filename"]
        else:
            target_path = SHARED_MODELS_DIR / spec["subdir"] / spec["filename"]
        expected_sha256 = spec.get("sha256")
        if target_path.exists() and expected_sha256 and _sha256(target_path) != expected_sha256:
            raise RuntimeError(f"Model checksum mismatch: {target_path}. Remove the file and retry, or provide the verified upstream model.")
        if not target_path.exists():
            _download_with_fallback(spec["urls"], target_path, active_logger, expected_sha256)
        resolved_paths[model_name] = target_path

    return resolved_paths
