/* ===========================================================================
   Zombie Defence — Leaderboard client
   Two boards from the backend:
     Live  (public)  each team's best PUBLISH PUBLIC run: the sum of its scores
                     on the public test cases; updates as runs finish
                     (Server-Sent Events from /api/leaderboard/stream)
     Final (private) each team's PUBLISH PRIVATE run on the hidden test cases
                     (sum), shown once the organisers publish it
   Served by the game site at /leaderboard/, so the backend is /backend.
   ?board=private opens the Final tab; ?api=<backend URL> points elsewhere.
   =========================================================================== */
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var BASE = (params.get("api") || (/^https?:$/.test(location.protocol)
    ? location.origin + "/backend" : "http://127.0.0.1:8010")).replace(/\/+$/, "");
  var POLL_MS = 30000;

  var els = {
    tabs: document.querySelectorAll(".tab"),
    dot: document.getElementById("liveDot"),
    status: document.getElementById("status"),
    rows: document.getElementById("rows"),
    updated: document.getElementById("updated"),
  };
  var board = params.get("board") === "private" ? "private" : "public";
  var source = null, pollTimer = null;

  function setStatus(msg, isError) {
    els.status.textContent = msg || "";
    els.status.className = "status" + (isError ? " error" : "");
  }

  function setLive(state) {  // "on" | "off" | ""
    els.dot.className = "live-dot" + (state ? " " + state : "");
    els.dot.title = state === "on" ? "Live" : state === "off" ? "Reconnecting…" : "Connecting…";
  }

  function medalFor(rank) {
    if (rank === 1) return "🥇";
    if (rank === 2) return "🥈";
    if (rank === 3) return "🥉";
    return "";
  }

  // Character portraits (the four defenders) cycled per team, with an emoji
  // fallback if an image file is missing. The <img> swings via CSS.
  var CHARS = [
    { img: "assets/archer.jpeg",  emoji: "🏹" },
    { img: "assets/wizard.jpeg",  emoji: "🧙" },
    { img: "assets/soldier.jpeg", emoji: "🪖" },
    { img: "assets/clown.jpeg",   emoji: "🤡" },
  ];
  function avatarFor(teamId) {
    var c = CHARS[Math.abs(Number(teamId) || 0) % CHARS.length];
    // onerror swaps a broken image for the emoji so the row never shows a gap
    return (
      '<img class="avatar-img" src="' + c.img + '" alt="" ' +
      "onerror=\"this.replaceWith(Object.assign(document.createElement('span'),{className:'avatar-emoji',textContent:'" +
      c.emoji + "'}))\" />"
    );
  }

  function fmtScore(score) {
    var n = Number(score);
    if (!isFinite(n)) return "—";
    // show up to 2 decimals, trim trailing zeros
    return n.toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showPlaceholder(icon, text) {
    els.rows.innerHTML =
      '<li class="placeholder"><span class="big">' + icon + "</span>" +
      escapeHtml(text) + "</li>";
  }


  // --- Render ---------------------------------------------------------------
  function subline(e) {
    if (board === "public") {
      return e.played + "/" + e.totalCases + " cases" + (e.status === "incomplete" ? " (incomplete)" : "") +
        " · " + e.evals + (e.evals === 1 ? " run" : " runs") + " published";
    }
    return e.played + "/" + e.totalCases + " hidden cases" + (e.status === "incomplete" ? " (incomplete)" : "");
  }

  function render(data) {
    if (board === "private" && !data.published) {
      showPlaceholder("🔒", "The final rankings are sealed. They are revealed after the evaluation on the hidden test cases.");
      setStatus("");
      return;
    }
    var entries = data.rows || [];
    if (entries.length === 0) {
      showPlaceholder("💀", board === "public"
        ? "No published runs yet. Press PUBLISH PUBLIC in the SDK to get on the board."
        : "No published private runs yet.");
    } else {
      var html = "";
      for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        var rank = Number(e.rank);
        var medal = medalFor(rank);
        html +=
          '<li class="row' + (rank >= 1 && rank <= 3 ? " top" + rank : "") + '" role="row">' +
            '<span class="cell-rank col-rank" role="cell">' +
              (medal ? '<span class="medal">' + medal + "</span>" : "") + "#" + rank + "</span>" +
            '<span class="cell-team col-team" role="cell">' +
              '<span class="avatar" aria-hidden="true">' + avatarFor(e.teamID) + "</span>" +
              '<span class="team-text">' + escapeHtml(e.teamName) +
                '<span class="sub">' + escapeHtml(subline(e)) + "</span></span>" +
            "</span>" +
            '<span class="cell-id col-id" role="cell">' + escapeHtml(e.teamID) + "</span>" +
            '<span class="cell-score col-score" role="cell">' + fmtScore(e.score) + "</span>" +
          "</li>";
      }
      els.rows.innerHTML = html;
    }
    var n = entries.length;
    setStatus(board === "public"
      ? n + (n === 1 ? " team" : " teams") + " · best published run · sum over " +
        (data.cases || []).length + " public test cases"
      : n + (n === 1 ? " team" : " teams") + " · " + (data.rule === "best" ? "best" : "latest") +
        " published run · sum over " + data.hiddenCases + " hidden test cases");
    els.updated.textContent = "Updated " + new Date(data.updatedAt || Date.now()).toLocaleTimeString();
  }

  // --- Data: live stream, with polling as the fallback ----------------------
  function poll() {
    fetch(BASE + "/api/leaderboard/" + board, { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(render)
      .catch(function (err) {
        setStatus("Could not reach the server (" + err.message + "). Retrying…", true);
      });
  }

  function connect() {
    if (source) source.close();
    clearInterval(pollTimer);
    setLive("");
    showPlaceholder("☣️", "Scanning the quarantine zone for survivors…");
    if (!window.EventSource) {
      poll();
      pollTimer = setInterval(poll, POLL_MS);
      return;
    }
    source = new EventSource(BASE + "/api/leaderboard/stream?board=" + board);
    source.addEventListener("board", function (ev) {
      setLive("on");
      clearInterval(pollTimer);
      pollTimer = null;
      try { render(JSON.parse(ev.data)); } catch (e) { /* ignore a malformed event */ }
    });
    source.onerror = function () {  // EventSource reconnects by itself; poll meanwhile
      setLive("off");
      if (!pollTimer) { poll(); pollTimer = setInterval(poll, POLL_MS); }
    };
  }

  function select(name) {
    board = name;
    els.tabs.forEach(function (t) { t.setAttribute("aria-selected", String(t.dataset.board === name)); });
    params.set("board", name);
    history.replaceState(null, "", "?" + params.toString());
    connect();
  }

  function init() {
    els.tabs.forEach(function (t) {
      t.addEventListener("click", function () { if (t.dataset.board !== board) select(t.dataset.board); });
    });
    select(board);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
