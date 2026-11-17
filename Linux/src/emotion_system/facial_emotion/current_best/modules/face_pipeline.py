from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


@dataclass
class DetectedFace:
    bbox: tuple[int, int, int, int]
    score: float
    landmarks: dict[str, tuple[float, float]]
    source: str


class FaceDetector:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self.detector = None
        self.haar = None
        self.backend = "haar"

        if hasattr(cv2, "FaceDetectorYN_create"):
            self.detector = cv2.FaceDetectorYN_create(
                "onnx",
                np.frombuffer(Path(model_path).read_bytes(), dtype=np.uint8),
                np.empty(0, dtype=np.uint8),
                (320, 320),
                0.85,
                0.3,
                5000,
            )
            self.backend = "yunet"

        if self.detector is None:
            self.haar = cv2.CascadeClassifier(
                str(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            )
            self.backend = "haar"

    def detect_faces(self, frame_bgr: np.ndarray) -> list[DetectedFace]:
        if self.backend == "yunet" and self.detector is not None:
            return self._detect_with_yunet(frame_bgr)
        return self._detect_with_haar(frame_bgr)

    def detect_primary_face(self, frame_bgr: np.ndarray) -> DetectedFace | None:
        faces = self.detect_faces(frame_bgr)
        return select_primary_face(faces, frame_bgr.shape)

    def _detect_with_yunet(self, frame_bgr: np.ndarray) -> list[DetectedFace]:
        height, width = frame_bgr.shape[:2]
        self.detector.setInputSize((width, height))
        _retval, faces = self.detector.detect(frame_bgr)
        if faces is None or len(faces) == 0:
            return []

        results: list[DetectedFace] = []
        for row in faces:
            x, y, w, h = [int(round(value)) for value in row[:4]]
            x = max(0, x)
            y = max(0, y)
            w = min(width - x, max(1, w))
            h = min(height - y, max(1, h))
            results.append(
                DetectedFace(
                    bbox=(x, y, w, h),
                    score=float(row[14]),
                    landmarks={
                        "right_eye": (float(row[4]), float(row[5])),
                        "left_eye": (float(row[6]), float(row[7])),
                        "nose": (float(row[8]), float(row[9])),
                        "right_mouth": (float(row[10]), float(row[11])),
                        "left_mouth": (float(row[12]), float(row[13])),
                    },
                    source="yunet",
                )
            )
        return results

    def _detect_with_haar(self, frame_bgr: np.ndarray) -> list[DetectedFace]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self.haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(72, 72))
        if len(faces) == 0:
            return []

        results: list[DetectedFace] = []
        for x, y, w, h in faces:
            results.append(
                DetectedFace(
                    bbox=(int(x), int(y), int(w), int(h)),
                    score=0.70,
                    landmarks={
                        "right_eye": (x + w * 0.32, y + h * 0.38),
                        "left_eye": (x + w * 0.68, y + h * 0.38),
                        "nose": (x + w * 0.50, y + h * 0.58),
                        "right_mouth": (x + w * 0.38, y + h * 0.76),
                        "left_mouth": (x + w * 0.62, y + h * 0.76),
                    },
                    source="haar",
                )
            )
        return results


def select_primary_face(faces: list[DetectedFace], frame_shape: tuple[int, ...]) -> DetectedFace | None:
    if not faces:
        return None

    frame_h, frame_w = frame_shape[:2]
    frame_center_x = frame_w / 2.0
    frame_center_y = frame_h / 2.0
    max_distance = math.hypot(frame_center_x, frame_center_y)

    def _priority(face: DetectedFace) -> float:
        x, y, w, h = face.bbox
        area_ratio = (w * h) / max(float(frame_w * frame_h), 1.0)
        face_center_x = x + w / 2.0
        face_center_y = y + h / 2.0
        center_distance = math.hypot(face_center_x - frame_center_x, face_center_y - frame_center_y)
        center_score = clamp(1.0 - center_distance / max(max_distance, 1.0), 0.0, 1.0)
        return 0.72 * area_ratio + 0.28 * center_score

    return max(faces, key=_priority)


def decode_image_bytes(image_bytes: bytes) -> np.ndarray:
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode image bytes.")
    return frame


def encode_frame_to_jpeg(frame_bgr: np.ndarray, quality: int = 95) -> bytes:
    success, encoded = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        raise ValueError("Failed to encode image as JPEG.")
    return encoded.tobytes()


def extract_face_crop(frame_bgr: np.ndarray, face: DetectedFace, padding: float = 0.18) -> np.ndarray:
    frame_h, frame_w = frame_bgr.shape[:2]
    x, y, w, h = face.bbox
    pad_x = int(round(w * padding))
    pad_y = int(round(h * padding))

    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(frame_w, x + w + pad_x)
    y2 = min(frame_h, y + h + pad_y)

    crop = frame_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("Face crop is empty.")
    return crop


def score_frame_candidate(frame_bgr: np.ndarray, face: DetectedFace) -> dict[str, Any]:
    frame_h, frame_w = frame_bgr.shape[:2]
    x, y, w, h = face.bbox
    crop = extract_face_crop(frame_bgr, face)
    gray_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    blur_variance = float(cv2.Laplacian(gray_crop, cv2.CV_64F).var())
    sharpness_score = clamp(math.log1p(blur_variance) / math.log1p(1400.0), 0.0, 1.0)

    left_eye = face.landmarks["left_eye"]
    right_eye = face.landmarks["right_eye"]
    nose = face.landmarks["nose"]
    left_mouth = face.landmarks["left_mouth"]
    right_mouth = face.landmarks["right_mouth"]

    eye_level_delta = abs(left_eye[1] - right_eye[1]) / max(float(h), 1.0)
    eye_level_score = clamp(1.0 - eye_level_delta * 3.8, 0.0, 1.0)

    nose_offset = abs(nose[0] - (x + w / 2.0)) / max(float(w), 1.0)
    nose_center_score = clamp(1.0 - nose_offset * 3.2, 0.0, 1.0)

    mouth_level_delta = abs(left_mouth[1] - right_mouth[1]) / max(float(h), 1.0)
    mouth_level_score = clamp(1.0 - mouth_level_delta * 3.0, 0.0, 1.0)

    eye_distance = abs(left_eye[0] - right_eye[0]) / max(float(w), 1.0)
    eye_distance_score = clamp(1.0 - abs(eye_distance - 0.34) / 0.20, 0.0, 1.0)

    frontal_score = (
        0.34 * eye_level_score
        + 0.32 * nose_center_score
        + 0.18 * mouth_level_score
        + 0.16 * eye_distance_score
    )

    face_center_x = x + w / 2.0
    face_center_y = y + h / 2.0
    frame_center_x = frame_w / 2.0
    frame_center_y = frame_h / 2.0
    center_distance = math.hypot(face_center_x - frame_center_x, face_center_y - frame_center_y)
    max_distance = math.hypot(frame_center_x, frame_center_y)
    center_score = clamp(1.0 - center_distance / max(max_distance, 1.0), 0.0, 1.0)

    brightness = float(gray_crop.mean()) / 255.0
    brightness_score = clamp(1.0 - abs(brightness - 0.56) / 0.34, 0.0, 1.0)

    face_ratio = (w * h) / max(float(frame_w * frame_h), 1.0)
    face_size_score = clamp(1.0 - abs(face_ratio - 0.16) / 0.12, 0.0, 1.0)

    detection_confidence_score = clamp((face.score - 0.55) / 0.40, 0.0, 1.0)

    total_score = (
        0.50 * frontal_score
        + 0.18 * sharpness_score
        + 0.12 * detection_confidence_score
        + 0.10 * center_score
        + 0.05 * brightness_score
        + 0.05 * face_size_score
    )

    return {
        "total_score": round(total_score, 6),
        "frontal_score": round(frontal_score, 6),
        "sharpness_score": round(sharpness_score, 6),
        "detection_confidence_score": round(detection_confidence_score, 6),
        "center_score": round(center_score, 6),
        "brightness_score": round(brightness_score, 6),
        "face_size_score": round(face_size_score, 6),
        "blur_variance": round(blur_variance, 3),
        "face_ratio": round(face_ratio, 4),
        "brightness": round(brightness, 4),
        "detector_backend": face.source,
    }
