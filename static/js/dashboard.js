(() => {
  "use strict";

  const REFRESH = {
    summary: 15000,
    leaderboard: 30000,
    activity: 45000,
    peak: 60000,
    health: 45000,
    recent: 20000,
  };

  const state = {
    activityRange: "all",
    activityChart: null,
    timers: [],
  };

  const $ = (id) => document.getElementById(id);

  async function fetchJson(url) {
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!res.ok) {
      throw new Error(`${url} → ${res.status}`);
    }
    return res.json();
  }

  function formatNumber(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString();
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return "No data available";
    const s = Math.round(Number(seconds));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    if (m < 60) return rem ? `${m}m ${rem}s` : `${m}m`;
    const h = Math.floor(m / 60);
    const mins = m % 60;
    return mins ? `${h}h ${mins}m` : `${h}h`;
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function setUpdated() {
    const el = $("last-updated");
    if (el) {
      el.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }
  }

  async function loadSummary() {
    const data = await fetchJson("/api/dashboard/summary");
    $("kpi-total-players").textContent = formatNumber(data.total_players);
    $("kpi-total-rooms").textContent = formatNumber(data.total_rooms);
    $("kpi-games-started").textContent = formatNumber(
      data.games_started ?? data.total_games
    );
    $("kpi-games-finished").textContent = formatNumber(data.games_finished);
    $("kpi-total-rounds").textContent = formatNumber(data.total_rounds);
    $("kpi-total-points").textContent = formatNumber(data.total_points);
    $("kpi-total-joins").textContent = formatNumber(data.total_joins);
    $("kpi-active-games").textContent = formatNumber(data.active_games);
  }

  async function loadLeaderboard() {
    const data = await fetchJson("/api/dashboard/leaderboard?limit=100");
    const body = $("leaderboard-body");
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="6" class="empty">No data available</td></tr>`;
      return;
    }
    body.innerHTML = items
      .map(
        (row) => `
      <tr>
        <td>${row.rank}</td>
        <td>${escapeHtml(row.username)}</td>
        <td>${formatNumber(row.games_played)}</td>
        <td>${formatNumber(row.wins)}</td>
        <td>${formatNumber(row.total_score)}</td>
        <td>${Number(row.win_rate).toFixed(1)}%</td>
      </tr>`
      )
      .join("");
  }

  async function loadActivity() {
    const data = await fetchJson(
      `/api/dashboard/activity?range=${encodeURIComponent(state.activityRange)}`
    );
    const labels = data.labels || [];
    const created = data.rooms_created || [];
    const started = data.games_started || [];
    const rounds = data.rounds_played || data.games_played || [];
    const empty = $("activity-empty");
    const hasData =
      created.some((n) => n > 0) ||
      started.some((n) => n > 0) ||
      rounds.some((n) => n > 0);

    if (empty) empty.classList.toggle("hidden", hasData);
    const sub = $("activity-sub");
    if (sub) {
      const bucket = data.bucket === "month" ? "month" : data.bucket === "week" ? "week" : "day";
      const span = data.range === "all" ? "all stored history" : data.range === "30d" ? "the last 30 days" : "the last 7 days";
      sub.textContent = `Rooms, games started, and rounds by ${bucket} across ${span} (UTC)`;
    }

    const ctx = $("activity-chart");
    if (!ctx || typeof Chart === "undefined") return;

    if (state.activityChart) {
      state.activityChart.data.labels = labels;
      state.activityChart.data.datasets[0].data = created;
      state.activityChart.data.datasets[1].data = started;
      state.activityChart.data.datasets[2].data = rounds;
      state.activityChart.update("none");
      return;
    }

    state.activityChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Rooms created",
            data: created,
            borderColor: "#818cf8",
            backgroundColor: "rgba(99, 102, 241, 0.18)",
            tension: 0.35,
            fill: true,
            pointRadius: 2,
          },
          {
            label: "Games started",
            data: started,
            borderColor: "#34d399",
            backgroundColor: "rgba(52, 211, 153, 0.12)",
            tension: 0.35,
            fill: true,
            pointRadius: 2,
          },
          {
            label: "Rounds played",
            data: rounds,
            borderColor: "#fbbf24",
            backgroundColor: "rgba(251, 191, 36, 0.12)",
            tension: 0.35,
            fill: true,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: "#93a3bd", boxWidth: 12 },
          },
        },
        scales: {
          x: {
            ticks: { color: "#93a3bd", maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
            grid: { color: "rgba(148, 163, 184, 0.08)" },
          },
          y: {
            beginAtZero: true,
            ticks: {
              color: "#93a3bd",
              precision: 0,
            },
            grid: { color: "rgba(148, 163, 184, 0.08)" },
          },
        },
      },
    });
  }

  async function loadHealth() {
    const data = await fetchJson("/api/dashboard/game-health");
    $("health-duration").textContent = formatDuration(
      data.average_game_duration_seconds
    );
    $("health-players").textContent =
      data.average_players_per_game === null ||
      data.average_players_per_game === undefined
        ? "No data available"
        : formatNumber(data.average_players_per_game);
    $("health-rounds").textContent = formatNumber(data.total_rounds);
    $("health-points").textContent = formatNumber(data.total_points);
    $("health-abandoned").textContent = formatNumber(
      data.abandoned_before_start
    );
    $("health-abandoned-rate").textContent =
      data.abandoned_rate === null || data.abandoned_rate === undefined
        ? "No data available"
        : `${Number(data.abandoned_rate).toFixed(1)}%`;
    const note = $("health-note");
    if (note) note.textContent = data.notes || "";
  }

  function heatColor(count, max) {
    if (!count || !max) return "rgba(148, 163, 184, 0.08)";
    const t = Math.min(1, count / max);
    const alpha = 0.15 + t * 0.85;
    return `rgba(99, 102, 241, ${alpha.toFixed(3)})`;
  }

  async function loadPeakActivity() {
    const data = await fetchJson("/api/dashboard/peak-activity");
    const root = $("heatmap");
    const empty = $("heatmap-empty");
    const days = data.days || [];
    const hours = data.hours || [];
    const matrix = data.matrix || [];
    const max = data.max || 0;

    if (!root) return;

    if (!max) {
      root.innerHTML = "";
      if (empty) empty.classList.remove("hidden");
      return;
    }
    if (empty) empty.classList.add("hidden");

    const parts = ['<div class="corner"></div>'];
    for (const h of hours) {
      parts.push(
        `<div class="hour-label">${String(h).padStart(2, "0")}</div>`
      );
    }

    days.forEach((day, di) => {
      parts.push(`<div class="day-label">${escapeHtml(day.slice(0, 3))}</div>`);
      const row = matrix[di] || [];
      for (let h = 0; h < 24; h += 1) {
        const count = row[h] || 0;
        parts.push(
          `<div class="cell" style="background:${heatColor(count, max)}" data-count="${count}" title="${escapeHtml(day)} ${String(h).padStart(2, "0")}:00 — ${count} join(s)"></div>`
        );
      }
    });

    root.innerHTML = parts.join("");
  }

  async function loadRecent() {
    const data = await fetchJson("/api/dashboard/recent-games?limit=100");
    const body = $("recent-body");
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="10" class="empty">No data available</td></tr>`;
      return;
    }
    body.innerHTML = items
      .map((row) => {
        const status = row.status || "—";
        return `
      <tr>
        <td><strong>${escapeHtml(row.room_code || "—")}</strong></td>
        <td>${escapeHtml(row.host || "—")}</td>
        <td>${formatNumber(row.players)}</td>
        <td>${formatNumber(row.rounds)}</td>
        <td>${formatNumber(row.points)}</td>
        <td><span class="status-pill status-${escapeHtml(status)}">${escapeHtml(status)}</span></td>
        <td>${formatDateTime(row.created_at)}</td>
        <td>${formatDateTime(row.started_at)}</td>
        <td>${formatDateTime(row.ended_at)}</td>
        <td>${row.duration_seconds == null ? "—" : formatDuration(row.duration_seconds)}</td>
      </tr>`;
      })
      .join("");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function refreshAll() {
    const tasks = [
      loadSummary(),
      loadLeaderboard(),
      loadActivity(),
      loadHealth(),
      loadPeakActivity(),
      loadRecent(),
    ];
    const results = await Promise.allSettled(tasks);
    results.forEach((r, i) => {
      if (r.status === "rejected") {
        console.error("Dashboard load failed", i, r.reason);
      }
    });
    setUpdated();
  }

  function bindRangeButtons() {
    document.querySelectorAll(".seg[data-range]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        document.querySelectorAll(".seg[data-range]").forEach((b) => {
          b.classList.toggle("active", b === btn);
        });
        state.activityRange = btn.getAttribute("data-range") || "7d";
        try {
          await loadActivity();
          setUpdated();
        } catch (err) {
          console.error(err);
        }
      });
    });
  }

  function startPolling() {
    state.timers.forEach(clearInterval);
    state.timers = [
      setInterval(() => loadSummary().then(setUpdated).catch(console.error), REFRESH.summary),
      setInterval(() => loadLeaderboard().catch(console.error), REFRESH.leaderboard),
      setInterval(() => loadActivity().catch(console.error), REFRESH.activity),
      setInterval(() => loadPeakActivity().catch(console.error), REFRESH.peak),
      setInterval(() => loadHealth().catch(console.error), REFRESH.health),
      setInterval(() => loadRecent().catch(console.error), REFRESH.recent),
    ];
  }

  function init() {
    bindRangeButtons();
    const refreshBtn = $("btn-refresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshAll();
      });
    }
    refreshAll();
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
