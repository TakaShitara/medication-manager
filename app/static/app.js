(function () {
  "use strict";

  var POLL_INTERVAL_MS = 30000;
  var periods = ["morning", "night"];
  var currentState = null;
  var busy = { morning: false, night: false };

  var messageEl = document.getElementById("message");
  var dateEl = document.getElementById("today-label");

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

  bindButtons();
  refreshStatus();
  window.setInterval(refreshStatus, POLL_INTERVAL_MS);
}());
