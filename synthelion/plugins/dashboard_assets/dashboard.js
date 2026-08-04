(() => {
  "use strict";

  const state = { days: 30 };
  let timelineChart = null;
  let toolsChart = null;

  const fmtInt = (n) => new Intl.NumberFormat("en-US").format(Math.round(n || 0));
  const fmtPct = (n) => `${(n || 0).toFixed(1)}%`;
  const fmtCo2 = (mg) => (mg || 0) >= 1000 ? `${((mg || 0) / 1000).toFixed(2)}g` : `${(mg || 0).toFixed(1)}mg`;
  const fmtUsd = (v) => `$${(v || 0).toFixed((v || 0) < 1 ? 4 : 2)}`;
  const fmtMwh = (v) => `${(v || 0).toFixed(3)} mWh`;

  Chart.defaults.color = "#8891a0";
  Chart.defaults.borderColor = "rgba(255,255,255,0.08)";
  Chart.defaults.font.family = "system-ui, -apple-system, sans-serif";

  async function fetchJson(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      throw new Error("Unauthorized — redirecting to login");
    }
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    return res.json();
  }

  async function postJson(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      throw new Error("Unauthorized — redirecting to login");
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `${url} -> ${res.status}`);
    return body;
  }

  function daysParam() {
    return state.days ? `?days=${state.days}` : "";
  }

  async function refresh() {
    document.getElementById("refresh-btn").disabled = true;
    try {
      const [summary, records, recentRequests, sessions, decisions, storageStatus] = await Promise.all([
        fetchJson(`/api/summary${daysParam()}`),
        fetchJson(`/api/records${daysParam()}`),
        fetchJson(`/api/records${daysParam()}${daysParam() ? "&" : "?"}limit=50`),
        fetchJson(`/api/sessions${daysParam()}`),
        fetchJson(`/api/decisions?limit=20`),
        fetchJson(`/api/storage-status`),
      ]);
      renderKpis(summary, sessions.sessions || [], records.records || [], storageStatus);
      renderToolChart(summary.by_tool || {});
      renderContentTypeTable(summary.by_content_type || {});
      renderTimeline(records.records || []);
      renderSessions(sessions.sessions || []);
      renderRequests(recentRequests.records || []);
      renderDecisions(decisions);
      document.getElementById("last-updated").textContent =
        "Updated: " + new Date().toLocaleTimeString("en-US");
    } catch (err) {
      document.getElementById("last-updated").textContent = "Load error: " + err.message;
    } finally {
      document.getElementById("refresh-btn").disabled = false;
    }
  }

  function renderKpis(s, sessions, records, storageStatus) {
    document.getElementById("kpi-calls").textContent = fmtInt(s.total_calls);
    document.getElementById("kpi-saved").textContent = fmtInt(s.tokens_saved);
    document.getElementById("kpi-eff").textContent = fmtPct(s.avg_efficiency_pct);
    document.getElementById("kpi-co2").textContent = fmtCo2(s.co2_mg_saved);

    document.getElementById("kpi-sessions").textContent = fmtInt(sessions.length);
    const avgCalls = sessions.length ? sessions.reduce((sum, x) => sum + x.calls, 0) / sessions.length : 0;
    document.getElementById("kpi-avg-calls").textContent = avgCalls.toFixed(1);
    document.getElementById("kpi-tools").textContent = fmtInt(Object.keys(s.by_tool || {}).length);

    const bestSaved = records.reduce((max, r) => Math.max(max, r.tokens_saved || 0), 0);
    document.getElementById("kpi-best-call").textContent = bestSaved ? fmtInt(bestSaved) + " tok" : "–";

    document.getElementById("kpi-latency-avg").textContent = s.avg_latency_ms ? fmtMs(s.avg_latency_ms) : "–";
    document.getElementById("kpi-latency-p95").textContent = s.p95_latency_ms ? fmtMs(s.p95_latency_ms) : "–";
    document.getElementById("kpi-latency-max").textContent = s.max_latency_ms ? fmtMs(s.max_latency_ms) : "–";

    document.getElementById("kpi-cost").textContent = fmtUsd(s.cost_usd_saved);
    document.getElementById("kpi-energy").textContent = fmtMwh(s.energy_mwh_saved);
    document.getElementById("kpi-tokens-before").textContent = fmtInt(s.tokens_before);
    document.getElementById("kpi-decisions").textContent = fmtInt((storageStatus || {}).decisions);
    document.getElementById("kpi-pii-masked").textContent = fmtInt(s.pii_masked_count);
  }

  function fmtMs(ms) {
    return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
  }

  function initTooltips() {
    // KPI card labels are static DOM nodes (only their text content is refreshed on
    // each refresh()), so a single init at page load is enough — no re-init needed.
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => new bootstrap.Tooltip(el));
  }

  function renderSessions(sessions) {
    const tbody = document.getElementById("table-sessions");
    if (!sessions.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-secondary">No sessions recorded yet.</td></tr>';
      return;
    }
    tbody.innerHTML = sessions
      .map((s) => {
        const shortId = (s.session_id || "").slice(0, 8);
        const first = s.first_ts ? new Date(s.first_ts).toLocaleString("en-US") : "–";
        const last = s.last_ts ? new Date(s.last_ts).toLocaleString("en-US") : "–";
        const tools = (s.tools || []).join(", ") || "–";
        return `<tr>
          <td><code>${escapeHtml(shortId)}</code></td>
          <td>${s.pid ?? "–"}</td>
          <td class="text-end">${fmtInt(s.calls)}</td>
          <td class="text-end">${fmtInt(s.tokens_saved)}</td>
          <td class="small">${escapeHtml(tools)}</td>
          <td class="small text-secondary">${first}</td>
          <td class="small text-secondary">${last}</td>
          <td class="text-end">
            <button type="button" class="btn btn-sm btn-outline-danger py-0 px-2 delete-session-btn" data-session-id="${escapeHtml(s.session_id || "")}">Delete</button>
          </td>
        </tr>`;
      })
      .join("");
  }

  function renderRequests(records) {
    document.getElementById("requests-count").textContent = `${fmtInt(records.length)} shown`;
    const tbody = document.getElementById("table-requests");
    if (!records.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-secondary">No requests recorded yet.</td></tr>';
      return;
    }
    const sorted = [...records].sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
    tbody.innerHTML = sorted
      .map((r) => {
        const time = r.ts ? new Date(r.ts).toLocaleString("en-US") : "–";
        const eff = r.tokens_before ? ((r.tokens_saved / r.tokens_before) * 100).toFixed(1) + "%" : "–";
        const shortSession = (r.session_id || "").slice(0, 8);
        const latency = r.duration_ms ? fmtMs(r.duration_ms) : "–";
        return `<tr>
          <td class="small text-secondary">${time}</td>
          <td>${escapeHtml(r.tool || "")}</td>
          <td class="small">${escapeHtml(r.content_type || "–")}</td>
          <td class="text-end">${fmtInt(r.tokens_before)}</td>
          <td class="text-end">${fmtInt(r.tokens_after)}</td>
          <td class="text-end">${fmtInt(r.tokens_saved)}</td>
          <td class="text-end">${eff}</td>
          <td class="text-end">${latency}</td>
          <td class="small"><code>${escapeHtml(shortSession)}</code></td>
        </tr>`;
      })
      .join("");
  }

  function renderContentTypeTable(byType) {
    const tbody = document.getElementById("table-content-type");
    const entries = Object.entries(byType).sort((a, b) => b[1] - a[1]);
    if (!entries.length) {
      tbody.innerHTML = '<tr><td colspan="2" class="text-secondary">No data.</td></tr>';
      return;
    }
    tbody.innerHTML = entries
      .map(([type, saved]) => `<tr><td>${escapeHtml(type)}</td><td class="text-end">${fmtInt(saved)}</td></tr>`)
      .join("");
  }

  function renderDecisions(decisions) {
    document.getElementById("decisions-backend").textContent = decisions.backend || "–";
    const list = document.getElementById("decisions-list");
    const items = decisions.decisions || [];
    if (!items.length) {
      list.innerHTML = '<li class="list-group-item text-secondary">No decisions recorded yet.</li>';
      return;
    }
    list.innerHTML = items
      .map((d) => {
        const ts = d.ts ? new Date(d.ts * 1000).toLocaleString("en-US") : "";
        const reason = d.reason ? `<div class="text-secondary small">${escapeHtml(d.reason)}</div>` : "";
        return `<li class="list-group-item">
          <div class="small text-secondary">${ts}</div>
          <div>${escapeHtml(d.text || "")}</div>
          ${reason}
        </li>`;
      })
      .join("");
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ── charts (Chart.js, vendored locally — no CDN) ────────────────────────────
  //
  // Chart instances are created once and updated in place via chart.update().
  // Never call `new Chart(...)` again on the same <canvas> without destroying
  // the previous instance first — Chart.js does not clean up its resize
  // listener on the old instance, so each recreation compounds the canvas's
  // reported size and the chart visibly grows on every redraw.

  function renderToolChart(byTool) {
    const entries = Object.entries(byTool).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const labels = entries.map((e) => e[0]);
    const values = entries.map((e) => e[1]);

    if (!toolsChart) {
      const ctx = document.getElementById("chart-tools").getContext("2d");
      toolsChart = new Chart(ctx, {
        type: "bar",
        data: {
          labels,
          datasets: [{ label: "Tokens saved", data: values, backgroundColor: "#6ea8fe", borderRadius: 4 }],
        },
        options: chartOptions(),
      });
    } else {
      toolsChart.data.labels = labels;
      toolsChart.data.datasets[0].data = values;
      toolsChart.update();
    }
  }

  function renderTimeline(records) {
    const empty = document.getElementById("timeline-empty");
    const byDay = new Map();
    for (const r of records) {
      if (!r.ts) continue;
      const day = r.ts.slice(0, 10); // ISO date prefix
      byDay.set(day, (byDay.get(day) || 0) + (r.tokens_saved || 0));
    }
    const days = Array.from(byDay.keys()).sort().slice(-14); // keep it readable
    empty.style.display = days.length ? "none" : "block";

    const labels = days;
    const values = days.map((d) => byDay.get(d));

    if (!timelineChart) {
      const ctx = document.getElementById("chart-timeline").getContext("2d");
      timelineChart = new Chart(ctx, {
        type: "bar",
        data: {
          labels,
          datasets: [{ label: "Tokens saved", data: values, backgroundColor: "#75b798", borderRadius: 4 }],
        },
        options: chartOptions(),
      });
    } else {
      timelineChart.data.labels = labels;
      timelineChart.data.datasets[0].data = values;
      timelineChart.update();
    }
  }

  function chartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, ticks: { callback: (v) => fmtInt(v) } },
      },
    };
  }

  document.querySelectorAll(".range-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".range-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.days = btn.dataset.days ? Number(btn.dataset.days) : null;
      refresh();
    });
  });

  document.getElementById("refresh-btn").addEventListener("click", refresh);

  async function loadVersion() {
    try {
      const v = await fetchJson("/api/version");
      document.getElementById("version-badge").textContent = "v" + v.version;
      document.getElementById("version-info-current").textContent = "v" + v.version;
    } catch (err) {
      // non-critical — leave the placeholder badge in place
    }
  }

  // ── settings panel ───────────────────────────────────────────────────────

  function updateSessionBackendFields() {
    const backend = document.getElementById("settings-session-backend").value;
    document.getElementById("settings-redis-url-group").style.display = backend === "redis" ? "" : "none";
    document.getElementById("settings-postgres-dsn-group").style.display = backend === "postgres" ? "" : "none";
  }

  function updateVectorBackendFields() {
    const backend = document.getElementById("settings-vector-backend").value;
    document.getElementById("settings-qdrant-url-group").style.display = backend === "qdrant" ? "" : "none";
  }

  // Material Dashboard's ".input-group-outline" floats the <label> out of the way
  // only after the field's own "focusout" handler adds ".is-filled" to the parent
  // .input-group — which never fires when we set .value from JS (as loadSettings()
  // does), leaving the label visually stacked on top of the value/placeholder.
  // Mirror that same class toggle here right after populating each field.
  function markFilled(inputId) {
    const input = document.getElementById(inputId);
    const group = input && input.closest(".input-group");
    if (group) group.classList.toggle("is-filled", !!input.value);
  }

  async function loadSettings() {
    try {
      const { config } = await fetchJson("/api/config");
      document.getElementById("settings-compression-level").value = config.compression.default_level;
      document.getElementById("settings-wiki-depth").value = String(config.wiki.default_depth);
      document.getElementById("settings-session-backend").value = config.session_store.backend;
      document.getElementById("settings-redis-url").value = config.session_store.redis.url;
      document.getElementById("settings-postgres-dsn").value = config.session_store.postgres.dsn;
      document.getElementById("settings-vector-backend").value = config.vector_store.backend;
      document.getElementById("settings-qdrant-url").value = config.vector_store.qdrant.url;
      document.getElementById("settings-dashboard-host").value = config.dashboard.host;
      document.getElementById("settings-dashboard-port").value = String(config.dashboard.port);
      document.getElementById("settings-dashboard-realtime").value = config.dashboard.realtime;
      document.getElementById("settings-dashboard-ws-port").value = String(config.dashboard.websocket_port);
      ["settings-redis-url", "settings-postgres-dsn", "settings-qdrant-url",
       "settings-dashboard-host", "settings-dashboard-port", "settings-dashboard-ws-port"]
        .forEach(markFilled);
      updateSessionBackendFields();
      updateVectorBackendFields();
    } catch (err) {
      // non-critical — form just keeps its blank/default values
    }
  }

  async function loadStorageStatus() {
    try {
      const s = await fetchJson("/api/storage-status");
      document.getElementById("settings-info-sessions").textContent = fmtInt(s.sessions);
      document.getElementById("settings-info-decisions").textContent = fmtInt(s.decisions);
      document.getElementById("settings-info-records").textContent = fmtInt(s.ledger_records);
      document.getElementById("settings-info-backend").textContent = s.vector_backend;
    } catch (err) {
      // non-critical
    }
  }

  async function saveSettings() {
    const btn = document.getElementById("settings-save-btn");
    const status = document.getElementById("settings-status");
    btn.disabled = true;
    status.textContent = "Saving…";
    status.className = "text-sm text-secondary";
    const payload = {
      compression: { default_level: document.getElementById("settings-compression-level").value },
      wiki: { default_depth: Number(document.getElementById("settings-wiki-depth").value) },
      session_store: {
        backend: document.getElementById("settings-session-backend").value,
        redis: { url: document.getElementById("settings-redis-url").value },
        postgres: { dsn: document.getElementById("settings-postgres-dsn").value },
      },
      vector_store: {
        backend: document.getElementById("settings-vector-backend").value,
        qdrant: { url: document.getElementById("settings-qdrant-url").value },
      },
      dashboard: {
        host: document.getElementById("settings-dashboard-host").value,
        port: Number(document.getElementById("settings-dashboard-port").value),
        realtime: document.getElementById("settings-dashboard-realtime").value,
        websocket_port: Number(document.getElementById("settings-dashboard-ws-port").value),
      },
    };
    try {
      await postJson("/api/config", payload);
      status.textContent = "Saved.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("settings-session-backend").addEventListener("change", updateSessionBackendFields);
  document.getElementById("settings-vector-backend").addEventListener("change", updateVectorBackendFields);
  document.getElementById("settings-save-btn").addEventListener("click", saveSettings);

  // ── privacy & security ───────────────────────────────────────────────────

  let privacyWhitelist = [];

  function renderWhitelist() {
    const list = document.getElementById("privacy-whitelist-list");
    if (!privacyWhitelist.length) {
      list.innerHTML = '<li class="list-group-item border-0 px-0 text-sm text-secondary">No whitelisted values yet.</li>';
      return;
    }
    list.innerHTML = privacyWhitelist.map((v, i) => `
      <li class="list-group-item border-0 px-0 d-flex justify-content-between align-items-center text-sm">
        <span>${escapeHtml(v)}</span>
        <button type="button" class="btn btn-sm btn-outline-danger mb-0 py-0 px-2 privacy-whitelist-remove" data-index="${i}">Remove</button>
      </li>`).join("");
    list.querySelectorAll(".privacy-whitelist-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        privacyWhitelist.splice(Number(btn.dataset.index), 1);
        renderWhitelist();
      });
    });
  }

  async function loadPrivacySettings() {
    try {
      const { config } = await fetchJson("/api/config");
      document.getElementById("privacy-enabled").checked = config.privacy.enabled;
      document.getElementById("privacy-auto-masking").checked = config.privacy.auto_masking;
      document.getElementById("privacy-injection-guard").checked = config.privacy.prompt_injection_guard;
      document.getElementById("privacy-transparency-notice").checked = config.privacy.ai_transparency_notice;
      document.getElementById("privacy-block-on-risk").checked = config.privacy.block_on_risk;
      document.getElementById("privacy-block-min-score").value = String(config.privacy.block_min_score || 61);
      document.getElementById("privacy-language").value = config.privacy.language;
      document.getElementById("privacy-transparency-custom").value = config.privacy.transparency_custom_message || "";
      markFilled("privacy-transparency-custom");
      privacyWhitelist = (config.privacy.whitelist || []).slice();
      renderWhitelist();
    } catch (err) {
      // non-critical — form just keeps its blank/default values
    }
  }

  async function savePrivacySettings() {
    const btn = document.getElementById("privacy-save-btn");
    const status = document.getElementById("privacy-save-status");
    btn.disabled = true;
    status.textContent = "Saving…";
    status.className = "text-sm text-secondary";
    const payload = {
      privacy: {
        enabled: document.getElementById("privacy-enabled").checked,
        auto_masking: document.getElementById("privacy-auto-masking").checked,
        prompt_injection_guard: document.getElementById("privacy-injection-guard").checked,
        ai_transparency_notice: document.getElementById("privacy-transparency-notice").checked,
        block_on_risk: document.getElementById("privacy-block-on-risk").checked,
        block_min_score: Number(document.getElementById("privacy-block-min-score").value),
        language: document.getElementById("privacy-language").value,
        transparency_custom_message: document.getElementById("privacy-transparency-custom").value,
        whitelist: privacyWhitelist,
      },
    };
    try {
      await postJson("/api/config", payload);
      status.textContent = "Saved.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("privacy-save-btn").addEventListener("click", savePrivacySettings);

  document.getElementById("privacy-whitelist-add-btn").addEventListener("click", () => {
    const input = document.getElementById("privacy-whitelist-input");
    const value = input.value.trim();
    if (value && !privacyWhitelist.includes(value)) {
      privacyWhitelist.push(value);
      renderWhitelist();
    }
    input.value = "";
  });

  document.getElementById("privacy-test-btn").addEventListener("click", async () => {
    const text = document.getElementById("privacy-test-input").value;
    const language = document.getElementById("privacy-language").value;
    try {
      const r = await postJson("/api/privacy-test", { text, language });
      document.getElementById("privacy-test-score").textContent = r.privacy.score;
      document.getElementById("privacy-test-risk").textContent = r.privacy.risk_level;
      document.getElementById("privacy-test-categories").textContent =
        r.privacy.detected_categories.length ? r.privacy.detected_categories.join(", ") : "none";
      document.getElementById("privacy-test-compliance").textContent =
        r.privacy.compliance_flags.length ? r.privacy.compliance_flags.join(", ") : "none";
      document.getElementById("privacy-test-masked").textContent = r.privacy.masked_text || "(no PII detected)";
      document.getElementById("privacy-test-injection-score").textContent = r.prompt_injection.score;
      document.getElementById("privacy-test-injection-risk").textContent = r.prompt_injection.risk_level;
      document.getElementById("privacy-test-injection-categories").textContent =
        r.prompt_injection.detected_categories.length ? r.prompt_injection.detected_categories.join(", ") : "none";
    } catch (err) {
      document.getElementById("privacy-test-risk").textContent = "Error: " + err.message;
    }
  });

  // ── security (WAF & firewall) ────────────────────────────────────────────

  async function loadWafSettings() {
    try {
      const { config } = await fetchJson("/api/config");
      const w = config.waf;
      document.getElementById("waf-enabled").checked = w.enabled;
      document.getElementById("waf-block-mode").checked = w.block_mode;
      document.getElementById("waf-rule-sql").checked = w.rule_sql_injection;
      document.getElementById("waf-rule-xss").checked = w.rule_xss;
      document.getElementById("waf-rule-path").checked = w.rule_path_traversal;
      document.getElementById("waf-rule-cmd").checked = w.rule_command_injection;
      document.getElementById("waf-rule-ua").checked = w.rule_bad_user_agent;
      document.getElementById("waf-rule-scanner").checked = w.rule_scanner_probe;
      document.getElementById("waf-inspect-body").checked = w.inspect_body;
      document.getElementById("waf-skip-authenticated").checked = w.skip_authenticated;
      document.getElementById("waf-autoban-enabled").checked = w.auto_ban_enabled;
      document.getElementById("waf-autoban-threshold").value = w.auto_ban_threshold;
      document.getElementById("waf-autoban-window").value = w.auto_ban_window_minutes;
      document.getElementById("waf-autoban-duration").value = w.auto_ban_duration_minutes;
      document.getElementById("waf-ratelimit-enabled").checked = w.rate_limit_enabled;
      document.getElementById("waf-ratelimit-rpm").value = w.rate_limit_requests_per_minute;
      document.getElementById("waf-ratelimit-ban").value = w.rate_limit_ban_minutes;
      document.getElementById("waf-block-status").value = w.block_status_code;
      document.getElementById("waf-log-retention").value = w.log_retention_days;
      document.getElementById("waf-block-message").value = w.block_message || "";
      document.getElementById("waf-excluded-paths").value = (w.excluded_paths || []).join("\n");
      ["waf-autoban-threshold", "waf-autoban-window", "waf-autoban-duration",
       "waf-ratelimit-rpm", "waf-ratelimit-ban", "waf-block-status", "waf-log-retention",
       "waf-block-message"].forEach(markFilled);
    } catch (err) {
      // non-critical — form just keeps its blank/default values
    }
  }

  async function saveWafSettings() {
    const btn = document.getElementById("waf-save-btn");
    const status = document.getElementById("waf-save-status");
    btn.disabled = true;
    status.textContent = "Saving…";
    status.className = "text-sm text-secondary";
    const payload = {
      waf: {
        enabled: document.getElementById("waf-enabled").checked,
        block_mode: document.getElementById("waf-block-mode").checked,
        rule_sql_injection: document.getElementById("waf-rule-sql").checked,
        rule_xss: document.getElementById("waf-rule-xss").checked,
        rule_path_traversal: document.getElementById("waf-rule-path").checked,
        rule_command_injection: document.getElementById("waf-rule-cmd").checked,
        rule_bad_user_agent: document.getElementById("waf-rule-ua").checked,
        rule_scanner_probe: document.getElementById("waf-rule-scanner").checked,
        inspect_body: document.getElementById("waf-inspect-body").checked,
        skip_authenticated: document.getElementById("waf-skip-authenticated").checked,
        auto_ban_enabled: document.getElementById("waf-autoban-enabled").checked,
        auto_ban_threshold: Number(document.getElementById("waf-autoban-threshold").value),
        auto_ban_window_minutes: Number(document.getElementById("waf-autoban-window").value),
        auto_ban_duration_minutes: Number(document.getElementById("waf-autoban-duration").value),
        rate_limit_enabled: document.getElementById("waf-ratelimit-enabled").checked,
        rate_limit_requests_per_minute: Number(document.getElementById("waf-ratelimit-rpm").value),
        rate_limit_ban_minutes: Number(document.getElementById("waf-ratelimit-ban").value),
        block_status_code: Number(document.getElementById("waf-block-status").value),
        log_retention_days: Number(document.getElementById("waf-log-retention").value),
        block_message: document.getElementById("waf-block-message").value,
        excluded_paths: document.getElementById("waf-excluded-paths").value
          .split("\n").map((s) => s.trim()).filter(Boolean),
      },
    };
    try {
      await postJson("/api/config", payload);
      status.textContent = "Saved.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("waf-save-btn").addEventListener("click", saveWafSettings);

  async function loadWafIpRules() {
    const tbody = document.getElementById("waf-ip-rules-body");
    try {
      const { rules } = await fetchJson("/api/waf/ip-rules");
      if (!rules.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-sm text-secondary">No IP rules yet.</td></tr>';
        return;
      }
      tbody.innerHTML = rules.map((r) => `
        <tr>
          <td class="text-sm"><code>${escapeHtml(r.ip)}</code></td>
          <td class="text-sm">${escapeHtml(r.kind)}${r.auto ? ' <span class="badge badge-sm bg-gradient-secondary">auto</span>' : ""}</td>
          <td class="text-sm">${escapeHtml(r.reason || "–")}</td>
          <td class="text-sm">${r.expires_at ? new Date(r.expires_at * 1000).toLocaleString() : "never"}</td>
          <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-2 waf-ip-remove-btn" data-ip="${escapeHtml(r.ip)}" data-kind="${escapeHtml(r.kind)}">Remove</button></td>
        </tr>`).join("");
      tbody.querySelectorAll(".waf-ip-remove-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
          await postJson("/api/waf/ip-rules/delete", { ip: btn.dataset.ip, kind: btn.dataset.kind });
          loadWafIpRules();
        });
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-sm text-danger">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  document.getElementById("waf-ip-add-btn").addEventListener("click", async () => {
    const ip = document.getElementById("waf-ip-input").value.trim();
    if (!ip) return;
    const kind = document.getElementById("waf-ip-kind").value;
    const reason = document.getElementById("waf-ip-reason").value.trim() || "Manual";
    try {
      await postJson("/api/waf/ip-rules", { ip, kind, reason });
      document.getElementById("waf-ip-input").value = "";
      document.getElementById("waf-ip-reason").value = "";
      loadWafIpRules();
    } catch (err) {
      // non-critical — the table just doesn't update
    }
  });

  async function loadWafEvents() {
    const tbody = document.getElementById("waf-events-body");
    try {
      const { events } = await fetchJson("/api/waf/events?limit=100");
      if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-sm text-secondary">No events yet.</td></tr>';
        return;
      }
      tbody.innerHTML = events.map((e) => {
        const cls = e.action === "Blocked" ? "text-danger" : "text-warning";
        return `
        <tr>
          <td class="text-sm">${new Date(e.ts).toLocaleString()}</td>
          <td class="text-sm"><code>${escapeHtml(e.ip || "–")}</code></td>
          <td class="text-sm">${escapeHtml(e.method || "")} ${escapeHtml(e.path || "")}</td>
          <td class="text-sm">${escapeHtml(e.category || "")}</td>
          <td class="text-sm">${escapeHtml(e.rule_name || "")}</td>
          <td class="text-sm">${escapeHtml(e.severity || "")}</td>
          <td class="text-sm ${cls}">${escapeHtml(e.action || "")}</td>
        </tr>`;
      }).join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-sm text-danger">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  // ── EnterpriseGuard (outbound DLP firewall) ─────────────────────────────────

  const EG_CATEGORIES = [
    "cloud_credentials", "database_connections", "ftp_credentials",
    "git_credentials", "private_keys", "api_tokens", "dotenv_bulk",
  ];

  async function loadEnterpriseGuardSettings() {
    try {
      const { config } = await fetchJson("/api/config");
      const eg = config.enterprise_guard || {};
      document.getElementById("eg-enabled").checked = eg.enabled !== false;
      document.getElementById("eg-use-defaults").checked = eg.use_default_blocked_paths !== false;
      const cats = eg.content_categories || {};
      EG_CATEGORIES.forEach((c) => {
        const el = document.getElementById("eg-cat-" + c);
        if (el) el.checked = cats[c] !== false;
      });
      document.getElementById("eg-blocked-paths").value = (eg.blocked_paths || []).join("\n");
    } catch (err) {
      // non-critical — form just keeps its blank/default values
    }
  }

  async function saveEnterpriseGuardSettings() {
    const btn = document.getElementById("eg-save-btn");
    const status = document.getElementById("eg-save-status");
    btn.disabled = true;
    status.textContent = "Saving…";
    status.className = "text-sm text-secondary";
    const content_categories = {};
    EG_CATEGORIES.forEach((c) => {
      const el = document.getElementById("eg-cat-" + c);
      if (el) content_categories[c] = el.checked;
    });
    const payload = {
      enterprise_guard: {
        enabled: document.getElementById("eg-enabled").checked,
        use_default_blocked_paths: document.getElementById("eg-use-defaults").checked,
        content_categories,
        blocked_paths: document.getElementById("eg-blocked-paths").value
          .split("\n").map((s) => s.trim()).filter(Boolean),
      },
    };
    try {
      await postJson("/api/config", payload);
      status.textContent = "Saved.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("eg-save-btn").addEventListener("click", saveEnterpriseGuardSettings);

  document.getElementById("eg-test-btn").addEventListener("click", async () => {
    const resultDiv = document.getElementById("eg-test-result");
    resultDiv.innerHTML = '<span class="text-sm text-secondary">Checking…</span>';
    try {
      const r = await postJson("/api/enterprise-guard-test", {
        text: document.getElementById("eg-test-text").value,
        path: document.getElementById("eg-test-path").value,
      });
      const rows = [];
      if (r.text_result) rows.push(["Text", r.text_result]);
      if (r.path_result) rows.push(["Path", r.path_result]);
      if (!rows.length) {
        resultDiv.innerHTML = '<span class="text-sm text-secondary">Enter text and/or a path above, then Test.</span>';
        return;
      }
      resultDiv.innerHTML = rows.map(([label, res]) => {
        const cls = res.blocked ? "text-danger" : "text-success";
        const verdict = res.blocked ? "BLOCKED" : "Allowed";
        const detail = res.blocked ? ` — ${escapeHtml(res.category || "")} / ${escapeHtml(res.rule_name || "")}` : "";
        return `<div class="text-sm ${cls}"><strong>${label}: ${verdict}</strong>${detail}</div>`;
      }).join("");
    } catch (err) {
      resultDiv.innerHTML = `<span class="text-sm text-danger">Error: ${escapeHtml(err.message)}</span>`;
    }
  });

  async function loadEnterpriseGuardEvents() {
    const tbody = document.getElementById("eg-events-body");
    try {
      const { events } = await fetchJson("/api/enterprise-guard/events?limit=100");
      if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-sm text-secondary">No blocks yet.</td></tr>';
        return;
      }
      tbody.innerHTML = events.map((e) => `
        <tr>
          <td class="text-sm">${new Date(e.timestamp * 1000).toLocaleString()}</td>
          <td class="text-sm">${escapeHtml(e.source || "")}</td>
          <td class="text-sm">${escapeHtml(e.category || "")}</td>
          <td class="text-sm">${escapeHtml(e.rule_name || "")}</td>
        </tr>`).join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="4" class="text-sm text-danger">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  async function loadEnterpriseGuardClients() {
    const tbody = document.getElementById("eg-clients-body");
    try {
      const { clients } = await fetchJson("/api/enterprise-guard/clients");
      if (!clients.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-sm text-secondary">No clients registered.</td></tr>';
        return;
      }
      tbody.innerHTML = clients.map((c) => `
        <tr>
          <td class="text-sm">${escapeHtml(c.label || "(no label)")}${c.auto_discovered ? ' <span class="badge badge-sm bg-gradient-secondary">auto-discovered</span>' : ""}</td>
          <td class="text-sm"><code>${escapeHtml(c.ip || "–")}</code></td>
          <td class="text-sm"><code>${escapeHtml(c.mac || "–")}</code></td>
          <td class="text-sm">${c.blocked_paths.length} path(s)</td>
          <td>
            <div class="form-check form-switch mb-0">
              <input class="form-check-input eg-client-enable-toggle" type="checkbox" role="switch" data-id="${escapeHtml(c.id)}" ${c.enabled ? "checked" : ""}>
            </div>
          </td>
          <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-2 eg-client-remove-btn" data-id="${escapeHtml(c.id)}">Remove</button></td>
        </tr>`).join("");
      tbody.querySelectorAll(".eg-client-enable-toggle").forEach((el) => {
        el.addEventListener("change", async () => {
          await postJson("/api/enterprise-guard/clients/update", { id: el.dataset.id, enabled: el.checked });
        });
      });
      tbody.querySelectorAll(".eg-client-remove-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
          await postJson("/api/enterprise-guard/clients/delete", { id: btn.dataset.id });
          loadEnterpriseGuardClients();
        });
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-sm text-danger">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  document.getElementById("eg-client-add-btn").addEventListener("click", async () => {
    const btn = document.getElementById("eg-client-add-btn");
    btn.disabled = true;
    try {
      await postJson("/api/enterprise-guard/clients", {
        label: document.getElementById("eg-client-label").value,
        ip: document.getElementById("eg-client-ip").value,
        mac: document.getElementById("eg-client-mac").value,
        blocked_paths: document.getElementById("eg-client-paths").value
          .split("\n").map((s) => s.trim()).filter(Boolean),
        enabled: true,
      });
      document.getElementById("eg-client-label").value = "";
      document.getElementById("eg-client-ip").value = "";
      document.getElementById("eg-client-mac").value = "";
      document.getElementById("eg-client-paths").value = "";
      loadEnterpriseGuardClients();
    } finally {
      btn.disabled = false;
    }
  });

  // ── proxy ─────────────────────────────────────────────────────────────────

  let _proxyRoutes = [];
  let _proxyFallbacks = [];

  async function loadProxyStatus() {
    try {
      const s = await fetchJson("/api/proxy/status");
      const badge = document.getElementById("proxy-state-badge");
      badge.textContent = s.running ? "Running" : "Stopped";
      badge.className = "badge " + (s.running ? "bg-gradient-success" : "bg-gradient-secondary");
      document.getElementById("proxy-address").textContent = `${s.host}:${s.port}`;
      document.getElementById("proxy-pid").textContent = s.pid || "–";
    } catch (err) {
      document.getElementById("proxy-control-status").textContent = "Error: " + err.message;
    }
  }

  document.getElementById("proxy-start-btn").addEventListener("click", async () => {
    const status = document.getElementById("proxy-control-status");
    status.textContent = "Starting…";
    status.className = "text-sm text-secondary";
    try {
      const r = await postJson("/api/proxy/start", {});
      status.textContent = r.status === "already_running" ? "Already running." : "Started.";
      status.className = "text-sm text-success";
      setTimeout(loadProxyStatus, 800);
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  document.getElementById("proxy-stop-btn").addEventListener("click", async () => {
    const status = document.getElementById("proxy-control-status");
    status.textContent = "Stopping…";
    status.className = "text-sm text-secondary";
    try {
      const r = await postJson("/api/proxy/stop", {});
      status.textContent = r.status === "not_running" ? "Was not running." : "Stopped.";
      status.className = "text-sm text-success";
      setTimeout(loadProxyStatus, 500);
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  async function loadProxyConfig() {
    try {
      const { config } = await fetchJson("/api/config");
      const p = config.proxy || {};
      document.getElementById("proxy-enabled").checked = !!p.enabled;
      document.getElementById("proxy-host").value = p.host || "127.0.0.1";
      document.getElementById("proxy-port").value = p.port || 8788;
      document.getElementById("proxy-anthropic-upstream").value = p.anthropic_upstream || "";
      document.getElementById("proxy-openai-upstream").value = p.openai_upstream || "";
      document.getElementById("proxy-gemini-upstream").value = p.gemini_upstream || "";
      document.getElementById("proxy-default-upstream").value = p.default_upstream || "";
      document.getElementById("proxy-cb-enabled").checked = p.circuit_breaker_enabled !== false;
      document.getElementById("proxy-cb-threshold").value = p.circuit_breaker_threshold ?? 3;
      document.getElementById("proxy-cb-window").value = p.circuit_breaker_window_seconds ?? 60;
      document.getElementById("proxy-cb-cooldown").value = p.circuit_breaker_cooldown_seconds ?? 30;
      document.getElementById("proxy-rh-enabled").checked = p.rolling_history_enabled !== false;
      document.getElementById("proxy-rh-threshold").value = p.rolling_history_threshold ?? 6;
      document.getElementById("proxy-ccr-enabled").checked = !!p.ccr_enabled;
      document.getElementById("proxy-ccr-min-saved").value = p.ccr_min_tokens_saved ?? 15;
      document.getElementById("proxy-ccr-ttl").value = p.ccr_ttl_seconds ?? 3600;
      document.getElementById("proxy-cache-enabled").checked = !!p.response_cache_enabled;
      document.getElementById("proxy-cache-ttl").value = p.response_cache_ttl_seconds ?? 120;
      document.getElementById("proxy-cache-max").value = p.response_cache_max_entries ?? 200;
      document.getElementById("proxy-budget").value = p.daily_budget_usd ?? 0;
      document.getElementById("proxy-output-shaping").checked = !!p.output_shaping_enabled;
      _proxyRoutes = (p.custom_routes || []).slice();
      _proxyFallbacks = (p.fallback_upstreams || []).slice();
      renderProxyRoutes();
      renderProxyFallbacks();
    } catch (err) {
      document.getElementById("proxy-upstreams-status").textContent = "Error: " + err.message;
    }
  }

  function renderProxyRoutes() {
    const tbody = document.getElementById("proxy-routes-table");
    if (!_proxyRoutes.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="text-secondary text-sm">No custom routes — built-in Anthropic/OpenAI/Gemini routing applies.</td></tr>';
      return;
    }
    tbody.innerHTML = _proxyRoutes.map((r, i) => `
      <tr>
        <td class="text-sm">${escapeHtml(r.label || "")}</td>
        <td class="text-sm"><code>${escapeHtml(r.path_prefix || "")}</code></td>
        <td class="text-sm">${escapeHtml(r.upstream || "")}</td>
        <td><button type="button" class="btn btn-link text-danger p-0 m-0 proxy-route-remove" data-idx="${i}">Remove</button></td>
      </tr>`).join("");
    tbody.querySelectorAll(".proxy-route-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        _proxyRoutes.splice(Number(btn.dataset.idx), 1);
        renderProxyRoutes();
        saveProxyRoutes();
      });
    });
  }

  function renderProxyFallbacks() {
    const el = document.getElementById("proxy-fallback-list");
    if (!_proxyFallbacks.length) {
      el.innerHTML = '<span class="text-secondary text-sm">No backup upstreams configured.</span>';
      return;
    }
    el.innerHTML = _proxyFallbacks.map((url, i) => `
      <span class="badge bg-gradient-secondary me-1 mb-1">
        ${escapeHtml(url)} <a href="#" class="text-white proxy-fallback-remove" data-idx="${i}" style="text-decoration:none">&times;</a>
      </span>`).join("");
    el.querySelectorAll(".proxy-fallback-remove").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        _proxyFallbacks.splice(Number(a.dataset.idx), 1);
        renderProxyFallbacks();
        saveProxyFallbacks();
      });
    });
  }

  async function saveProxyRoutes() {
    try {
      await postJson("/api/config", { proxy: { custom_routes: _proxyRoutes } });
    } catch (err) {
      document.getElementById("proxy-upstreams-status").textContent = "Error saving routes: " + err.message;
    }
  }

  async function saveProxyFallbacks() {
    try {
      await postJson("/api/config", { proxy: { fallback_upstreams: _proxyFallbacks } });
    } catch (err) {
      document.getElementById("proxy-reliability-status").textContent = "Error saving fallbacks: " + err.message;
    }
  }

  document.getElementById("proxy-save-upstreams-btn").addEventListener("click", async () => {
    const status = document.getElementById("proxy-upstreams-status");
    try {
      await postJson("/api/config", {
        proxy: {
          enabled: document.getElementById("proxy-enabled").checked,
          host: document.getElementById("proxy-host").value.trim(),
          port: parseInt(document.getElementById("proxy-port").value, 10) || 8788,
          anthropic_upstream: document.getElementById("proxy-anthropic-upstream").value.trim(),
          openai_upstream: document.getElementById("proxy-openai-upstream").value.trim(),
          gemini_upstream: document.getElementById("proxy-gemini-upstream").value.trim(),
          default_upstream: document.getElementById("proxy-default-upstream").value.trim(),
        },
      });
      status.textContent = "Saved. Restart the proxy for changes to take effect.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  document.getElementById("proxy-save-reliability-btn").addEventListener("click", async () => {
    const status = document.getElementById("proxy-reliability-status");
    try {
      await postJson("/api/config", {
        proxy: {
          circuit_breaker_enabled: document.getElementById("proxy-cb-enabled").checked,
          circuit_breaker_threshold: parseInt(document.getElementById("proxy-cb-threshold").value, 10) || 3,
          circuit_breaker_window_seconds: parseInt(document.getElementById("proxy-cb-window").value, 10) || 60,
          circuit_breaker_cooldown_seconds: parseInt(document.getElementById("proxy-cb-cooldown").value, 10) || 30,
        },
      });
      status.textContent = "Saved. Restart the proxy for changes to take effect.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  document.getElementById("proxy-save-advanced-btn").addEventListener("click", async () => {
    const status = document.getElementById("proxy-advanced-status");
    try {
      await postJson("/api/config", {
        proxy: {
          rolling_history_enabled: document.getElementById("proxy-rh-enabled").checked,
          rolling_history_threshold: parseInt(document.getElementById("proxy-rh-threshold").value, 10) || 6,
          ccr_enabled: document.getElementById("proxy-ccr-enabled").checked,
          ccr_min_tokens_saved: parseInt(document.getElementById("proxy-ccr-min-saved").value, 10) || 15,
          ccr_ttl_seconds: parseInt(document.getElementById("proxy-ccr-ttl").value, 10) || 3600,
          response_cache_enabled: document.getElementById("proxy-cache-enabled").checked,
          response_cache_ttl_seconds: parseInt(document.getElementById("proxy-cache-ttl").value, 10) || 120,
          response_cache_max_entries: parseInt(document.getElementById("proxy-cache-max").value, 10) || 200,
          daily_budget_usd: parseFloat(document.getElementById("proxy-budget").value) || 0,
          output_shaping_enabled: document.getElementById("proxy-output-shaping").checked,
        },
      });
      status.textContent = "Saved. Restart the proxy for changes to take effect.";
      status.className = "text-sm text-success";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  document.getElementById("proxy-route-add-btn").addEventListener("click", () => {
    const label = document.getElementById("proxy-route-label").value.trim();
    const prefix = document.getElementById("proxy-route-prefix").value.trim();
    const upstream = document.getElementById("proxy-route-upstream").value.trim();
    if (!prefix || !upstream) return;
    _proxyRoutes.push({ label, path_prefix: prefix, upstream });
    document.getElementById("proxy-route-label").value = "";
    document.getElementById("proxy-route-prefix").value = "/";
    document.getElementById("proxy-route-upstream").value = "";
    renderProxyRoutes();
    saveProxyRoutes();
  });

  document.getElementById("proxy-fallback-add-btn").addEventListener("click", () => {
    const input = document.getElementById("proxy-fallback-input");
    const url = input.value.trim();
    if (!url) return;
    if (_proxyFallbacks.length >= 10) {
      document.getElementById("proxy-reliability-status").textContent = "Maximum of 10 backup upstreams.";
      return;
    }
    _proxyFallbacks.push(url);
    input.value = "";
    renderProxyFallbacks();
    saveProxyFallbacks();
  });

  document.getElementById("proxy-load-providers-btn").addEventListener("click", async () => {
    const select = document.getElementById("proxy-provider-picker");
    select.innerHTML = '<option value="">Loading…</option>';
    try {
      const { providers, error } = await fetchJson("/api/proxy/providers");
      if (error) {
        select.innerHTML = `<option value="">Error: ${escapeHtml(error)}</option>`;
        return;
      }
      select.innerHTML = '<option value="">Pick a provider…</option>' +
        providers.map((p) => `<option value="${escapeHtml(p.api)}" data-name="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`).join("");
    } catch (err) {
      select.innerHTML = `<option value="">Error: ${escapeHtml(err.message)}</option>`;
    }
  });

  document.getElementById("proxy-provider-picker").addEventListener("change", (ev) => {
    const opt = ev.target.selectedOptions[0];
    if (!opt || !opt.value) return;
    document.getElementById("proxy-route-upstream").value = opt.value;
    document.getElementById("proxy-route-label").value = opt.dataset.name || "";
  });

  async function loadProxyLogs() {
    const tbody = document.getElementById("proxy-logs-table");
    try {
      const { logs } = await fetchJson("/api/proxy/logs?limit=100");
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-sm text-secondary">No proxy calls yet.</td></tr>';
        return;
      }
      tbody.innerHTML = logs.map((l) => {
        let resultCls = "text-success", resultText = "OK";
        if (l.blocked) { resultCls = "text-warning"; resultText = "Blocked (privacy)"; }
        else if (!l.responded) { resultCls = "text-danger"; resultText = "Failed"; }
        const saved = l.tokens_before > 0 ? `${Math.round((1 - l.tokens_after / l.tokens_before) * 100)}%` : "–";
        return `
        <tr>
          <td class="text-sm">${new Date(l.ts).toLocaleTimeString()}</td>
          <td class="text-sm">${escapeHtml(l.method || "")}</td>
          <td class="text-sm">${escapeHtml(l.path || "")}</td>
          <td class="text-sm">${escapeHtml((l.upstream || "").replace(/^https?:\/\//, ""))}</td>
          <td class="text-sm text-end">${l.status_code ?? "–"}</td>
          <td class="text-sm text-end">${Math.round(l.duration_ms)}ms</td>
          <td class="text-sm text-end">${saved}</td>
          <td class="text-sm ${resultCls}">${resultText}</td>
        </tr>`;
      }).join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-sm text-danger">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  // ── doctor / version / upgrade ───────────────────────────────────────────

  function doctorIcon(status) {
    if (status === "ok") return '<span class="text-success">✓</span>';
    if (status === "warn") return '<span class="text-warning">!</span>';
    return '<span class="text-danger">✗</span>';
  }

  document.getElementById("doctor-run-btn").addEventListener("click", async () => {
    const list = document.getElementById("doctor-list");
    list.innerHTML = '<li class="list-group-item border-0 px-0 text-sm text-secondary">Running…</li>';
    try {
      const { checks } = await fetchJson("/api/doctor");
      list.innerHTML = checks
        .map((c) => `<li class="list-group-item border-0 px-0 text-sm">${doctorIcon(c.status)} <strong>${escapeHtml(c.check)}</strong> — ${escapeHtml(c.detail)}</li>`)
        .join("");
    } catch (err) {
      list.innerHTML = `<li class="list-group-item border-0 px-0 text-sm text-danger">Error: ${escapeHtml(err.message)}</li>`;
    }
  });

  document.getElementById("version-check-btn").addEventListener("click", async () => {
    const status = document.getElementById("version-status");
    const upgradeBtn = document.getElementById("version-upgrade-btn");
    status.textContent = "Checking PyPI…";
    status.className = "text-sm text-secondary";
    try {
      const r = await fetchJson("/api/version-check");
      if (r.error) {
        status.textContent = "Could not reach PyPI: " + r.error;
        status.className = "text-sm text-danger";
        return;
      }
      if (r.update_available) {
        status.textContent = `Update available: v${r.current} → v${r.latest}`;
        status.className = "text-sm text-warning";
        upgradeBtn.style.display = "";
      } else {
        status.textContent = `Up to date (v${r.current}).`;
        status.className = "text-sm text-success";
        upgradeBtn.style.display = "none";
      }
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  document.getElementById("version-upgrade-btn").addEventListener("click", async () => {
    const status = document.getElementById("version-status");
    const btn = document.getElementById("version-upgrade-btn");
    const restartBtn = document.getElementById("version-restart-btn");
    btn.disabled = true;
    status.textContent = "Upgrading — this can take a minute…";
    status.className = "text-sm text-secondary";
    try {
      const r = await postJson("/api/upgrade", {});
      if (r.success) {
        status.textContent = "Upgraded. The MCP server needs a manual restart (by your agent/host) — the dashboard can restart itself below.";
        status.className = "text-sm text-success";
        btn.style.display = "none";
        restartBtn.style.display = "";
      } else {
        status.textContent = "Upgrade failed — see server log.";
        status.className = "text-sm text-danger";
        btn.disabled = false;
      }
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
      btn.disabled = false;
    }
  });

  document.getElementById("version-restart-btn").addEventListener("click", async () => {
    const status = document.getElementById("version-status");
    const btn = document.getElementById("version-restart-btn");
    btn.disabled = true;
    status.textContent = "Restarting dashboard…";
    status.className = "text-sm text-secondary";
    try {
      await postJson("/api/restart", {});
    } catch {
      // The connection drops as soon as the process re-execs — expected, not an error.
    }
    let attempts = 0;
    const poll = setInterval(async () => {
      attempts += 1;
      try {
        const res = await fetch(window.location.origin + "/login", { method: "GET" });
        if (res.ok || res.status === 200) {
          clearInterval(poll);
          window.location.reload();
        }
      } catch {
        // still restarting — server not accepting connections yet
      }
      if (attempts > 30) {
        clearInterval(poll);
        status.textContent = "Restart is taking longer than expected — reload the page manually.";
        status.className = "text-sm text-warning";
      }
    }, 1000);
  });

  // ── sessions cleanup ─────────────────────────────────────────────────────

  document.querySelectorAll(".prune-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const status = document.getElementById("prune-status");
      status.textContent = "Working…";
      status.className = "text-sm text-secondary";
      try {
        const r = await postJson("/api/sessions/prune", { days: Number(btn.dataset.days) });
        status.textContent = `Removed ${r.removed} record(s) older than ${r.days} days.`;
        status.className = "text-sm text-success";
        refresh();
        loadStorageStatus();
      } catch (err) {
        status.textContent = "Error: " + err.message;
        status.className = "text-sm text-danger";
      }
    });
  });

  document.getElementById("table-sessions").addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".delete-session-btn");
    if (!btn) return;
    const sessionId = btn.dataset.sessionId;
    if (!sessionId) return;
    btn.disabled = true;
    try {
      await postJson("/api/sessions/delete", { session_id: sessionId });
      refresh();
      loadStorageStatus();
    } catch (err) {
      btn.disabled = false;
      alert("Delete failed: " + err.message);
    }
  });

  document.querySelectorAll(".prune-decisions-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const status = document.getElementById("prune-decisions-status");
      status.textContent = "Working…";
      status.className = "text-sm text-secondary";
      try {
        const r = await postJson("/api/decisions/prune", { days: Number(btn.dataset.days) });
        status.textContent = `Removed ${r.removed} decision(s) older than ${r.days} days.`;
        status.className = "text-sm text-success";
        refresh();
        loadStorageStatus();
      } catch (err) {
        status.textContent = "Error: " + err.message;
        status.className = "text-sm text-danger";
      }
    });
  });

  // ── profile / account ────────────────────────────────────────────────────

  function setProfileUsername(username) {
    document.getElementById("profile-heading-username").textContent = username;
    document.getElementById("profile-info-username").textContent = username;
  }

  async function loadProfile() {
    try {
      const [account, version, { config }] = await Promise.all([
        fetchJson("/api/account"),
        fetchJson("/api/version"),
        fetchJson("/api/config"),
      ]);
      setProfileUsername(account.username);
      document.getElementById("profile-info-version").textContent = "v" + version.version;
      document.getElementById("profile-info-session-store").textContent = config.session_store.backend;
      document.getElementById("profile-info-vector-store").textContent = config.vector_store.backend;
    } catch (err) {
      // non-critical
    }
  }

  document.getElementById("profile-save-btn").addEventListener("click", async () => {
    const status = document.getElementById("profile-status");
    const currentPassword = document.getElementById("profile-current-password").value;
    const newUsername = document.getElementById("profile-new-username").value;
    const newPassword = document.getElementById("profile-new-password").value;
    status.textContent = "Saving…";
    status.className = "text-sm text-secondary";
    try {
      const r = await postJson("/api/account", {
        current_password: currentPassword,
        new_username: newUsername,
        new_password: newPassword,
      });
      status.textContent = "Credentials updated.";
      status.className = "text-sm text-success";
      setProfileUsername(r.username);
      document.getElementById("profile-current-password").value = "";
      document.getElementById("profile-new-username").value = "";
      document.getElementById("profile-new-password").value = "";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.className = "text-sm text-danger";
    }
  });

  // ── notifications ────────────────────────────────────────────────────────

  function notificationItemHtml(n) {
    const cls = n.level === "error" ? "text-danger" : "text-warning";
    return `<div class="d-flex flex-column py-1">
      <span class="text-sm font-weight-bold ${cls}">${escapeHtml(n.title)}</span>
      <span class="text-xs text-secondary">${escapeHtml(n.message)}</span>
    </div>`;
  }

  async function loadNotifications() {
    try {
      const { notifications } = await fetchJson("/api/notifications");
      const badge = document.getElementById("notif-badge");
      if (notifications.length) {
        badge.style.display = "";
        badge.textContent = String(notifications.length);
      } else {
        badge.style.display = "none";
      }

      const dropdown = document.getElementById("notif-dropdown");
      dropdown.innerHTML = notifications.length
        ? notifications.map((n) => `<li class="mb-1 px-2">${notificationItemHtml(n)}</li>`).join("")
        : '<li class="text-secondary text-sm px-2">No notifications.</li>';

      const pageList = document.getElementById("notif-page-list");
      pageList.innerHTML = notifications.length
        ? notifications.map((n) => `<li class="list-group-item">${notificationItemHtml(n)}</li>`).join("")
        : '<li class="list-group-item text-secondary">No notifications — everything looks fine.</li>';

      const profileList = document.getElementById("profile-notifications-list");
      if (profileList) {
        profileList.innerHTML = notifications.length
          ? notifications.map((n) => `<li class="list-group-item border-0 px-0">${notificationItemHtml(n)}</li>`).join("")
          : '<li class="list-group-item border-0 px-0 text-sm text-secondary">No notifications — everything looks fine.</li>';
      }
    } catch (err) {
      // non-critical
    }
  }

  // ── cluster (master/slave) ───────────────────────────────────────────────

  let clusterTokenVisible = false;
  let lastClusterStatus = null;

  function renderClusterNodes(nodes) {
    const tbody = document.getElementById("cluster-nodes-table");
    if (!nodes.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-secondary">No nodes joined yet.</td></tr>';
      return;
    }
    const now = Date.now() / 1000;
    tbody.innerHTML = nodes
      .map((n) => {
        const stats = n.stats || {};
        const age = now - (n.last_seen || 0);
        const status = age < 90 ? '<span class="text-success">up</span>' : '<span class="text-warning">stale</span>';
        return `<tr>
          <td>${escapeHtml(n.node_id)}</td>
          <td class="small text-secondary">${escapeHtml(n.url || "–")}</td>
          <td class="text-end">${stats.total_calls != null ? fmtInt(stats.total_calls) : "–"}</td>
          <td class="text-end">${stats.tokens_saved != null ? fmtInt(stats.tokens_saved) : "–"}</td>
          <td class="small">${escapeHtml(stats.version || "–")}</td>
          <td>${status}</td>
        </tr>`;
      })
      .join("");
  }

  function renderClusterStatus(status) {
    lastClusterStatus = status;
    document.getElementById("cluster-role-badge").textContent = status.role;
    document.getElementById("cluster-node-id").textContent = status.node_id || "–";
    document.getElementById("cluster-master-url").textContent = status.role === "slave" ? (status.master_url || "–") : "–";

    const tokenGroup = document.getElementById("cluster-token-group");
    const becomeMasterBtn = document.getElementById("cluster-become-master-btn");
    const rotateBtn = document.getElementById("cluster-rotate-token-btn");
    const leaveBtn = document.getElementById("cluster-leave-btn");
    const joinRow = document.getElementById("cluster-join-row");
    const nodesRow = document.getElementById("cluster-nodes-row");

    if (status.role === "standalone") {
      tokenGroup.style.display = "none";
      becomeMasterBtn.style.display = "";
      rotateBtn.style.display = "none";
      leaveBtn.style.display = "none";
      joinRow.style.display = "";
      nodesRow.style.display = "none";
    } else if (status.role === "master") {
      tokenGroup.style.display = "";
      becomeMasterBtn.style.display = "none";
      rotateBtn.style.display = "";
      leaveBtn.style.display = "";
      joinRow.style.display = "none";
      nodesRow.style.display = "";
      renderClusterNodes(status.nodes || []);
    } else {
      // slave
      tokenGroup.style.display = "";
      becomeMasterBtn.style.display = "none";
      rotateBtn.style.display = "none";
      leaveBtn.style.display = "";
      joinRow.style.display = "none";
      nodesRow.style.display = "none";
    }

    updateClusterTokenDisplay();
  }

  function updateClusterTokenDisplay() {
    const el = document.getElementById("cluster-token-value");
    const toggle = document.getElementById("cluster-token-toggle");
    if (!lastClusterStatus || !lastClusterStatus.node_token) {
      el.textContent = "–";
      return;
    }
    el.textContent = clusterTokenVisible ? lastClusterStatus.node_token : "•".repeat(16);
    toggle.textContent = clusterTokenVisible ? "Hide" : "Show";
  }

  async function loadClusterStatus() {
    try {
      const status = await fetchJson("/api/cluster/status");
      renderClusterStatus(status);
    } catch (err) {
      // non-critical
    }
  }

  function clusterActionStatus(msg, cls) {
    const el = document.getElementById("cluster-action-status");
    el.textContent = msg;
    el.className = "text-sm mt-2 " + cls;
  }

  async function runClusterAction(payload) {
    try {
      const r = await postJson("/api/cluster/action", payload);
      clusterActionStatus("Done.", "text-success");
      renderClusterStatus(r.cluster);
      return r;
    } catch (err) {
      clusterActionStatus("Error: " + err.message, "text-danger");
      throw err;
    }
  }

  document.getElementById("cluster-become-master-btn").addEventListener("click", () => {
    runClusterAction({ action: "become_master" });
  });

  document.getElementById("cluster-rotate-token-btn").addEventListener("click", () => {
    if (confirm("Rotating the token disconnects every currently-joined node until they re-join with the new token. Continue?")) {
      runClusterAction({ action: "rotate_token" });
    }
  });

  document.getElementById("cluster-leave-btn").addEventListener("click", () => {
    if (confirm("Leave the cluster and return to standalone mode?")) {
      runClusterAction({ action: "leave" });
    }
  });

  document.getElementById("cluster-join-btn").addEventListener("click", () => {
    const masterUrl = document.getElementById("cluster-join-master-url").value.trim();
    const token = document.getElementById("cluster-join-token").value;
    if (!masterUrl || !token) {
      clusterActionStatus("Master URL and token are both required.", "text-danger");
      return;
    }
    runClusterAction({ action: "join", master_url: masterUrl, token });
  });

  document.getElementById("cluster-token-toggle").addEventListener("click", () => {
    clusterTokenVisible = !clusterTokenVisible;
    updateClusterTokenDisplay();
  });

  document.getElementById("cluster-token-copy").addEventListener("click", () => {
    if (lastClusterStatus && lastClusterStatus.node_token) {
      navigator.clipboard.writeText(lastClusterStatus.node_token).catch(() => {});
    }
  });

  // ── page routing (client-side; server serves the same shell for every
  // route in _PAGE_ROUTES — see dashboard.py) ─────────────────────────────

  const PAGE_TITLES = {
    overview: "Overview", charts: "Charts", sessions: "Sessions", requests: "Recent requests",
    decisions: "Decisions", settings: "Settings", profile: "Profile", notifications: "Notifications",
    cluster: "Cluster", doctor: "Doctor", version: "Version", privacy: "Privacy",
    security: "Security", proxy: "Proxy",
  };

  function pageForPath(path) {
    if (path === "/" || path === "/index.html" || path === "/overview") return "overview";
    const name = path.replace(/^\//, "");
    return PAGE_TITLES[name] ? name : "overview";
  }

  function showPage(name) {
    document.querySelectorAll("section[data-page]").forEach((section) => {
      section.style.display = section.dataset.page === name ? "" : "none";
    });
    // .nav-link.active alone only tweaks padding in Material Dashboard's CSS —
    // the visible highlighted pill comes from the bg-gradient-* utility class
    // itself, so it has to be toggled alongside .active, not left to CSS.
    document.querySelectorAll(".sidenav .nav-link[data-page-link]").forEach((link) => {
      const isActive = link.dataset.page === name;
      link.classList.toggle("active", isActive);
      link.classList.toggle("bg-gradient-info", isActive);
    });
    document.getElementById("breadcrumb-page").textContent = PAGE_TITLES[name] || "Overview";
    // IP rules/events can change from other processes (auto-ban, rate limit) —
    // refresh them every time the page is opened, not just once at load.
    if (name === "security") {
      loadWafIpRules();
      loadWafEvents();
      loadEnterpriseGuardEvents();
      loadEnterpriseGuardClients();
    }
    if (name === "proxy") {
      loadProxyStatus();
      loadProxyConfig();
      loadProxyLogs();
    }
  }

  function navigate(path, push) {
    const name = pageForPath(path);
    if (push) history.pushState({ page: name }, "", path);
    showPage(name);
  }

  document.addEventListener("click", (ev) => {
    const link = ev.target.closest("[data-page-link]");
    if (!link) return;
    ev.preventDefault();
    navigate(link.getAttribute("href"), true);
  });

  window.addEventListener("popstate", () => {
    showPage(pageForPath(window.location.pathname));
  });

  refresh();
  loadVersion();
  loadSettings();
  loadPrivacySettings();
  loadWafSettings();
  loadEnterpriseGuardSettings();
  loadStorageStatus();
  loadProfile();
  loadNotifications();
  loadClusterStatus();
  navigate(window.location.pathname, false);
  initTooltips();
  setInterval(refresh, 20000);
  setInterval(loadNotifications, 60000);
  setInterval(loadClusterStatus, 15000);
})();
