/* ============================================================
   Job Hunter — frontend logic
   ============================================================ */
(() => {
  "use strict";

  // ── DOM helpers ──
  const $ = (id) => document.getElementById(id);

  // ── Constants ──
  const HEALTH_MIN_MS = 15000;   // 15s
  const HEALTH_MAX_MS = 60000;   // 60s
  const POLL_RUNNING_MS  = 1000; // 1s while pipeline running
  const POLL_IDLE_MS     = 3000; // 3s when idle

  // ── State ──
  const state = {
    logCursor: 0,          // how many log lines we've already rendered
    healthOnline: null,    // null = unknown, true/false after first check
    lastRunning: false,
    lastExcelReady: false,
    sheets: [],
    activeSheet: null,
    resultsCache: {},      // sheet -> rows
    statusTimer: null,
    logTimer: null,
    durationTicker: null,
    lastStartedAt: null,
    lastFinishedAt: null,
  };

  // ─────────────────────────────────────────────────────────
  //  Utilities
  // ─────────────────────────────────────────────────────────
  function toast(msg, isError) {
    const el = $("toast");
    el.textContent = msg;
    el.className = "toast show" + (isError ? " error" : "");
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.className = "toast"), 3200);
  }

  function fmtDuration(sec) {
    if (sec == null || isNaN(sec)) return "—";
    if (sec < 60) return sec.toFixed(1) + "s";
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return `${m}m ${s}s`;
  }

  function fmtBytes(n) {
    if (!n) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(2) + " MB";
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleTimeString();
  }

  function classifyLine(line) {
    if (line.includes("★")) return "star";
    if (/^\[\d+\/\d+\]/.test(line)) return "arrow";
    if (/^={3,}/.test(line)) return "header";
    if (/DONE|export|added|kept|ready/i.test(line)) return "ok";
    if (/ERROR|FATAL|Traceback|failed/i.test(line)) return "err";
    if (/^\[[0-9]+\/[0-9]+\]/.test(line)) return "header";
    return "";
  }

  // ─────────────────────────────────────────────────────────
  //  API layer
  // ─────────────────────────────────────────────────────────
  const api = {
    async health() {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (!r.ok) throw new Error("health " + r.status);
      return r.json();
    },
    async status() {
      const r = await fetch("/api/status", { cache: "no-store" });
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    },
    async logs(after, limit) {
      const q = new URLSearchParams({ after: String(after) });
      if (limit) q.set("limit", String(limit));
      const r = await fetch("/api/logs?" + q, { cache: "no-store" });
      if (!r.ok) throw new Error("logs " + r.status);
      return r.json();
    },
    async sheets() {
      const r = await fetch("/api/sheets", { cache: "no-store" });
      if (r.status === 404) return { sheets: [] };
      if (!r.ok) throw new Error("sheets " + r.status);
      return r.json();
    },
    async results(sheet) {
      const q = sheet ? "?sheet=" + encodeURIComponent(sheet) : "";
      const r = await fetch("/api/results" + q, { cache: "no-store" });
      if (r.status === 404) return { columns: [], rows: [], sheet: null };
      if (!r.ok) throw new Error("results " + r.status);
      return r.json();
    },
    async run() {
      const r = await fetch("/api/run", { method: "POST" });
      return { ok: r.ok, data: await r.json().catch(() => ({})) };
    },
    async stop() {
      const r = await fetch("/api/stop", { method: "POST" });
      return { ok: r.ok, data: await r.json().catch(() => ({})) };
    },
  };

  // ─────────────────────────────────────────────────────────
  //  Health check — independent, random 15-60s
  // ─────────────────────────────────────────────────────────
  function setHealth(ok) {
    const el = $("health");
    const lbl = el.querySelector(".label");
    el.classList.remove("online", "offline");
    el.classList.add(ok ? "online" : "offline");
    lbl.textContent = ok ? "online" : "offline";
    state.healthOnline = ok;
  }

  async function healthCheckOnce() {
    try {
      const h = await api.health();
      setHealth(true);
      if (h && h.version) $("version").textContent = "v" + h.version;
    } catch (e) {
      setHealth(false);
    }
  }

  function scheduleHealthCheck() {
    const delay = HEALTH_MIN_MS + Math.random() * (HEALTH_MAX_MS - HEALTH_MIN_MS);
    setTimeout(async () => {
      await healthCheckOnce();
      scheduleHealthCheck();
    }, delay);
  }

  // ─────────────────────────────────────────────────────────
  //  Status polling — adaptive interval
  // ─────────────────────────────────────────────────────────
  function renderStatus(s) {
    const pill = $("status-pill");
    const excelReady = !!s.excel_ready;
    const running = !!s.running;
    const err = s.last_error;

    if (running) {
      pill.textContent = "running";
      pill.className = "status-pill status-running";
    } else if (err) {
      pill.textContent = "error";
      pill.className = "status-pill status-error";
    } else if (excelReady) {
      pill.textContent = "done";
      pill.className = "status-pill status-done";
    } else if (s.finished_at) {
      pill.textContent = "idle";
      pill.className = "status-pill status-idle";
    } else {
      pill.textContent = "idle";
      pill.className = "status-pill status-idle";
    }

    // Stats
    $("s-running").textContent    = running ? "yes" : "no";
    $("s-started").textContent    = fmtTime(s.started_at);
    state.lastStartedAt          = s.started_at;
    state.lastFinishedAt         = s.finished_at;
    $("s-exit").textContent       = s.exit_code ?? "—";
    $("s-excel").textContent      = excelReady ? "ready ✓" : "no";
    $("s-excel-size").textContent = fmtBytes(s.excel_size);
    $("s-logs").textContent       = s.log_count ?? 0;
    $("s-error").textContent      = err || "—";

    // Buttons
    $("btn-run").disabled      = running;
    $("btn-stop").disabled     = !running;
    $("btn-download").disabled = !excelReady;

    // Transition detection
    if (state.lastRunning && !running) {
      if (excelReady) {
        toast("✓ Pipeline done — Excel ready");
        onExcelReady();
      } else if (err) {
        toast("Pipeline failed: " + err, true);
      } else {
        toast("Pipeline finished");
      }
    }
    if (!state.lastExcelReady && excelReady) {
      onExcelReady();
    }
    state.lastRunning = running;
    state.lastExcelReady = excelReady;
  }

  async function statusTick() {
    try {
      const s = await api.status();
      renderStatus(s);
      // kick off log polling only when running
      if (s.running) startLogPolling();
      const nextDelay = s.running ? POLL_RUNNING_MS : POLL_IDLE_MS;
      state.statusTimer = setTimeout(statusTick, nextDelay);
    } catch (e) {
      // Backend down
      setHealth(false);
      state.statusTimer = setTimeout(statusTick, 3000);
    }
  }

  // ─────────────────────────────────────────────────────────
  //  Log polling
  // ─────────────────────────────────────────────────────────
  function appendLogs(lines) {
    if (!lines || !lines.length) return;
    const el = $("log");
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    const frag = document.createDocumentFragment();
    for (const line of lines) {
      const span = document.createElement("span");
      span.className = "line " + classifyLine(line);
      span.textContent = line + "\n";
      frag.appendChild(span);
    }
    el.appendChild(frag);
    while (el.childNodes.length > 6000) el.removeChild(el.firstChild);
    if ($("autoscroll").checked && atBottom) el.scrollTop = el.scrollHeight;
  }

  async function logTick() {
    try {
      const r = await api.logs(state.logCursor);
      appendLogs(r.logs || []);
      state.logCursor = r.total ?? state.logCursor;
    } catch (e) {
      /* ignore */
    }
  }

  function startLogPolling() {
    if (state.logTimer) return;
    state.logTimer = setInterval(logTick, 1000);
  }

  function stopLogPolling() {
    if (state.logTimer) {
      clearInterval(state.logTimer);
      state.logTimer = null;
    }
  }

  // ─────────────────────────────────────────────────────────
  //  Excel / results
  // ─────────────────────────────────────────────────────────
  async function onExcelReady() {
    try {
      const { sheets } = await api.sheets();
      state.sheets = sheets || [];
      renderTabs();
      if (state.sheets.length && !state.activeSheet) {
        state.activeSheet = state.sheets[0];
      }
      await loadActiveSheet();
    } catch (e) {
      // no-op
    }
  }

  function renderTabs() {
    const wrap = $("tabs");
    wrap.innerHTML = "";
    for (const s of state.sheets) {
      const btn = document.createElement("button");
      btn.className = "tab" + (s === state.activeSheet ? " active" : "");
      btn.textContent = s;
      btn.onclick = async () => {
        state.activeSheet = s;
        renderTabs();
        await loadActiveSheet();
      };
      wrap.appendChild(btn);
    }
  }

  async function loadActiveSheet() {
    const hint = $("results-hint");
    if (!state.activeSheet) {
      hint.textContent = "Run the pipeline to generate Excel";
      return;
    }
    hint.textContent = "loading…";
    try {
      const data = await api.results(state.activeSheet);
      renderTable(data);
      hint.textContent = `${data.rows.length} rows`;
    } catch (e) {
      hint.textContent = "error loading";
      toast("Failed to load results", true);
    }
  }

  function renderTable(data) {
    const thead = $("results-thead");
    const tbody = $("results-tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    if (!data || !data.columns || !data.rows) return;

    const trh = document.createElement("tr");
    for (const c of data.columns) {
      const th = document.createElement("th");
      th.textContent = c;
      trh.appendChild(th);
    }
    thead.appendChild(trh);

    if (!data.rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = data.columns.length || 1;
      td.className = "empty";
      td.textContent = "No rows in this sheet.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    // Column mapping for rendering decisions
    const colIdx = {};
    data.columns.forEach((c, i) => (colIdx[c] = i));
    const scoreCol   = data.columns.find((c) => /score/i.test(c));
    const applyCol   = data.columns.find((c) => /apply/i.test(c) && /now|link/i.test(c));
    const jobUrlCol  = data.columns.find((c) => /job\s*url|url/i.test(c) && c !== applyCol);
    const emailCol   = data.columns.find((c) => /email/i.test(c));

    for (const row of data.rows) {
      const tr = document.createElement("tr");

      for (const c of data.columns) {
        const td = document.createElement("td");
        const v = row[c];

        // Score cell — colorize
        if (c === scoreCol && typeof v === "number") {
          td.innerHTML = "";
          const sp = document.createElement("span");
          sp.className = "score " + (v >= 90 ? "s90" : v >= 80 ? "s80" : v >= 70 ? "s70" : v >= 60 ? "s60" : "sxx");
          sp.textContent = v;
          td.appendChild(sp);
          td.classList.add("num");
        }
        // Apply Now cell — use Job URL as href
        else if (c === applyCol) {
          const url = row[jobUrlCol] || null;
          if (url) {
            const a = document.createElement("a");
            a.href = url;
            a.target = "_blank";
            a.rel = "noopener";
            a.className = "link";
            a.textContent = v || "▶ Apply";
            td.appendChild(a);
          } else {
            td.textContent = v ?? "";
          }
        }
        // Job URL cell — link
        else if (c === jobUrlCol && v) {
          const a = document.createElement("a");
          a.href = v;
          a.target = "_blank";
          a.rel = "noopener";
          a.className = "link";
          a.textContent = v.length > 60 ? v.slice(0, 57) + "…" : v;
          td.appendChild(a);
        }
        // Email cell — mailto link
        else if (c === emailCol && v) {
          const a = document.createElement("a");
          a.href = "mailto:" + v;
          a.className = "link";
          a.textContent = v;
          td.appendChild(a);
        }
        // Numbers — right align
        else if (typeof v === "number") {
          td.textContent = v;
          td.classList.add("num");
        }
        // Fallback
        else {
          td.textContent = v ?? "";
        }

        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  }

  // ─────────────────────────────────────────────────────────
  //  Button handlers
  // ─────────────────────────────────────────────────────────
  function bind() {
    $("btn-run").addEventListener("click", async () => {
      // Clear log pane for a fresh run
      $("log").innerHTML = "";
      state.logCursor = 0;
      const { ok, data } = await api.run();
      if (!ok) {
        toast(data.error || "Failed to start", true);
        return;
      }
      toast("Pipeline started");
      startLogPolling();
      statusTick();
    });

    $("btn-stop").addEventListener("click", async () => {
      const { ok, data } = await api.stop();
      if (!ok) {
        toast(data.error || "Failed to stop", true);
        return;
      }
      toast("Stop signal sent");
    });

    $("btn-download").addEventListener("click", () => {
      window.location.href = "/api/download";
    });

    $("btn-refresh").addEventListener("click", () => {
      loadActiveSheet();
    });

    $("btn-clear-logs").addEventListener("click", () => {
      $("log").innerHTML = "";
    });
  }

  // ─────────────────────────────────────────────────────────
  //  Duration ticker — updates Duration label every second
  // ─────────────────────────────────────────────────────────
  function startDurationTicker() {
    setInterval(() => {
      if (!state.lastStartedAt) return;
      const end = state.lastFinishedAt || Date.now() / 1000;
      $("s-duration").textContent = fmtDuration(end - state.lastStartedAt);
    }, 1000);
  }

  // ─────────────────────────────────────────────────────────
  //  Boot
  // ─────────────────────────────────────────────────────────
  function boot() {
    bind();
    startDurationTicker();

    // Initial health check
    healthCheckOnce();
    // Schedule the first random health check
    scheduleHealthCheck();

    // Begin main status polling
    statusTick();

    // If Excel already exists on load, hydrate results
    api.status().then((s) => {
      if (s.excel_ready) onExcelReady();
    }).catch(() => {});
  }

  document.addEventListener("DOMContentLoaded", boot);
})();