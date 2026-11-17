const UI_CONFIG = window.UI_CONFIG || {};

const bodyRoot = document.body;
const stageRoot = document.getElementById("stageRoot");
const cameraVideo = document.getElementById("cameraVideo");
const selectedFrame = document.getElementById("selectedFrame");
const scanOverlay = document.getElementById("scanOverlay");
const stateKicker = document.getElementById("stateKicker");
const subcopy = document.getElementById("subcopy");
const debugPanel = document.getElementById("debugPanel");
const activationProgressWrap = document.getElementById("activationProgressWrap");
const activationProgressFill = document.getElementById("activationProgressFill");
const activationProgressLabel = document.getElementById("activationProgressLabel");
const openingUiVisual = document.getElementById("openingUiVisual");
const openingUiImage = document.getElementById("openingUiImage");
const openingUiPrompt = document.getElementById("openingUiPrompt");
const openingUiWelcomeTitle = document.querySelector(".opening-ui-welcome-title");
const anchoredCopy = document.getElementById("anchoredCopy");
const anchoredHeadline = document.querySelector(".anchored-headline");

const LIVE_DISPLAY_STATES = new Set(["idle_black", "activating", "best_frame_selecting"]);
const SELECTED_DISPLAY_STATES = new Set(["capture_flash", "face_scanning", "anchored_message"]);
const SCAN_VISIBLE_STATES = new Set(["face_scanning"]);
const EMOTION_ORDER = ["happy", "surprise", "angry", "neutral", "sad", "fear", "disgust"];
const START_KEY_CODES = new Set(["Numpad1", "Digit1"]);
const RESET_KEY_CODES = new Set(["Numpad4", "Digit4"]);
const CAMERA_SOURCE_MODE = "frontend_getUserMedia";
const GENERIC_CAMERA_FAILURE_TEXT = "设备初始化失败，请联系现场工作人员";

const STATE_COPY = {
    idle_black_waiting_eeg: {
        kicker: "Idle Black",
        subcopy: "请按数字 1（主键盘或小键盘）启动设备。",
    },
    idle_black: {
        kicker: "Idle Black",
        subcopy: "等待最近 10 秒窗口内累计检测到人脸 6 秒。",
    },
    activating: {
        kicker: "Activating",
        subcopy: "摄像头原始画面已启用，系统正在进入激活过渡。",
    },
    best_frame_selecting: {
        kicker: "Best Frame Selecting",
        subcopy: "系统正在 5 秒采样窗口内锁定 selected_best_frame。",
    },
    capture_flash: {
        kicker: "Capture Flash",
        subcopy: "selected_best_frame 已锁定，正在执行闪烁过渡。",
    },
    face_scanning: {
        kicker: "Face Scanning",
        subcopy: "当前阶段只使用 selected_best_frame 进行扫描识别。",
    },
    anchored_message: {
        kicker: "Anchored Message",
        subcopy: "当前轮次已进入 anchored_message，背景使用 selected_best_frame。",
    },
};

const appState = {
    snapshot: { state: "idle_black_waiting_eeg", camera_active: false },
    mediaStream: null,
    cameraRequestPromise: null,
    frameLoopId: null,
    pollingId: null,
    uploadCanvas: document.createElement("canvas"),
    sendingFrame: false,
    selectedFrameRunId: null,
    localError: "",
    startInFlight: false,
    resetInFlight: false,
    textFitFrameId: null,
    openingUiAssetLoaded: false,
    openingUiAssetFailed: false,
    debugVisible: false,
    lastForwardedEmotionRunId: null,
    lastProceedSignalRunId: null,
    cameraDebug: {
        startRequested: false,
        streamActive: false,
        tracksCount: 0,
        errorName: null,
        errorMessage: null,
        startAttemptCount: 0,
        lastStartTimestamp: null,
        sourceMode: CAMERA_SOURCE_MODE,
        inputTriggerKey: null,
    },
};

async function postJson(url, payload = {}) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        throw new Error(`${url} failed: ${response.status}`);
    }
    return response.json();
}

function getCameraTracks(stream = appState.mediaStream) {
    if (!stream || typeof stream.getTracks !== "function") return [];
    return stream.getTracks();
}

function hasLiveCameraStream() {
    return getCameraTracks().some((track) => track.readyState === "live");
}

function syncLocalCameraDebugFromStream() {
    const tracks = getCameraTracks();
    appState.cameraDebug.tracksCount = tracks.length;
    appState.cameraDebug.streamActive = tracks.some((track) => track.readyState === "live");
}

function resetCameraDebugState() {
    appState.cameraDebug.startRequested = false;
    appState.cameraDebug.streamActive = false;
    appState.cameraDebug.tracksCount = 0;
    appState.cameraDebug.errorName = null;
    appState.cameraDebug.errorMessage = null;
    appState.cameraDebug.startAttemptCount = 0;
    appState.cameraDebug.lastStartTimestamp = null;
    appState.cameraDebug.inputTriggerKey = null;
}

function clearLocalCameraError() {
    appState.cameraDebug.errorName = null;
    appState.cameraDebug.errorMessage = null;
}

function setLocalCameraError(error) {
    appState.cameraDebug.errorName = error?.name || "UnknownError";
    appState.cameraDebug.errorMessage = error?.message || String(error || "Unknown camera error");
}

function logCameraEvent(eventName, payload = {}) {
    console.info(`[camera] ${eventName}`, payload);
}

function getCameraFailurePermissionStatus() {
    return appState.cameraDebug.errorName === "NotAllowedError" ? "denied" : "error";
}

function applyCssDurations() {
    document.documentElement.style.setProperty(
        "--activating-duration-ms",
        `${UI_CONFIG.activatingDurationMs || 5000}ms`,
    );
    document.documentElement.style.setProperty(
        "--capture-flash-duration-ms",
        `${UI_CONFIG.captureFlashDurationMs || 1000}ms`,
    );
    document.documentElement.style.setProperty(
        "--face-scanning-duration-ms",
        `${UI_CONFIG.faceScanningDurationMs || 5000}ms`,
    );
    document.documentElement.style.setProperty(
        "--anchored-transition-duration-ms",
        `${UI_CONFIG.anchoredTransitionDurationMs || 9000}ms`,
    );
}

function syncOpeningUiAssetState() {
    if (!openingUiImage) return;
    appState.openingUiAssetLoaded = openingUiImage.complete && openingUiImage.naturalWidth > 0;
    appState.openingUiAssetFailed = openingUiImage.complete && openingUiImage.naturalWidth === 0;
}

function handleOpeningUiAssetLoad() {
    appState.openingUiAssetLoaded = true;
    appState.openingUiAssetFailed = false;
    render(appState.snapshot);
}

function handleOpeningUiAssetError() {
    appState.openingUiAssetLoaded = false;
    appState.openingUiAssetFailed = true;
    render(appState.snapshot);
}

function applyDebugVisibility() {
    bodyRoot.classList.toggle("debug-hidden", !appState.debugVisible);
}

function toggleDebugVisibility() {
    appState.debugVisible = !appState.debugVisible;
    applyDebugVisibility();
}

async function bootstrapSession() {
    try {
        await postJson("/api/reset");
        if (window.FIELD_UI_BRIDGE && typeof window.FIELD_UI_BRIDGE.sendRecognitionReset === "function") {
            window.FIELD_UI_BRIDGE.sendRecognitionReset();
        }
    } catch (error) {
        appState.localError = `Reset on load failed: ${error.message}`;
    }
}

async function fetchState() {
    try {
        const response = await fetch("/api/state", { cache: "no-store" });
        if (!response.ok) {
            throw new Error(`state ${response.status}`);
        }
        const snapshot = await response.json();
        appState.snapshot = snapshot;
        syncSelectedFrame(snapshot);
        syncCamera(snapshot);
        forwardSnapshotEmotionToFieldUi(snapshot);
        render(snapshot);
    } catch (error) {
        appState.localError = `State polling failed: ${error.message}`;
        render(appState.snapshot);
    }
}

function buildFieldUiEmotionScores(snapshot) {
    if (!snapshot || snapshot.state !== "anchored_message") return null;

    const compoundEmotion = snapshot.compound_emotion;
    if (!compoundEmotion || typeof compoundEmotion !== "object") return null;

    const emotionScores = {
        happy: Number(compoundEmotion.happy),
        sadness: Number(compoundEmotion.sadness ?? compoundEmotion.sad),
        surprise: Number(compoundEmotion.surprise ?? compoundEmotion.surprised),
        fear: Number(compoundEmotion.fear ?? compoundEmotion.fearful),
        anger: Number(compoundEmotion.anger ?? compoundEmotion.angry),
        disgust: Number(compoundEmotion.disgust ?? compoundEmotion.disgusted),
        neutral: Number(compoundEmotion.neutral),
    };

    const hasFiniteValue = Object.values(emotionScores).some((value) => Number.isFinite(value));
    if (!hasFiniteValue) {
        return null;
    }

    return {
        emotions: emotionScores,
        portraitSrc: typeof snapshot.portraitSrc === "string" ? snapshot.portraitSrc.trim() : "",
        portraitTimestamp: Number(snapshot.portraitTimestamp) > 0 ? Number(snapshot.portraitTimestamp) : 0,
        timestamp: Number(snapshot.timestamp) > 0 ? Number(snapshot.timestamp) : Date.now(),
    };
}

function forwardSnapshotEmotionToFieldUi(snapshot) {
    if (!snapshot || !snapshot.run_id || appState.lastForwardedEmotionRunId === snapshot.run_id) {
        return;
    }

    if (!window.FIELD_UI_BRIDGE || typeof window.FIELD_UI_BRIDGE.sendEmotionResult !== "function") {
        return;
    }

    const emotionScores = buildFieldUiEmotionScores(snapshot);
    if (!emotionScores) {
        return;
    }

    const message = window.FIELD_UI_BRIDGE.sendEmotionResult(emotionScores);
    if (message) {
        appState.lastForwardedEmotionRunId = snapshot.run_id;
    }
}

function syncProceedSignalToFieldUi(snapshot) {
    if (!snapshot || snapshot.state !== "anchored_message" || !snapshot.run_id) {
        return;
    }

    if (appState.lastProceedSignalRunId === snapshot.run_id) {
        return;
    }

    if (!window.FIELD_UI_BRIDGE || typeof window.FIELD_UI_BRIDGE.sendProceedToCoreArea !== "function") {
        return;
    }

    const message = window.FIELD_UI_BRIDGE.sendProceedToCoreArea();
    if (message) {
        appState.lastProceedSignalRunId = snapshot.run_id;
    }
}

function syncSelectedFrame(snapshot) {
    if (!snapshot.selected_best_frame_available || !snapshot.run_id) {
        selectedFrame.removeAttribute("src");
        appState.selectedFrameRunId = null;
        return;
    }
    if (appState.selectedFrameRunId === snapshot.run_id) return;
    selectedFrame.src = `${snapshot.selected_best_frame_url}?run_id=${encodeURIComponent(snapshot.run_id)}&ts=${Date.now()}`;
    appState.selectedFrameRunId = snapshot.run_id;
}

function syncCamera(snapshot) {
    if (snapshot.camera_active) {
        syncLocalCameraDebugFromStream();
        if (hasLiveCameraStream()) {
            startFrameLoop();
        }
        return;
    }
    stopFrameLoop();
    stopCamera({ reason: "snapshot_inactive" });
}

async function ensureCamera() {
    if (hasLiveCameraStream()) {
        syncLocalCameraDebugFromStream();
        logCameraEvent("camera_stream_existing", {
            tracksCount: appState.cameraDebug.tracksCount,
        });
        return true;
    }

    if (appState.cameraRequestPromise) {
        logCameraEvent("camera_stream_existing", { inFlight: true });
        return appState.cameraRequestPromise;
    }

    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
        const notSupportedError = new Error("navigator.mediaDevices.getUserMedia is unavailable.");
        notSupportedError.name = "NotSupportedError";
        setLocalCameraError(notSupportedError);
        logCameraEvent("camera_getUserMedia_error_name", { value: appState.cameraDebug.errorName });
        logCameraEvent("camera_getUserMedia_error_message", { value: appState.cameraDebug.errorMessage });
        render(appState.snapshot);
        return false;
    }

    if (appState.mediaStream || cameraVideo.srcObject) {
        stopCamera({ reason: "stale_stream_cleanup" });
    }

    clearLocalCameraError();
    logCameraEvent("camera_getUserMedia_called", {
        constraints: { video: true, audio: false },
    });

    appState.cameraRequestPromise = (async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: true,
                audio: false,
            });
            appState.mediaStream = stream;
            cameraVideo.srcObject = stream;
            await cameraVideo.play();
            syncLocalCameraDebugFromStream();
            appState.localError = "";
            logCameraEvent("camera_getUserMedia_success", {
                tracksCount: appState.cameraDebug.tracksCount,
            });
            logCameraEvent("camera_tracks_count", {
                value: appState.cameraDebug.tracksCount,
            });
            return true;
        } catch (error) {
            stopCamera({ reason: "camera_start_failed" });
            setLocalCameraError(error);
            logCameraEvent("camera_getUserMedia_error_name", {
                value: appState.cameraDebug.errorName,
            });
            logCameraEvent("camera_getUserMedia_error_message", {
                value: appState.cameraDebug.errorMessage,
            });
            return false;
        } finally {
            appState.cameraRequestPromise = null;
            render(appState.snapshot);
        }
    })();

    return appState.cameraRequestPromise;
}

function stopCamera({ reason = "manual_stop" } = {}) {
    const tracks = getCameraTracks();
    const hadStream = Boolean(appState.mediaStream || cameraVideo.srcObject || tracks.length);

    if (hadStream) {
        logCameraEvent("camera_stop_called", {
            reason,
            tracksCount: tracks.length,
        });
    }

    for (const track of tracks) {
        try {
            track.stop();
        } catch (error) {
            console.warn("[camera] track stop failed", error);
        }
    }

    cameraVideo.pause();
    cameraVideo.srcObject = null;
    appState.mediaStream = null;
    syncLocalCameraDebugFromStream();

    if (hadStream) {
        logCameraEvent("camera_stop_success", { reason });
        logCameraEvent("camera_tracks_count", {
            value: appState.cameraDebug.tracksCount,
        });
    }
}

function startFrameLoop() {
    if (appState.frameLoopId) return;
    appState.frameLoopId = window.setInterval(captureAndSendFrame, UI_CONFIG.framePostIntervalMs || 200);
}

function stopFrameLoop() {
    if (!appState.frameLoopId) return;
    clearInterval(appState.frameLoopId);
    appState.frameLoopId = null;
    appState.sendingFrame = false;
}

function captureAndSendFrame() {
    if (!hasLiveCameraStream() || appState.sendingFrame) return;
    if (!appState.snapshot.camera_active) return;
    if (!LIVE_DISPLAY_STATES.has(appState.snapshot.state)) return;
    if (cameraVideo.readyState < 2) return;

    const canvas = appState.uploadCanvas;
    canvas.width = cameraVideo.videoWidth || UI_CONFIG.cameraFrameWidth || 960;
    canvas.height = cameraVideo.videoHeight || UI_CONFIG.cameraFrameHeight || 540;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(cameraVideo, 0, 0, canvas.width, canvas.height);

    appState.sendingFrame = true;
    canvas.toBlob(async (blob) => {
        if (!blob) {
            appState.sendingFrame = false;
            return;
        }
        const formData = new FormData();
        formData.append("frame", blob, "frame.jpg");
        formData.append("timestamp_ms", `${Date.now()}`);
        try {
            const response = await fetch("/api/frame", {
                method: "POST",
                body: formData,
            });
            if (!response.ok) {
                throw new Error(`frame ${response.status}`);
            }
            const payload = await response.json();
            if (payload?.snapshot) {
                appState.snapshot = payload.snapshot;
                syncSelectedFrame(payload.snapshot);
                syncCamera(payload.snapshot);
                render(payload.snapshot);
            }
        } catch (error) {
            appState.localError = `Frame upload failed: ${error.message}`;
        } finally {
            appState.sendingFrame = false;
        }
    }, "image/jpeg", 0.9);
}

async function beginInteractionFromKey(triggerKey) {
    if (appState.startInFlight || appState.resetInFlight) return;
    if (SELECTED_DISPLAY_STATES.has(appState.snapshot.state) || appState.snapshot.best_frame_locked) return;
    if (hasLiveCameraStream() && appState.snapshot.camera_active) {
        syncLocalCameraDebugFromStream();
        appState.cameraDebug.inputTriggerKey = triggerKey;
        logCameraEvent("camera_stream_existing", {
            inputTriggerKey: triggerKey,
            tracksCount: appState.cameraDebug.tracksCount,
        });
        render(appState.snapshot);
        return;
    }

    appState.startInFlight = true;
    appState.localError = "";
    appState.cameraDebug.startRequested = true;
    appState.cameraDebug.inputTriggerKey = triggerKey;
    appState.cameraDebug.startAttemptCount += 1;
    appState.cameraDebug.lastStartTimestamp = new Date().toISOString();
    clearLocalCameraError();
    if (window.FIELD_UI_BRIDGE && typeof window.FIELD_UI_BRIDGE.sendRecognitionState === "function") {
        window.FIELD_UI_BRIDGE.sendRecognitionState({
            started: true,
            completed: false,
            proceedToCoreArea: false,
        });
    }
    logCameraEvent("camera_start_requested", {
        inputTriggerKey: triggerKey,
        attempt: appState.cameraDebug.startAttemptCount,
        timestamp: appState.cameraDebug.lastStartTimestamp,
    });
    render(appState.snapshot);

    try {
        const startSnapshot = await postJson("/api/start");
        appState.snapshot = startSnapshot;
        render(startSnapshot);

        const cameraReady = await ensureCamera();
        if (cameraReady) {
            startFrameLoop();
            appState.snapshot = await postJson("/api/camera-status", {
                camera_active: true,
                camera_permission_status: "granted",
                error: null,
            });
        } else {
            appState.snapshot = await postJson("/api/camera-status", {
                camera_active: false,
                camera_permission_status: getCameraFailurePermissionStatus(),
                error: GENERIC_CAMERA_FAILURE_TEXT,
            });
        }
        render(appState.snapshot);
    } catch (error) {
        appState.localError = `Start key handling failed: ${error.message}`;
        render(appState.snapshot);
    } finally {
        appState.startInFlight = false;
        await fetchState();
    }
}

async function resetFromKey() {
    if (appState.resetInFlight) return;
    appState.resetInFlight = true;

    stopFrameLoop();
    stopCamera({ reason: "reset_requested" });
    resetCameraDebugState();
    appState.localError = "";
    selectedFrame.removeAttribute("src");
    appState.selectedFrameRunId = null;
    appState.lastForwardedEmotionRunId = null;
    appState.lastProceedSignalRunId = null;

    try {
        if (window.FIELD_UI_BRIDGE && typeof window.FIELD_UI_BRIDGE.sendRecognitionReset === "function") {
            window.FIELD_UI_BRIDGE.sendRecognitionReset();
        }
        appState.snapshot = await postJson("/api/reset");
        render(appState.snapshot);
    } catch (error) {
        appState.localError = `Reset failed: ${error.message}`;
        render(appState.snapshot);
    } finally {
        appState.resetInFlight = false;
        await fetchState();
    }
}

function buildSubcopy(snapshot, fallbackText) {
    if (snapshot.state === "idle_black_waiting_eeg") {
        return "请按数字 1（主键盘或小键盘）启动设备。";
    }
    if (snapshot.camera_permission_status === "requesting") {
        return "正在初始化采集设备，请保持当前页面并允许浏览器访问镜头。";
    }
    if (snapshot.camera_permission_status === "denied" || snapshot.system_status === "camera_error") {
        return GENERIC_CAMERA_FAILURE_TEXT;
    }
    if (snapshot.state === "idle_black") {
        return `最近 10 秒内累计检测到人脸 ${Number(snapshot.activation_face_seconds || 0).toFixed(2)}s / ${Number(snapshot.activation_required_face_seconds || 6).toFixed(2)}s。`;
    }
    if (snapshot.state === "best_frame_selecting") {
        return `最佳帧采样 ${Number(snapshot.sampling_seconds || 0).toFixed(2)}s / ${Number(UI_CONFIG.bestFrameSamplingSeconds || 5).toFixed(2)}s。`;
    }
    return fallbackText;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll("\"", "&quot;")
        .replaceAll("'", "&#39;");
}

function formatMetric(value, digits = 2) {
    if (value === null || value === undefined) return "null";
    if (typeof value === "number") {
        if (!Number.isFinite(value)) return String(value);
        return value.toFixed(digits);
    }
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
}

function formatCompactObject(value) {
    if (value === null || value === undefined) return "null";
    if (typeof value !== "object") return formatMetric(value, 3);
    return Object.entries(value)
        .map(([key, innerValue]) => {
            if (innerValue && typeof innerValue === "object" && !Array.isArray(innerValue)) {
                return `${key}(${formatCompactObject(innerValue)})`;
            }
            return `${key}:${formatMetric(innerValue, 3)}`;
        })
        .join(" ");
}

function formatCompoundEmotion(compoundEmotion) {
    if (!compoundEmotion || typeof compoundEmotion !== "object") return "null";
    return EMOTION_ORDER
        .map((emotionKey) => `${emotionKey}:${formatMetric(compoundEmotion[emotionKey] ?? 0, 2)}`)
        .join(" ");
}

function buildDebugRows(snapshot) {
    return [
        { label: "timestamp", value: snapshot.timestamp },
        { label: "run_id", value: snapshot.run_id },
        { label: "state", value: snapshot.state },
        { label: "face_detected", value: formatMetric(snapshot.face_detected, 0) },
        { label: "activation_window_seconds", value: formatMetric(snapshot.activation_window_seconds, 2) },
        { label: "activation_required_face_seconds", value: formatMetric(snapshot.activation_required_face_seconds, 2) },
        { label: "activation_face_seconds", value: formatMetric(snapshot.activation_face_seconds, 2) },
        { label: "activation_progress", value: formatMetric(snapshot.activation_progress, 3) },
        { label: "remaining_activation_seconds", value: formatMetric(snapshot.remaining_activation_seconds, 2) },
        { label: "sampling_seconds", value: formatMetric(snapshot.sampling_seconds, 2) },
        { label: "best_frame_locked", value: formatMetric(snapshot.best_frame_locked, 0) },
        { label: "selected_best_frame_available", value: formatMetric(snapshot.selected_best_frame_available, 0) },
        { label: "dominant_emotion", value: snapshot.dominant_emotion },
        { label: "compound_emotion", value: formatCompoundEmotion(snapshot.compound_emotion), stacked: true },
        { label: "emotion_fluctuation", value: formatMetric(snapshot.emotion_fluctuation, 3) },
        { label: "eeg_ready", value: formatMetric(snapshot.eeg_ready, 0) },
        { label: "eeg_source", value: snapshot.eeg_source },
        { label: "interaction_started", value: formatMetric(snapshot.interaction_started, 0) },
        { label: "camera_source_mode", value: appState.cameraDebug.sourceMode },
        { label: "camera_start_requested", value: formatMetric(appState.cameraDebug.startRequested, 0) },
        { label: "camera_start_attempt_count", value: formatMetric(appState.cameraDebug.startAttemptCount, 0) },
        { label: "camera_last_start_timestamp", value: appState.cameraDebug.lastStartTimestamp, stacked: true },
        { label: "input_trigger_key", value: appState.cameraDebug.inputTriggerKey },
        { label: "camera_active", value: formatMetric(snapshot.camera_active, 0) },
        { label: "camera_permission_status", value: snapshot.camera_permission_status },
        { label: "camera_stream_active", value: formatMetric(appState.cameraDebug.streamActive, 0) },
        { label: "camera_tracks_count", value: formatMetric(appState.cameraDebug.tracksCount, 0) },
        { label: "camera_error_name", value: appState.cameraDebug.errorName },
        { label: "camera_error_message", value: appState.cameraDebug.errorMessage, stacked: true },
        { label: "display_orientation", value: snapshot.display_orientation },
        { label: "display_aspect_ratio", value: snapshot.display_aspect_ratio },
        { label: "video_fit_mode", value: snapshot.video_fit_mode },
        { label: "camera_frame", value: `${snapshot.camera_frame_width}x${snapshot.camera_frame_height}` },
        { label: "fps", value: formatMetric(snapshot.fps, 2) },
        { label: "system_status", value: snapshot.system_status, stacked: true },
        { label: "error", value: snapshot.error, stacked: true },
        { label: "warning", value: snapshot.warning, stacked: true },
        { label: "selected_best_frame_metadata", value: formatCompactObject(snapshot.selected_best_frame_metadata), stacked: true },
        { label: "local_error", value: appState.localError || null, stacked: true },
        { label: "debug_visible", value: formatMetric(appState.debugVisible, 0) },
        { label: "opening_ui_asset_loaded", value: formatMetric(appState.openingUiAssetLoaded, 0) },
        { label: "opening_ui_reference_id", value: UI_CONFIG.openingUiReferenceId || null },
        { label: "opening_ui_asset_path", value: UI_CONFIG.openingUiAssetPath || null, stacked: true },
        { label: "opening_ui_asset_note", value: UI_CONFIG.openingUiAssetNote || null, stacked: true },
        { label: "opening_ui_mode", value: UI_CONFIG.openingUiMode || null },
        { label: "opening_ui_background_static", value: formatMetric(Boolean(UI_CONFIG.openingUiBackgroundStatic), 0) },
        { label: "opening_ui_local_masking", value: formatMetric(Boolean(UI_CONFIG.openingUiLocalMasking), 0) },
        { label: "opening_ui_local_animation_enabled", value: formatMetric(Boolean(UI_CONFIG.openingUiAnimationEnabled) && appState.openingUiAssetLoaded, 0) },
        { label: "opening_ui_prompt_pulse", value: formatMetric(Boolean(UI_CONFIG.openingUiPromptPulse), 0) },
        { label: "opening_ui_sensor_label_pulse", value: formatMetric(Boolean(UI_CONFIG.openingUiSensorLabelPulse), 0) },
        { label: "opening_ui_field_dot_motion", value: formatMetric(Boolean(UI_CONFIG.openingUiFieldDotMotion), 0) },
        { label: "opening_ui_welcome_dot_motion", value: formatMetric(Boolean(UI_CONFIG.openingUiWelcomeDotMotion), 0) },
        { label: "portrait_effect_enabled", value: "false" },
        { label: "portrait_effect_mode", value: "disabled" },
    ];
}

function renderDebug(snapshot) {
    const rowsHtml = buildDebugRows(snapshot)
        .map(({ label, value, stacked }) => {
            const rowClassName = stacked ? "debug-row debug-row--stacked" : "debug-row";
            return [
                `<div class="${rowClassName}">`,
                `<div class="debug-label">${escapeHtml(label)}</div>`,
                `<div class="debug-value">${escapeHtml(value)}</div>`,
                "</div>",
            ].join("");
        })
        .join("");

    debugPanel.innerHTML = `<div class="debug-list">${rowsHtml}</div>`;
}

function getContainerInnerWidth(container) {
    if (!container) return 0;
    const computedStyle = window.getComputedStyle(container);
    const paddingLeft = Number.parseFloat(computedStyle.paddingLeft) || 0;
    const paddingRight = Number.parseFloat(computedStyle.paddingRight) || 0;
    return Math.max(0, container.clientWidth - paddingLeft - paddingRight);
}

function fitTextOneLine(element, container, maxFontSize, minFontSize, widthRatio = 1) {
    if (!element || !container) return;

    const availableWidth = Math.floor(getContainerInnerWidth(container) * widthRatio);
    if (availableWidth <= 0) return;

    let fontSize = maxFontSize;
    element.style.fontSize = `${fontSize}px`;
    element.style.whiteSpace = "nowrap";

    while (element.scrollWidth > availableWidth && fontSize > minFontSize) {
        fontSize -= 1;
        element.style.fontSize = `${fontSize}px`;
    }
}

function fitMainPromptText() {
    fitTextOneLine(openingUiWelcomeTitle, stageRoot, 30, 14, 0.78);
    fitTextOneLine(openingUiPrompt, stageRoot, 26, 14, 0.92);
    fitTextOneLine(anchoredHeadline, anchoredCopy, 42, 12, 0.98);
}

function scheduleTextFit() {
    if (appState.textFitFrameId) {
        window.cancelAnimationFrame(appState.textFitFrameId);
    }
    appState.textFitFrameId = window.requestAnimationFrame(() => {
        appState.textFitFrameId = null;
        fitMainPromptText();
    });
}

function render(snapshot) {
    const copy = STATE_COPY[snapshot.state] || STATE_COPY.idle_black_waiting_eeg;
    syncLocalCameraDebugFromStream();
    forwardSnapshotEmotionToFieldUi(snapshot);
    const showLiveFrame = hasLiveCameraStream() && snapshot.camera_active && LIVE_DISPLAY_STATES.has(snapshot.state);
    const showSelectedFrame = snapshot.selected_best_frame_available && SELECTED_DISPLAY_STATES.has(snapshot.state);
    const showOpeningUi = Boolean(UI_CONFIG.openingUiAssetUrl) && !appState.openingUiAssetFailed;
    const showProgress =
        snapshot.state === "idle_black"
        && snapshot.eeg_ready
        && snapshot.camera_active
        && Number(snapshot.activation_face_seconds || 0) >= Number(UI_CONFIG.activationProgressShowSeconds || 4);

    stageRoot.dataset.state = snapshot.state || "idle_black_waiting_eeg";
    stateKicker.textContent = copy.kicker;
    subcopy.textContent = buildSubcopy(snapshot, copy.subcopy);

    cameraVideo.classList.toggle("hidden", !showLiveFrame);
    selectedFrame.classList.toggle("hidden", !showSelectedFrame);
    openingUiVisual.classList.toggle("hidden", !showOpeningUi);

    activationProgressWrap.classList.toggle("hidden", !showProgress);
    activationProgressFill.style.width = `${Math.max(0, Math.min(1, Number(snapshot.activation_progress || 0))) * 100}%`;
    activationProgressLabel.textContent = `映射激活中 ${Number(snapshot.remaining_activation_seconds || 0).toFixed(1)}s`;

    scanOverlay.classList.toggle("hidden", !SCAN_VISIBLE_STATES.has(snapshot.state));
    renderDebug(snapshot);
    scheduleTextFit();
    syncProceedSignalToFieldUi(snapshot);
}

function handleKeydown(event) {
    if (event.repeat) return;
    if (event.code === "KeyQ" || event.key === "q" || event.key === "Q") {
        event.preventDefault();
        toggleDebugVisibility();
        return;
    }
    if (START_KEY_CODES.has(event.code) || event.key === "1") {
        event.preventDefault();
        const triggerKey = START_KEY_CODES.has(event.code) ? event.code : "Digit1";
        void beginInteractionFromKey(triggerKey);
        return;
    }
    if (RESET_KEY_CODES.has(event.code)) {
        event.preventDefault();
        void resetFromKey();
    }
}

async function init() {
    applyCssDurations();
    appState.debugVisible = false;
    resetCameraDebugState();
    applyDebugVisibility();
    syncOpeningUiAssetState();
    if (openingUiImage) {
        openingUiImage.addEventListener("load", handleOpeningUiAssetLoad);
        openingUiImage.addEventListener("error", handleOpeningUiAssetError);
    }
    await bootstrapSession();
    await fetchState();
    appState.pollingId = window.setInterval(fetchState, UI_CONFIG.statePollIntervalMs || 220);
    window.addEventListener("keydown", handleKeydown);
    window.addEventListener("resize", scheduleTextFit);
    if (document.fonts && typeof document.fonts.ready?.then === "function") {
        document.fonts.ready.then(() => {
            scheduleTextFit();
        });
    }
    window.addEventListener("beforeunload", () => {
        stopFrameLoop();
        stopCamera({ reason: "beforeunload" });
        if (appState.textFitFrameId) {
            window.cancelAnimationFrame(appState.textFitFrameId);
            appState.textFitFrameId = null;
        }
    });
}

init();
