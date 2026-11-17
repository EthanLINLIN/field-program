from __future__ import annotations

import math
import time
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from .face_pipeline import DetectedFace, extract_face_crop


FERPLUS_LABELS = [
    "neutral",
    "happiness",
    "surprise",
    "sadness",
    "anger",
    "disgust",
    "fear",
    "contempt",
]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


class EmotionRecognizer:
    def __init__(self, model_path: str) -> None:
        load_started = time.perf_counter()
        self.session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        self.load_elapsed_ms = round((time.perf_counter() - load_started) * 1000.0, 2)
        self.input_info = self.session.get_inputs()[0]
        self.output_info = self.session.get_outputs()[0]
        self.input_name = self.input_info.name
        self.input_type = self.input_info.type
        self.input_shape = self.input_info.shape

    def recognize(self, frame_bgr: np.ndarray, face: DetectedFace) -> dict[str, Any]:
        crop = extract_face_crop(frame_bgr, face, padding=0.16)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)

        input_tensor = self._prepare_input_tensor(resized)
        infer_started = time.perf_counter()
        outputs = self.session.run([self.output_info.name], {self.input_name: input_tensor})
        inference_ms = round((time.perf_counter() - infer_started) * 1000.0, 2)

        raw_scores = np.asarray(outputs[0]).astype(np.float32).reshape(-1)
        probabilities = self._softmax(raw_scores)
        result = self._map_to_seven_emotions(probabilities)
        result["model_load_ms"] = self.load_elapsed_ms
        result["inference_ms"] = inference_ms
        result["model_source"] = "Emotion FERPlus ONNX"
        result["face_bbox"] = {
            "x": int(face.bbox[0]),
            "y": int(face.bbox[1]),
            "w": int(face.bbox[2]),
            "h": int(face.bbox[3]),
        }
        return result

    def _prepare_input_tensor(self, gray_64: np.ndarray) -> np.ndarray:
        if "float" in self.input_type:
            converted = gray_64.astype(np.float32)
        elif "uint8" in self.input_type:
            converted = gray_64.astype(np.uint8)
        else:
            converted = gray_64.astype(np.float32)

        shape = [dim if isinstance(dim, int) else None for dim in self.input_shape]
        if len(shape) == 4 and shape[1] == 1:
            return converted[np.newaxis, np.newaxis, :, :]
        if len(shape) == 4 and shape[-1] == 1:
            return converted[np.newaxis, :, :, np.newaxis]
        if len(shape) == 3:
            return converted[np.newaxis, :, :]
        raise RuntimeError(f"Unsupported FERPlus input shape: {self.input_shape}")

    @staticmethod
    def _softmax(values: np.ndarray) -> np.ndarray:
        shifted = values - np.max(values)
        exp_values = np.exp(shifted)
        return exp_values / np.sum(exp_values)

    def _map_to_seven_emotions(self, probabilities: np.ndarray) -> dict[str, Any]:
        mapped = {
            "happy": float(probabilities[FERPLUS_LABELS.index("happiness")]),
            "surprise": float(probabilities[FERPLUS_LABELS.index("surprise")]),
            "angry": float(probabilities[FERPLUS_LABELS.index("anger")]),
            "neutral": float(probabilities[FERPLUS_LABELS.index("neutral")]),
            "sad": float(probabilities[FERPLUS_LABELS.index("sadness")]),
            "fear": float(probabilities[FERPLUS_LABELS.index("fear")]),
            "disgust": float(
                probabilities[FERPLUS_LABELS.index("disgust")]
                + probabilities[FERPLUS_LABELS.index("contempt")]
            ),
        }

        total = sum(mapped.values())
        if total <= 0:
            raise RuntimeError("FERPlus returned an invalid probability distribution.")

        normalized = {key: value / total for key, value in mapped.items()}
        dominant_emotion = max(normalized, key=normalized.get)
        dominant_confidence = normalized[dominant_emotion]
        non_neutral_sum = (
            normalized["happy"]
            + normalized["surprise"]
            + normalized["angry"]
            + normalized["sad"]
            + normalized["fear"]
            + normalized["disgust"]
        )
        neutral_penalty = 0.08 if dominant_emotion == "neutral" else 0.0
        emotion_fluctuation = clamp(
            0.6 * non_neutral_sum + 0.4 * dominant_confidence - neutral_penalty,
            0.0,
            1.0,
        )

        rounded = {key: round(value, 6) for key, value in normalized.items()}
        return {
            "dominant_emotion": dominant_emotion,
            "compound_emotion": rounded,
            "emotion_fluctuation": round(emotion_fluctuation, 6),
            "raw_non_neutral_sum": round(non_neutral_sum, 6),
            "dominant_confidence": round(dominant_confidence, 6),
            "postprocess_note": "FERPlus contempt output is merged into disgust for the required 7-class interface.",
        }
