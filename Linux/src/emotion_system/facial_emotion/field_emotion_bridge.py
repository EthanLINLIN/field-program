from __future__ import annotations

import math
from copy import deepcopy
from threading import Lock
from time import time
from typing import Any


EMOTION_KEYS = (
    "happy",
    "sadness",
    "surprise",
    "fear",
    "anger",
    "disgust",
    "neutral",
)

EMOTION_ALIASES = {
    "happy": "happy",
    "happiness": "happy",
    "sad": "sadness",
    "sadness": "sadness",
    "surprise": "surprise",
    "surprised": "surprise",
    "fear": "fear",
    "fearful": "fear",
    "angry": "anger",
    "anger": "anger",
    "disgust": "disgust",
    "disgusted": "disgust",
    "neutral": "neutral",
}

PROCEED_TO_CORE_AREA_MESSAGE = "请移步至核心展区观看......"

_state_lock = Lock()
_latest_state = {
    "recognitionStarted": False,
    "recognitionCompleted": False,
    "proceedToCoreArea": False,
    "message": "",
    "status": "idle",
    "stage": "idle",
    "valid": False,
    "hasFace": False,
    "emotions": {key: 0.0 for key in EMOTION_KEYS},
    "primaryEmotion": "neutral",
    "confidence": 0.0,
    "portraitSrc": "",
    "portraitTimestamp": 0,
    "timestamp": 0,
}


def _now_timestamp_ms() -> int:
    return int(time() * 1000)


def _touch_state_timestamp_locked() -> int:
    next_timestamp = _now_timestamp_ms()
    current_timestamp = int(_latest_state.get("timestamp", 0))
    if next_timestamp <= current_timestamp:
        next_timestamp = current_timestamp + 1
    _latest_state["timestamp"] = next_timestamp
    return next_timestamp


def _to_finite_number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _extract_candidate_scores(raw_scores: Any) -> dict[str, Any]:
    if not isinstance(raw_scores, dict):
        return {}

    if isinstance(raw_scores.get("payload"), dict):
        payload_scores = _extract_candidate_scores(raw_scores["payload"])
        if payload_scores:
            return payload_scores

    if isinstance(raw_scores.get("result"), dict):
        result_scores = _extract_candidate_scores(raw_scores["result"])
        if result_scores:
            return result_scores

    if isinstance(raw_scores.get("emotions"), dict):
        return raw_scores["emotions"]

    if isinstance(raw_scores.get("compound_emotion"), dict):
        return raw_scores["compound_emotion"]

    labels = raw_scores.get("labels")
    scores = raw_scores.get("scores")
    if isinstance(labels, list) and isinstance(scores, list):
        pairs: dict[str, Any] = {}
        for index in range(min(len(labels), len(scores))):
            label = labels[index]
            if isinstance(label, str):
                pairs[label] = scores[index]
        if pairs:
            return pairs

    return raw_scores


def _normalize_emotions(raw_scores: Any) -> tuple[dict[str, float], bool]:
    source = _extract_candidate_scores(raw_scores)
    collected: dict[str, float | None] = {key: None for key in EMOTION_KEYS}
    detected_values: list[float] = []

    if isinstance(source, dict):
        for raw_key, raw_value in source.items():
            if not isinstance(raw_key, str):
                continue

            canonical_key = EMOTION_ALIASES.get(raw_key.strip().lower())
            if not canonical_key:
                continue

            numeric = _to_finite_number(raw_value)
            if numeric is None:
                continue

            collected[canonical_key] = numeric
            detected_values.append(numeric)

    scale_to_percent = bool(detected_values) and max(detected_values) <= 1.5
    normalized = {key: 0.0 for key in EMOTION_KEYS}

    for key in EMOTION_KEYS:
        numeric = collected[key]
        value = 0.0 if numeric is None else numeric
        if scale_to_percent:
            value *= 100.0
        normalized[key] = round(_clamp(value, 0.0, 100.0), 4)

    has_detected_values = any(value is not None for value in collected.values())
    return normalized, has_detected_values


def _resolve_timestamp(raw_scores: Any) -> int:
    candidates: list[Any] = []
    if isinstance(raw_scores, dict):
        candidates.extend(
            [
                raw_scores.get("timestamp"),
                raw_scores.get("payload", {}).get("timestamp") if isinstance(raw_scores.get("payload"), dict) else None,
                raw_scores.get("result", {}).get("timestamp") if isinstance(raw_scores.get("result"), dict) else None,
            ]
        )

    for candidate in candidates:
        numeric = _to_finite_number(candidate)
        if numeric is not None and numeric > 0:
            return int(numeric)

    return _now_timestamp_ms()


def reset_field_emotion_state() -> dict[str, Any]:
    with _state_lock:
        _latest_state["recognitionStarted"] = False
        _latest_state["recognitionCompleted"] = False
        _latest_state["proceedToCoreArea"] = False
        _latest_state["message"] = ""
        _latest_state["status"] = "idle"
        _latest_state["stage"] = "idle"
        _latest_state["valid"] = False
        _latest_state["hasFace"] = False
        _latest_state["emotions"] = {key: 0.0 for key in EMOTION_KEYS}
        _latest_state["primaryEmotion"] = "neutral"
        _latest_state["confidence"] = 0.0
        _latest_state["portraitSrc"] = ""
        _latest_state["portraitTimestamp"] = 0
        _touch_state_timestamp_locked()
        return deepcopy(_latest_state)


def set_recognition_started(started: bool = True) -> dict[str, Any]:
    with _state_lock:
        started = bool(started)
        _latest_state["recognitionStarted"] = started
        _latest_state["recognitionCompleted"] = False
        _latest_state["proceedToCoreArea"] = False
        _latest_state["message"] = ""
        _latest_state["status"] = "recognizing" if started else "idle"
        _latest_state["stage"] = "recognizing" if started else "idle"
        _latest_state["portraitSrc"] = ""
        _latest_state["portraitTimestamp"] = 0
        _touch_state_timestamp_locked()
        return deepcopy(_latest_state)


def set_proceed_to_core_area(ready: bool = True) -> dict[str, Any]:
    with _state_lock:
        ready = bool(ready)
        if ready:
            _latest_state["recognitionStarted"] = True
        _latest_state["recognitionCompleted"] = ready
        _latest_state["proceedToCoreArea"] = ready
        _latest_state["message"] = PROCEED_TO_CORE_AREA_MESSAGE if ready else ""
        _latest_state["status"] = "proceed_to_core_area" if ready else (
            "recognizing" if _latest_state["recognitionStarted"] else "idle"
        )
        _latest_state["stage"] = "completed" if ready else (
            "recognizing" if _latest_state["recognitionStarted"] else "idle"
        )
        _touch_state_timestamp_locked()
        return deepcopy(_latest_state)


def update_field_emotion_result(
    raw_scores: Any,
    has_face: bool = True,
    portrait_src: str | None = None,
    portrait_timestamp: int | None = None,
) -> dict[str, Any]:
    has_face = bool(has_face)
    timestamp = _resolve_timestamp(raw_scores)
    normalized_emotions, has_detected_values = _normalize_emotions(raw_scores)

    primary_emotion = "neutral"
    confidence = 0.0
    for key in EMOTION_KEYS:
        if normalized_emotions[key] > confidence:
            primary_emotion = key
            confidence = normalized_emotions[key]

    valid = has_face and has_detected_values
    resolved_portrait_src = portrait_src.strip() if isinstance(portrait_src, str) else ""
    resolved_portrait_timestamp = _to_finite_number(portrait_timestamp)
    if resolved_portrait_src and resolved_portrait_timestamp is None:
        resolved_portrait_timestamp = float(timestamp)
    state = {
        "valid": valid,
        "hasFace": has_face,
        "emotions": normalized_emotions if valid else {key: 0.0 for key in EMOTION_KEYS},
        "primaryEmotion": primary_emotion if valid else "neutral",
        "confidence": round(confidence, 4) if valid else 0.0,
        "portraitSrc": resolved_portrait_src if valid else "",
        "portraitTimestamp": int(resolved_portrait_timestamp) if valid and resolved_portrait_src and resolved_portrait_timestamp else 0,
        "timestamp": timestamp,
    }

    with _state_lock:
        if timestamp <= int(_latest_state["timestamp"]):
            timestamp = int(_latest_state["timestamp"]) + 1
        state["timestamp"] = timestamp
        _latest_state.update(state)
        if _latest_state["proceedToCoreArea"]:
            _latest_state["status"] = "proceed_to_core_area"
            _latest_state["stage"] = "completed"
            _latest_state["message"] = PROCEED_TO_CORE_AREA_MESSAGE
        elif _latest_state["recognitionStarted"]:
            _latest_state["status"] = "recognizing"
            _latest_state["stage"] = "recognizing"
            _latest_state["message"] = ""
        else:
            _latest_state["status"] = "idle"
            _latest_state["stage"] = "idle"
            _latest_state["message"] = ""
        return deepcopy(_latest_state)


def get_field_emotion_result() -> dict[str, Any]:
    with _state_lock:
        return deepcopy(_latest_state)
