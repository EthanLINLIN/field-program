(function () {
  const FIELD_EMOTION_CHANNEL_NAME = "FIELD_EMOTION_CHANNEL";
  const EMOTION_KEYS = ["happy", "sadness", "surprise", "fear", "anger", "disgust", "neutral"];
  const EMOTION_FIELD_ALIASES = {
    happy: "happy",
    happiness: "happy",
    sad: "sadness",
    sadness: "sadness",
    surprise: "surprise",
    surprised: "surprise",
    fear: "fear",
    fearful: "fear",
    angry: "anger",
    anger: "anger",
    disgust: "disgust",
    disgusted: "disgust",
    neutral: "neutral"
  };

  let fieldEmotionChannel = null;

  function getFieldEmotionChannel() {
    if (typeof window === "undefined" || !("BroadcastChannel" in window)) {
      return null;
    }

    if (!fieldEmotionChannel) {
      fieldEmotionChannel = new window.BroadcastChannel(FIELD_EMOTION_CHANNEL_NAME);
    }

    return fieldEmotionChannel;
  }

  function postMessageToFieldUi(message) {
    const channel = getFieldEmotionChannel();
    if (!message || !channel) {
      return null;
    }

    channel.postMessage(message);
    return message;
  }

  function toFiniteNumber(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function getTrimmedString(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function getFirstStringValue(source, keys, fallback = "") {
    if (!source || typeof source !== "object") {
      return fallback;
    }

    for (let index = 0; index < keys.length; index += 1) {
      const key = keys[index];
      if (!Object.prototype.hasOwnProperty.call(source, key)) {
        continue;
      }

      const value = getTrimmedString(source[key]);
      if (value) {
        return value;
      }
    }

    return fallback;
  }

  function getFirstNumericValue(source, keys, fallback = 0) {
    if (!source || typeof source !== "object") {
      return fallback;
    }

    for (let index = 0; index < keys.length; index += 1) {
      const key = keys[index];
      if (!Object.prototype.hasOwnProperty.call(source, key)) {
        continue;
      }

      const numeric = toFiniteNumber(source[key]);
      if (numeric !== null && numeric > 0) {
        return numeric;
      }
    }

    return fallback;
  }

  function normalizeEmotionScores(emotionScores) {
    if (!emotionScores || typeof emotionScores !== "object") {
      return null;
    }

    const normalized = {
      happy: 0,
      sadness: 0,
      surprise: 0,
      fear: 0,
      anger: 0,
      disgust: 0,
      neutral: 0
    };
    let hasFiniteValue = false;

    Object.keys(EMOTION_FIELD_ALIASES).forEach((alias) => {
      if (!Object.prototype.hasOwnProperty.call(emotionScores, alias)) {
        return;
      }

      const numeric = toFiniteNumber(emotionScores[alias]);
      if (numeric === null) {
        return;
      }

      normalized[EMOTION_FIELD_ALIASES[alias]] = numeric;
      hasFiniteValue = true;
    });

    EMOTION_KEYS.forEach((key) => {
      if (!Object.prototype.hasOwnProperty.call(normalized, key)) {
        normalized[key] = 0;
      }
    });

    return hasFiniteValue ? normalized : null;
  }

  function buildEmotionMessage(emotionPayload) {
    const source = emotionPayload && typeof emotionPayload === "object" ? emotionPayload : null;
    const emotionScores =
      source && source.emotions && typeof source.emotions === "object"
        ? source.emotions
        : emotionPayload;
    const normalized = normalizeEmotionScores(emotionScores);
    if (!normalized) {
      return null;
    }

    const portraitSrc = getFirstStringValue(
      source,
      ["portraitSrc", "portraitUrl", "faceImageSrc", "faceImageUrl", "portraitDataUrl"],
      ""
    );
    const portraitTimestamp = getFirstNumericValue(source, ["portraitTimestamp", "timestamp"], 0);
    const timestamp = getFirstNumericValue(source, ["timestamp", "portraitTimestamp"], Date.now());

    const payload = {
      emotions: normalized,
      timestamp: timestamp
    };
    if (portraitSrc) {
      payload.portraitSrc = portraitSrc;
    }
    if (portraitTimestamp > 0) {
      payload.portraitTimestamp = portraitTimestamp;
    }

    return {
      type: "FIELD_EMOTION_UPDATE",
      source: "FacialEmotion",
      payload: payload
    };
  }

  function sendEmotionResult(emotionScores) {
    const message = buildEmotionMessage(emotionScores);
    return postMessageToFieldUi(message);
  }

  function sendRecognitionState(state = {}) {
    const started = state.started === true;
    const completed = state.completed === true;
    const proceedToCoreArea = state.proceedToCoreArea === true;

    return postMessageToFieldUi({
      type: "FIELD_RECOGNITION_STATE",
      source: "FacialEmotion",
      payload: {
        started: started,
        completed: completed,
        proceedToCoreArea: proceedToCoreArea,
        timestamp: Date.now()
      }
    });
  }

  function sendRecognitionReset() {
    return postMessageToFieldUi({
      type: "FIELD_RECOGNITION_RESET",
      source: "FacialEmotion",
      timestamp: Date.now()
    });
  }

  function sendProceedToCoreAreaToFieldUI() {
    return postMessageToFieldUi({
      type: "FIELD_PROCEED_TO_CORE_AREA",
      source: "FacialEmotion",
      payload: {
        ready: true,
        completed: true,
        proceedToCoreArea: true,
        message: "请移步至核心展区观看......",
        timestamp: Date.now()
      }
    });
  }

  window.FIELD_UI_BRIDGE = {
    sendEmotionResult: sendEmotionResult,
    sendRecognitionState: sendRecognitionState,
    sendProceedToCoreArea: sendProceedToCoreAreaToFieldUI,
    sendRecognitionReset: sendRecognitionReset
  };

  window.sendEmotionToFieldUI = function sendEmotionToFieldUI(emotionScores) {
    return sendEmotionResult(emotionScores);
  };

  window.sendRecognitionStateToFieldUI = function sendRecognitionStateToFieldUI(state) {
    return sendRecognitionState(state);
  };

  window.sendRecognitionResetToFieldUI = function sendRecognitionResetToFieldUI() {
    return sendRecognitionReset();
  };

  window.sendProceedToCoreAreaToFieldUI = sendProceedToCoreAreaToFieldUI;
})();
