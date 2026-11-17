from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from field_emotion_bridge import (
    reset_field_emotion_state,
    set_proceed_to_core_area,
    set_recognition_started,
    update_field_emotion_result,
)
from .config import (
    ACTIVATING_DURATION_MS,
    ACTIVATION_REQUIRED_FACE_SECONDS,
    ACTIVATION_WINDOW_SECONDS,
    ANCHORED_TRANSITION_DURATION_MS,
    BEST_FRAME_SAMPLING_S,
    CAMERA_FRAME_HEIGHT,
    CAMERA_FRAME_WIDTH,
    CAPTURE_FLASH_DURATION_MS,
    FACE_LOST_TOLERANCE_S,
    FACE_SCANNING_DURATION_MS,
    HIGH_QUALITY_SCORE_THRESHOLD,
)
from .emotion_recognizer import EmotionRecognizer
from .face_pipeline import (
    DetectedFace,
    FaceDetector,
    decode_image_bytes,
    encode_frame_to_jpeg,
    extract_face_crop,
    score_frame_candidate,
    select_primary_face,
)


LOGGER = logging.getLogger(__name__)

FRAME_PROCESSING_STATES = {
    "idle_black",
    "activating",
    "best_frame_selecting",
}

FINAL_DISPLAY_STATES = {
    "capture_flash",
    "face_scanning",
    "anchored_message",
}

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEXT_F_RUNTIME_DIR = PROJECT_ROOT / "display" / "assets" / "runtime"
TEXT_F_PORTRAIT_PATH = TEXT_F_RUNTIME_DIR / "portrait_current.jpg"
TEXT_F_PORTRAIT_RELATIVE_SRC = "assets/runtime/portrait_current.jpg"


def ensure_text_f_runtime_dir() -> None:
    TEXT_F_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def save_latest_portrait_image(frame, face: DetectedFace | None = None) -> dict[str, Any] | None:
    if frame is None:
        return None

    ensure_text_f_runtime_dir()

    crop = frame
    if face is not None:
        try:
            crop = extract_face_crop(frame, face, padding=0.35)
        except Exception:  # noqa: BLE001
            crop = frame

    portrait_bytes = encode_frame_to_jpeg(crop, quality=92)
    portrait_timestamp = int(time.time() * 1000)
    temp_path = TEXT_F_RUNTIME_DIR / f".portrait_current_{portrait_timestamp}.tmp"
    temp_path.write_bytes(portrait_bytes)
    temp_path.replace(TEXT_F_PORTRAIT_PATH)
    return {
        "portraitSrc": TEXT_F_PORTRAIT_RELATIVE_SRC,
        "portraitTimestamp": portrait_timestamp,
    }


def _default_emotion_payload() -> dict[str, Any]:
    return {
        "dominant_emotion": "neutral",
        "compound_emotion": {
            "happy": 0.0,
            "surprise": 0.0,
            "angry": 0.0,
            "neutral": 1.0,
            "sad": 0.0,
            "fear": 0.0,
            "disgust": 0.0,
        },
        "emotion_fluctuation": 0.0,
    }


class SystemController:
    def __init__(self, face_detector: FaceDetector, emotion_recognizer: EmotionRecognizer) -> None:
        self.face_detector = face_detector
        self.emotion_recognizer = emotion_recognizer
        self._lock = threading.RLock()
        self._track_sequence = 0
        self._reset_internal_state()

    def _reset_internal_state(self) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self._track_sequence = 0
        self.state = "idle_black_waiting_eeg"
        self.eeg_ready = False
        self.eeg_source: str | None = None
        self.interaction_started = False
        self.camera_active = False
        self.camera_permission_status = "idle"
        self.face_detected: bool | None = None
        self.primary_face: DetectedFace | None = None
        self.tracked_face: DetectedFace | None = None
        self.tracked_face_id: str | None = None
        self.tracked_face_status = "waiting_for_start_key"
        self.face_presence_seconds = 0.0
        self.stable_face_seconds = 0.0
        self.activation_face_seconds = 0.0
        self.activation_progress = 0.0
        self.remaining_activation_seconds = ACTIVATION_REQUIRED_FACE_SECONDS
        self.detection_samples: list[dict[str, Any]] = []
        self.frame_interval_estimate = 0.20
        self.face_missing_since: float | None = None
        self.last_tracked_seen_at: float | None = None
        self.activating_started_at: float | None = None
        self.sampling_started_at: float | None = None
        self.anchored_started_at: float | None = None
        self.best_candidate: dict[str, Any] | None = None
        self.selected_best_frame_jpeg: bytes | None = None
        self.selected_best_face: DetectedFace | None = None
        self.selected_best_frame_metadata: dict[str, Any] | None = None
        self.portrait_src = ""
        self.portrait_timestamp = 0
        self.emotion_result: dict[str, Any] = _default_emotion_payload()
        self.system_status = "waiting_for_start_trigger"
        self.warning: str | None = None
        self.error: str | None = None
        self.last_frame_at: float | None = None
        self.fps = 0.0
        self.timestamp_ms = int(time.time() * 1000)
        reset_field_emotion_state()

    def reset_system(self) -> dict[str, Any]:
        with self._lock:
            self._reset_internal_state()
            LOGGER.info("System reset to idle_black_waiting_eeg. run_id=%s", self.run_id)
            return self.get_state_snapshot()

    def start_interaction(self) -> dict[str, Any]:
        with self._lock:
            if self.camera_active and self.camera_permission_status in {"requesting", "granted"}:
                return self.get_state_snapshot()
            if self.state in FINAL_DISPLAY_STATES and self.selected_best_frame_jpeg is not None:
                return self.get_state_snapshot()

            self.eeg_ready = True
            self.eeg_source = "numpad_mock"
            self.interaction_started = True
            self.camera_active = True
            self.camera_permission_status = "requesting"
            self.state = "idle_black"
            self.face_detected = False
            self.primary_face = None
            self.tracked_face = None
            self.tracked_face_id = None
            self.tracked_face_status = "waiting_for_camera_permission"
            self.face_presence_seconds = 0.0
            self.stable_face_seconds = 0.0
            self.activation_face_seconds = 0.0
            self.activation_progress = 0.0
            self.remaining_activation_seconds = ACTIVATION_REQUIRED_FACE_SECONDS
            self.detection_samples = []
            self.face_missing_since = None
            self.last_tracked_seen_at = None
            self.activating_started_at = None
            self.sampling_started_at = None
            self.anchored_started_at = None
            self.best_candidate = None
            self.selected_best_frame_jpeg = None
            self.selected_best_face = None
            self.selected_best_frame_metadata = None
            self.portrait_src = ""
            self.portrait_timestamp = 0
            self.emotion_result = _default_emotion_payload()
            self.warning = None
            self.error = None
            self.last_frame_at = None
            self.fps = 0.0
            self.timestamp_ms = int(time.time() * 1000)
            self.system_status = "requesting_camera_permission"
            set_recognition_started(True)
            LOGGER.info("Interaction start requested. run_id=%s", self.run_id)
            return self.get_state_snapshot()

    def update_camera_status(
        self,
        *,
        camera_active: bool,
        camera_permission_status: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.timestamp_ms = int(time.time() * 1000)
            self.camera_active = camera_active
            self.camera_permission_status = camera_permission_status

            if camera_active and camera_permission_status == "granted":
                self.state = "idle_black" if self.state == "idle_black_waiting_eeg" else self.state
                self.face_detected = False
                self.error = None
                self.system_status = "waiting_for_face_activation"
                if self.tracked_face_status in {"waiting_for_start_key", "waiting_for_camera_permission", "camera_error"}:
                    self.tracked_face_status = "waiting_for_face"
                LOGGER.info("Camera granted and active. run_id=%s", self.run_id)
                return self.get_state_snapshot()

            self.camera_active = False
            self.face_detected = None
            self.primary_face = None
            self.tracked_face = None
            self.tracked_face_id = None
            self.face_missing_since = None
            self.last_tracked_seen_at = None
            self.last_frame_at = None
            self.fps = 0.0

            if error:
                self.error = error

            if camera_permission_status in {"denied", "error"}:
                self.tracked_face_status = "camera_error"
                self.system_status = "camera_error"
                LOGGER.warning("Camera failed to start. run_id=%s error=%s", self.run_id, error)
            elif camera_permission_status == "stopped":
                self.tracked_face_status = "camera_stopped"
                if self.state in FINAL_DISPLAY_STATES:
                    self.system_status = "selected_best_frame_locked"
                elif self.interaction_started:
                    self.system_status = "camera_stopped"
            return self.get_state_snapshot()

    def process_frame(self, image_bytes: bytes, client_timestamp_ms: int | None = None) -> dict[str, Any]:
        with self._lock:
            self.timestamp_ms = client_timestamp_ms or int(time.time() * 1000)
            if not self._should_process_frames():
                return self.get_state_snapshot()

        frame = decode_image_bytes(image_bytes)
        faces = self.face_detector.detect_faces(frame)
        primary_face = select_primary_face(faces, frame.shape)
        now = time.monotonic()

        with self._lock:
            self.timestamp_ms = client_timestamp_ms or int(time.time() * 1000)
            if not self._should_process_frames():
                return self.get_state_snapshot()

            self._update_fps(now)
            self.primary_face = primary_face
            self.face_detected = primary_face is not None

            if primary_face is None:
                update_field_emotion_result({}, has_face=False)
                set_recognition_started(True)

            if self.state == "idle_black":
                self.system_status = "waiting_for_face_activation"
                self._update_detection_window(now, self.face_detected)
                self._update_tracking(now, primary_face)
                if self.activation_face_seconds >= ACTIVATION_REQUIRED_FACE_SECONDS:
                    self.state = "activating"
                    self.activating_started_at = now
                    self.system_status = "activating"
                    LOGGER.info(
                        "Entered activating for run_id=%s track_id=%s activation_face_seconds=%.3f",
                        self.run_id,
                        self.tracked_face_id,
                        self.activation_face_seconds,
                    )

            if self.state == "activating":
                self.system_status = "activating"
                self._update_tracking(now, primary_face)
                if self.activating_started_at is not None and (now - self.activating_started_at) * 1000.0 >= ACTIVATING_DURATION_MS:
                    self.state = "best_frame_selecting"
                    self.sampling_started_at = now
                    self.best_candidate = None
                    self.warning = None
                    self.system_status = "sampling_best_frame"
                    LOGGER.info("Entered best_frame_selecting for run_id=%s", self.run_id)

            if self.state == "best_frame_selecting":
                self.system_status = "sampling_best_frame"
                if primary_face is not None:
                    self._consider_best_candidate(frame, primary_face, now)

            if (
                self.state == "best_frame_selecting"
                and self.sampling_started_at is not None
                and (now - self.sampling_started_at) >= BEST_FRAME_SAMPLING_S
            ):
                self._lock_best_frame()

            return self.get_state_snapshot()

    def get_selected_frame_bytes(self) -> bytes | None:
        with self._lock:
            return self.selected_best_frame_jpeg

    def get_state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            sampling_seconds = 0.0
            if self.state == "best_frame_selecting" and self.sampling_started_at is not None:
                sampling_seconds = min(time.monotonic() - self.sampling_started_at, BEST_FRAME_SAMPLING_S)

            payload = dict(self.emotion_result)
            primary_face_bbox = self._serialize_face_bbox(self.primary_face)
            selected_best_face_bbox = self._serialize_face_bbox(self.selected_best_face)
            return {
                "timestamp": self.timestamp_ms,
                "run_id": self.run_id,
                "state": self.state,
                "eeg_ready": self.eeg_ready,
                "eeg_source": self.eeg_source,
                "interaction_started": self.interaction_started,
                "camera_active": self.camera_active,
                "camera_permission_status": self.camera_permission_status,
                "face_detected": self.face_detected,
                "activation_window_seconds": ACTIVATION_WINDOW_SECONDS,
                "activation_required_face_seconds": ACTIVATION_REQUIRED_FACE_SECONDS,
                "activation_face_seconds": round(self.activation_face_seconds, 3),
                "activation_progress": round(self.activation_progress, 6),
                "remaining_activation_seconds": round(self.remaining_activation_seconds, 3),
                "stable_face_seconds": round(self.stable_face_seconds, 3),
                "sampling_seconds": round(sampling_seconds, 3),
                "best_frame_locked": self.selected_best_frame_jpeg is not None,
                "selected_best_frame_available": self.selected_best_frame_jpeg is not None,
                "selected_best_frame_url": "/api/selected-frame" if self.selected_best_frame_jpeg else None,
                "selected_best_frame_metadata": self.selected_best_frame_metadata,
                "portraitSrc": self.portrait_src,
                "portraitTimestamp": self.portrait_timestamp,
                "primary_face_bbox": primary_face_bbox,
                "selected_best_face_bbox": selected_best_face_bbox,
                "dominant_emotion": payload["dominant_emotion"],
                "compound_emotion": payload["compound_emotion"],
                "emotion_fluctuation": payload["emotion_fluctuation"],
                "display_orientation": "portrait",
                "display_aspect_ratio": "9:16",
                "video_fit_mode": "cover",
                "system_status": self.system_status,
                "fps": round(self.fps, 2),
                "error": self.error,
                "warning": self.warning,
                "tracked_face_id": self.tracked_face_id,
                "tracked_face_status": self.tracked_face_status,
                "face_presence_seconds": round(self.face_presence_seconds, 3),
                "face_lost_tolerance_seconds": FACE_LOST_TOLERANCE_S,
                "camera_frame_width": CAMERA_FRAME_WIDTH,
                "camera_frame_height": CAMERA_FRAME_HEIGHT,
                "activating_duration_ms": ACTIVATING_DURATION_MS,
                "capture_flash_duration_ms": CAPTURE_FLASH_DURATION_MS,
                "face_scanning_duration_ms": FACE_SCANNING_DURATION_MS,
                "anchored_transition_duration_ms": ANCHORED_TRANSITION_DURATION_MS,
            }

    @staticmethod
    def _serialize_face_bbox(face: DetectedFace | None) -> dict[str, int] | None:
        if face is None:
            return None
        x, y, w, h = face.bbox
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}

    def _should_process_frames(self) -> bool:
        return (
            self.interaction_started
            and self.camera_active
            and self.camera_permission_status == "granted"
            and self.state in FRAME_PROCESSING_STATES
        )

    def _update_fps(self, now: float) -> None:
        if self.last_frame_at is not None:
            delta = now - self.last_frame_at
            if 0.01 <= delta <= 1.0:
                current_fps = 1.0 / delta
                self.fps = current_fps if self.fps == 0.0 else (0.82 * self.fps + 0.18 * current_fps)
                self.frame_interval_estimate = 0.72 * self.frame_interval_estimate + 0.28 * delta
        self.last_frame_at = now

    def _update_detection_window(self, now: float, detected: bool) -> None:
        self.detection_samples.append({"timestamp": now, "detected": detected})
        threshold = now - ACTIVATION_WINDOW_SECONDS
        self.detection_samples = [sample for sample in self.detection_samples if sample["timestamp"] >= threshold]

        if not self.detection_samples:
            self.activation_face_seconds = 0.0
        elif len(self.detection_samples) == 1:
            self.activation_face_seconds = self.frame_interval_estimate if self.detection_samples[0]["detected"] else 0.0
        else:
            total = 0.0
            for index in range(1, len(self.detection_samples)):
                previous = self.detection_samples[index - 1]
                current = self.detection_samples[index]
                delta = min(max(current["timestamp"] - previous["timestamp"], 0.0), 0.5)
                if previous["detected"]:
                    total += delta
            if self.detection_samples[-1]["detected"]:
                total += min(self.frame_interval_estimate, 0.5)
            self.activation_face_seconds = total

        self.activation_face_seconds = min(self.activation_face_seconds, ACTIVATION_WINDOW_SECONDS)
        self.activation_progress = min(self.activation_face_seconds / ACTIVATION_REQUIRED_FACE_SECONDS, 1.0)
        self.remaining_activation_seconds = max(0.0, ACTIVATION_REQUIRED_FACE_SECONDS - self.activation_face_seconds)
        self.stable_face_seconds = self.activation_face_seconds
        self.face_presence_seconds = self.activation_face_seconds

    def _update_tracking(self, now: float, primary_face: DetectedFace | None) -> None:
        if primary_face is None:
            if self.tracked_face is None:
                self.tracked_face_status = "no_face"
                return

            if self.face_missing_since is None:
                self.face_missing_since = now
            lost_elapsed = now - (self.last_tracked_seen_at or now)
            if lost_elapsed <= FACE_LOST_TOLERANCE_S:
                self.tracked_face_status = "temporarily_lost_within_tolerance"
            else:
                self._clear_track()
            return

        if self.tracked_face is None:
            self._begin_new_track(primary_face, now)
            return

        if self._is_same_subject(primary_face, self.tracked_face):
            self.tracked_face = primary_face
            self.last_tracked_seen_at = now
            self.face_missing_since = None
            self.tracked_face_status = "tracking_visible_subject"
            return

        self._begin_new_track(primary_face, now)

    def _begin_new_track(self, face: DetectedFace, now: float) -> None:
        self._track_sequence += 1
        self.tracked_face = face
        self.tracked_face_id = f"face_{self._track_sequence:03d}"
        self.tracked_face_status = "tracking_visible_subject"
        self.face_missing_since = None
        self.last_tracked_seen_at = now

    def _clear_track(self) -> None:
        self.tracked_face = None
        self.tracked_face_id = None
        self.tracked_face_status = "track_reset"
        self.face_missing_since = None
        self.last_tracked_seen_at = None

    def _is_same_subject(self, current: DetectedFace, tracked: DetectedFace) -> bool:
        iou = self._bbox_iou(current.bbox, tracked.bbox)
        center_distance = self._normalized_center_distance(current.bbox, tracked.bbox)
        size_ratio = self._size_ratio(current.bbox, tracked.bbox)
        return iou >= 0.18 or (center_distance <= 0.22 and 0.55 <= size_ratio <= 1.85)

    @staticmethod
    def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax2, ay2 = ax + aw, ay + ah
        bx2, by2 = bx + bw, by + bh
        inter_x1 = max(ax, bx)
        inter_y1 = max(ay, by)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        union_area = aw * ah + bw * bh - inter_area
        if union_area <= 0:
            return 0.0
        return inter_area / union_area

    @staticmethod
    def _normalized_center_distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        acx, acy = ax + aw / 2.0, ay + ah / 2.0
        bcx, bcy = bx + bw / 2.0, by + bh / 2.0
        diagonal = math.hypot(max(aw, bw), max(ah, bh))
        if diagonal <= 0:
            return 1.0
        return math.hypot(acx - bcx, acy - bcy) / diagonal

    @staticmethod
    def _size_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        a_area = max(a[2] * a[3], 1)
        b_area = max(b[2] * b[3], 1)
        smaller = min(a_area, b_area)
        larger = max(a_area, b_area)
        return smaller / larger

    def _consider_best_candidate(self, frame, face: DetectedFace, now: float) -> None:
        score_details = score_frame_candidate(frame, face)
        total_score = score_details["total_score"]
        if self.best_candidate is None or total_score > self.best_candidate["score"]:
            self.best_candidate = {
                "score": total_score,
                "frame_jpeg": encode_frame_to_jpeg(frame, quality=96),
                "face": face,
                "timestamp": now,
                "score_details": score_details,
            }

    def _lock_best_frame(self) -> None:
        if self.best_candidate is None:
            self._reset_live_detection_stage(
                warning="No face candidate was captured during the 5-second sampling window. Returning to live detection."
            )
            return

        candidate = self.best_candidate
        self.selected_best_frame_jpeg = candidate["frame_jpeg"]
        self.selected_best_face = candidate["face"]
        self.selected_best_frame_metadata = {
            "captured_at_monotonic": round(candidate["timestamp"], 3),
            "score": candidate["score"],
            "score_details": candidate["score_details"],
        }
        if candidate["score"] < HIGH_QUALITY_SCORE_THRESHOLD:
            self.warning = (
                "No candidate reached the preferred quality threshold. "
                "The highest-scoring frame from the 5-second window was selected."
            )
        self.camera_active = False
        self.camera_permission_status = "stopped"
        self.face_detected = None
        self.primary_face = None
        self.tracked_face = None
        self.tracked_face_id = None
        self.tracked_face_status = "selected_best_frame_locked"
        self.last_frame_at = None
        self.fps = 0.0
        self.sampling_started_at = None
        self.best_candidate = None
        self.state = "capture_flash"
        self.system_status = "selected_best_frame_locked"
        LOGGER.info("Best frame locked for run_id=%s score=%.4f", self.run_id, candidate["score"])
        threading.Thread(target=self._run_post_capture_pipeline, args=(self.run_id,), daemon=True).start()

    def _reset_live_detection_stage(self, *, warning: str | None = None) -> None:
        self.state = "idle_black"
        self.camera_active = True
        self.camera_permission_status = "granted"
        self.face_detected = False
        self.primary_face = None
        self.tracked_face = None
        self.tracked_face_id = None
        self.tracked_face_status = "waiting_for_face"
        self.face_presence_seconds = 0.0
        self.stable_face_seconds = 0.0
        self.activation_face_seconds = 0.0
        self.activation_progress = 0.0
        self.remaining_activation_seconds = ACTIVATION_REQUIRED_FACE_SECONDS
        self.detection_samples = []
        self.face_missing_since = None
        self.last_tracked_seen_at = None
        self.activating_started_at = None
        self.sampling_started_at = None
        self.anchored_started_at = None
        self.best_candidate = None
        self.selected_best_frame_jpeg = None
        self.selected_best_face = None
        self.selected_best_frame_metadata = None
        self.portrait_src = ""
        self.portrait_timestamp = 0
        self.emotion_result = _default_emotion_payload()
        self.last_frame_at = None
        self.fps = 0.0
        self.system_status = "waiting_for_face_activation"
        self.warning = warning
        self.error = None
        update_field_emotion_result({}, has_face=False)
        LOGGER.info("Returned to idle_black after sampling retry. run_id=%s warning=%s", self.run_id, warning)

    def _run_post_capture_pipeline(self, run_id: str) -> None:
        frame_bytes = self.get_selected_frame_bytes()
        selected_face = self.selected_best_face
        if frame_bytes is None or selected_face is None:
            self._set_error(run_id, "selected_best_frame was missing before scanning.")
            return

        frame = decode_image_bytes(frame_bytes)
        try:
            recognition_result = self.emotion_recognizer.recognize(frame, selected_face)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Emotion recognition failed for run_id=%s", run_id)
            self._set_error(run_id, f"Emotion recognition failed: {exc}")
            return

        if not self._sleep_if_active(run_id, CAPTURE_FLASH_DURATION_MS / 1000.0):
            return

        with self._lock:
            if self.run_id != run_id:
                return
            self.state = "face_scanning"
            self.system_status = "face_scanning"

        if not self._sleep_if_active(run_id, FACE_SCANNING_DURATION_MS / 1000.0):
            return

        portrait_payload = None
        if self._is_active_run(run_id):
            try:
                portrait_payload = save_latest_portrait_image(frame, selected_face)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Portrait image save failed for run_id=%s error=%s", run_id, exc)

        with self._lock:
            if self.run_id != run_id:
                return
            self.emotion_result = recognition_result
            self.portrait_src = portrait_payload["portraitSrc"] if portrait_payload else ""
            self.portrait_timestamp = int(portrait_payload["portraitTimestamp"]) if portrait_payload else 0
            update_field_emotion_result(
                recognition_result.get("compound_emotion", {}),
                has_face=True,
                portrait_src=self.portrait_src or None,
                portrait_timestamp=self.portrait_timestamp or None,
            )
            set_proceed_to_core_area(True)
            self.state = "anchored_message"
            self.anchored_started_at = time.monotonic()
            self.system_status = "anchored_message"
            LOGGER.info("Anchored message entered for run_id=%s", run_id)

    def _sleep_if_active(self, run_id: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._is_active_run(run_id):
                return False
            time.sleep(0.05)
        return self._is_active_run(run_id)

    def _is_active_run(self, run_id: str) -> bool:
        with self._lock:
            return self.run_id == run_id

    def _set_error(self, run_id: str, message: str) -> None:
        with self._lock:
            if self.run_id != run_id:
                return
            self.error = message
            self.system_status = "error"
            reset_field_emotion_state()
