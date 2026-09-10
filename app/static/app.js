(function () {
  "use strict";

  var POLL_INTERVAL_MS = 30000;
  var KEEP_ALIVE_RESTART_MS = 60000;
  var periods = ["morning", "night"];
  var currentState = null;
  var busy = { morning: false, night: false };
  var keepAlive = {
    audio: null,
    hint: null,
    started: false,
    pending: false,
    pendingReason: null,
    attemptId: 0,
    intervalId: null,
    listenersArmed: false,
    failureLogged: false
  };
  var keepAliveActivationEvents = ["touchstart", "pointerdown", "click", "keydown"];

  var messageEl = document.getElementById("message");
  var dateEl = document.getElementById("today-label");
  keepAlive.hint = document.getElementById("keepalive-hint");

  function requestJson(url, options) {
    return fetch(url, options || {}).then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    });
  }

  function setMessage(text, kind) {
    messageEl.textContent = text || "";
    messageEl.className = "message" + (kind ? " " + kind : "");
  }

  function logKeepAlive(message, detail) {
    if (!window.console) {
      return;
    }

    var logger = window.console.info || window.console.log;
    if (!logger) {
      return;
    }

    if (detail) {
      logger.call(window.console, "[KeepAlive] " + message, detail);
    } else {
      logger.call(window.console, "[KeepAlive] " + message);
    }
  }

  function warnKeepAlive(message, error) {
    if (!window.console) {
      return;
    }

    var logger = window.console.warn || window.console.log;
    if (!logger) {
      return;
    }

    if (error) {
      logger.call(window.console, "[KeepAlive] " + message, error);
    } else {
      logger.call(window.console, "[KeepAlive] " + message);
    }
  }

  function setKeepAliveHint(visible) {
    if (!keepAlive.hint) {
      return;
    }

    keepAlive.hint.hidden = !visible;
    keepAlive.hint.setAttribute("aria-hidden", visible ? "false" : "true");
  }

  function getKeepAliveAudio() {
    if (keepAlive.audio) {
      return keepAlive.audio;
    }

    keepAlive.audio = document.getElementById("keepalive-audio");
    if (!keepAlive.audio) {
      warnKeepAlive("audio element not found");
      return null;
    }

    keepAlive.audio.loop = true;
    keepAlive.audio.preload = "auto";
    keepAlive.audio.muted = false;
    keepAlive.audio.volume = 1;
    keepAlive.audio.setAttribute("playsinline", "");
    return keepAlive.audio;
  }

  function addKeepAliveActivationListeners() {
    if (keepAlive.listenersArmed) {
      return;
    }

    for (var i = 0; i < keepAliveActivationEvents.length; i += 1) {
      document.addEventListener(keepAliveActivationEvents[i], handleKeepAliveActivation, true);
    }
    keepAlive.listenersArmed = true;
  }

  function removeKeepAliveActivationListeners() {
    if (!keepAlive.listenersArmed) {
      return;
    }

    for (var i = 0; i < keepAliveActivationEvents.length; i += 1) {
      document.removeEventListener(keepAliveActivationEvents[i], handleKeepAliveActivation, true);
    }
    keepAlive.listenersArmed = false;
  }

  function ensureKeepAliveInterval() {
    if (keepAlive.intervalId) {
      return;
    }

    keepAlive.intervalId = window.setInterval(function () {
      startKeepAlive("restart");
    }, KEEP_ALIVE_RESTART_MS);
  }

  function handleKeepAliveSuccess(reason) {
    keepAlive.pending = false;
    keepAlive.pendingReason = null;
    keepAlive.started = true;
    keepAlive.failureLogged = false;
    setKeepAliveHint(false);
    removeKeepAliveActivationListeners();
    ensureKeepAliveInterval();

    if (reason === "autoplay") {
      logKeepAlive("autoplay success");
    } else if (reason === "user") {
      logKeepAlive("user operation start success");
    }
  }

  function handleKeepAliveFailure(reason, error) {
    keepAlive.pending = false;
    keepAlive.pendingReason = null;
    keepAlive.started = false;
    addKeepAliveActivationListeners();

    if (reason === "autoplay") {
      logKeepAlive("autoplay rejected", error);
      setKeepAliveHint(true);
      return;
    }

    if (!keepAlive.failureLogged) {
      warnKeepAlive("playback failed", error);
      keepAlive.failureLogged = true;
    }
    setKeepAliveHint(true);
  }

  function startKeepAlive(reason) {
    var audio = getKeepAliveAudio();
    var playResult;
    var attemptId;

    if (!audio) {
      return;
    }

    if (keepAlive.pending) {
      if (reason === "user" && keepAlive.pendingReason === "autoplay") {
        keepAlive.attemptId += 1;
        keepAlive.pending = false;
        keepAlive.pendingReason = null;
      } else {
        return;
      }
    }

    keepAlive.pending = true;
    keepAlive.pendingReason = reason;
    keepAlive.attemptId += 1;
    attemptId = keepAlive.attemptId;

    try {
      audio.muted = false;
      audio.volume = 1;
      if (reason === "restart") {
        audio.pause();
        audio.currentTime = 0;
      }
      playResult = audio.play();
    } catch (error) {
      if (attemptId === keepAlive.attemptId) {
        handleKeepAliveFailure(reason, error);
      }
      return;
    }

    if (playResult && typeof playResult.then === "function") {
      playResult.then(function () {
        if (attemptId === keepAlive.attemptId) {
          handleKeepAliveSuccess(reason);
        }
      }).catch(function (error) {
        if (attemptId === keepAlive.attemptId) {
          handleKeepAliveFailure(reason, error);
        }
      });
    } else {
      handleKeepAliveSuccess(reason);
    }
  }

  function handleKeepAliveActivation() {
    startKeepAlive("user");
  }

  function initKeepAlive() {
    try {
      logKeepAlive("initialized");
      addKeepAliveActivationListeners();
      startKeepAlive("autoplay");
    } catch (error) {
      warnKeepAlive("initialization failed", error);
    }
  }

  function entryFor(period) {
    return currentState ? currentState[period] : { taken: false, time: null };
  }

  function renderState(data) {
    currentState = data;
    dateEl.textContent = data.displayDate || data.date || "";
    dateEl.setAttribute("datetime", data.date || "");

    for (var i = 0; i < periods.length; i += 1) {
      renderPeriod(periods[i], data[periods[i]]);
    }

    if (data.warning) {
      setMessage("⚠ " + data.warning, "warning");
    } else {
      setMessage("", "");
    }
  }

  function renderPeriod(period, entry) {
    var card = document.querySelector('[data-period="' + period + '"]');
    var status = document.getElementById(period + "-status");
    var text = status.querySelector(".status-text");
    var mark = status.querySelector(".status-mark");
    var time = document.getElementById(period + "-time");
    var button = card.querySelector("button");

    if (entry.taken) {
      status.className = "dose-status taken";
      mark.textContent = "🟢";
      text.textContent = "服薬済み";
      time.textContent = entry.time || "--:--";
      button.textContent = "取り消す";
      button.className = "action-button cancel-button";
    } else {
      status.className = "dose-status not-taken";
      mark.textContent = "🔴";
      text.textContent = "未服薬";
      time.textContent = "--:--";
      button.textContent = "飲みました";
      button.className = "action-button take-button";
    }

    button.disabled = !!busy[period];
  }

  function refreshStatus() {
    requestJson("/api/status")
      .then(function (data) {
        renderState(data);
      })
      .catch(function () {
        setMessage("サーバーとの通信に失敗しました", "error");
      });
  }

  function postAction(period) {
    var entry = entryFor(period);
    var endpoint;

    if (busy[period]) {
      return;
    }

    if (entry.taken) {
      if (!window.confirm("服薬済みを取り消しますか？")) {
        return;
      }
      endpoint = "/api/cancel/" + period;
    } else {
      endpoint = "/api/take/" + period;
    }

    busy[period] = true;
    renderPeriod(period, entry);

    requestJson(endpoint, { method: "POST" })
      .then(function (data) {
        busy[period] = false;
        renderState(data);
      })
      .catch(function () {
        busy[period] = false;
        if (currentState) {
          renderPeriod(period, entryFor(period));
        }
        setMessage("サーバーとの通信に失敗しました", "error");
      });
  }

  function bindButtons() {
    var buttons = document.querySelectorAll(".action-button");
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].addEventListener("click", function (event) {
        postAction(event.currentTarget.getAttribute("data-period"));
      });
    }
  }

  initKeepAlive();
  bindButtons();
  refreshStatus();
  window.setInterval(refreshStatus, POLL_INTERVAL_MS);
}());
