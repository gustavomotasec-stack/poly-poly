/* ─────────────────────────────────────────────
   Polymarket Bot Dashboard — app.js
   Real-time updates via SSE + REST polling
───────────────────────────────────────────── */

const API = '';          // same origin
let paused = false;
let equityChart = null;
let equityData = { labels: [], values: [] };
const MAX_EQUITY_POINTS = 120;

// ── Init ──────────────────────────────────────

window.addEventListener('DOMContentLoaded', () => {
  initChart();
  connectSSE();
  fetchAll();
  setInterval(fetchAll, 15000);
});

function fetchAll() {
  fetchMetrics();
  fetchTrades();
  fetchSignals();
  fetchPositions();
  fetchEquity();
}

// ── SSE connection ────────────────────────────

function connectSSE() {
  const es = new EventSource(`${API}/events`);

  es.onopen = () => setStatus(true);
  es.onerror = () => {
    setStatus(false);
    setTimeout(connectSSE, 5000);
    es.close();
  };

  es.onmessage = (e) => {
    try {
      const { type, data } = JSON.parse(e.data);
      if (type === 'metrics_update')  applyMetrics(data);
      if (type === 'signals_update')  renderSignals(data.signals || []);
      if (type === 'trade_opened')    { fetchTrades(); fetchPositions(); toast('Trade opened: ' + (data.strategy || ''), 'green'); }
      if (type === 'daily_limit')     toast('⚠ Daily loss limit hit! Bot stopped.', 'red');
      if (type === 'heartbeat')       setStatus(true);
    } catch (_) {}
  };
}

// ── Status indicator ──────────────────────────

function setStatus(online) {
  const dot = document.getElementById('statusDot');
  const lbl = document.getElementById('statusLabel');
  dot.className = 'status-dot' + (online ? '' : ' offline');
  lbl.textContent = online ? 'Online' : 'Offline';
}

// ── Metrics ───────────────────────────────────

async function fetchMetrics() {
  try {
    const data = await api('/api/metrics');
    applyMetrics(data);
  } catch (_) {}
}

function applyMetrics(d) {
  setVal('mBankroll', fmt$(d.bankroll));
  setValColor('mPnl', fmt$(d.total_pnl, true), d.total_pnl);
  setText('mPnlPct', `${d.total_pnl_pct !== undefined ? d.total_pnl_pct : '—'}%`);
  setText('mWinRate', `${d.win_rate ?? '—'}%`);
  setText('mTrades', `${d.total_trades ?? 0} total trades`);
  setValColor('mDrawdown', `${d.max_drawdown_pct ?? '—'}%`, -(d.max_drawdown_pct ?? 0));
  setText('mSharpe', `Sharpe: ${d.sharpe_ratio ?? '—'}`);
  setText('mToday', d.today_trades ?? '—');
  setText('mAvgPnl', fmt$(d.avg_trade_pnl, true));
  setText('mPositions', d.open_trades ?? d.active_positions ?? '—');

  const risk = d.risk || {};
  const riskParts = [];
  if (risk.in_cooldown) riskParts.push('⏳ Cooldown');
  if (risk.consecutive_losses > 0) riskParts.push(`${risk.consecutive_losses} losses`);
  setText('mRiskStatus', riskParts.join(' · ') || 'Normal');

  // Mode badge
  const badge = document.getElementById('modeBadge');
  if (d.mode === 'live') {
    badge.textContent = '⚠ LIVE TRADING';
    badge.className = 'badge badge-live';
  }

  // Pause button state
  paused = d.paused;
  syncPauseBtn();
}

// ── Equity chart ──────────────────────────────

function initChart() {
  const ctx = document.getElementById('equityChart').getContext('2d');
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: equityData.labels,
      datasets: [{
        label: 'Bankroll (USDC)',
        data: equityData.values,
        borderColor: '#3fb950',
        backgroundColor: 'rgba(63,185,80,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { display: true, ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 } }, grid: { color: '#21262d' } },
        y: { display: true, ticks: { color: '#8b949e', font: { size: 10 }, callback: v => '$' + v.toFixed(2) }, grid: { color: '#21262d' } },
      },
      plugins: { legend: { display: false } },
    }
  });
}

async function fetchEquity() {
  try {
    const history = await api('/api/equity');
    if (!history.length) return;
    equityData.labels = history.map(h => fmtTime(h.timestamp));
    equityData.values = history.map(h => h.bankroll);

    // Trim to max points
    if (equityData.labels.length > MAX_EQUITY_POINTS) {
      const step = Math.ceil(equityData.labels.length / MAX_EQUITY_POINTS);
      equityData.labels = equityData.labels.filter((_, i) => i % step === 0);
      equityData.values = equityData.values.filter((_, i) => i % step === 0);
    }

    equityChart.data.labels = equityData.labels;
    equityChart.data.datasets[0].data = equityData.values;

    // Dynamic color: green if up, red if down
    const first = equityData.values[0] || 100;
    const last  = equityData.values[equityData.values.length - 1] || 100;
    const color = last >= first ? '#3fb950' : '#f85149';
    equityChart.data.datasets[0].borderColor = color;
    equityChart.data.datasets[0].backgroundColor = color + '14';
    equityChart.update('none');
  } catch (_) {}
}

// Push a live point
function pushEquityPoint(bankroll) {
  const now = fmtTime(new Date().toISOString());
  equityData.labels.push(now);
  equityData.values.push(bankroll);
  if (equityData.labels.length > MAX_EQUITY_POINTS) {
    equityData.labels.shift();
    equityData.values.shift();
  }
  if (equityChart) {
    equityChart.data.labels = equityData.labels;
    equityChart.data.datasets[0].data = equityData.values;
    equityChart.update('none');
  }
}

// ── Trades table ──────────────────────────────

async function fetchTrades() {
  try {
    const trades = await api('/api/trades?limit=50');
    renderTrades(trades);
  } catch (_) {}
}

function renderTrades(trades) {
  const tbody = document.getElementById('tradesBody');
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:24px;">No trades yet</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map(t => {
    const pnl = t.pnl !== null && t.pnl !== undefined ? t.pnl : null;
    const pnlStr = pnl !== null ? `<span class="${pnl >= 0 ? 'pos' : 'neg'}">${fmt$(pnl, true)}</span>` : '<span class="neu">—</span>';
    const dirPill = directionPill(t.direction);
    const stratPill = strategyPill(t.strategy);
    const statusColor = t.status === 'open' ? 'var(--blue)' : t.status === 'closed' ? 'var(--muted)' : 'var(--yellow)';
    return `<tr>
      <td style="color:var(--muted)">${fmtTime(t.timestamp)}</td>
      <td title="${t.question}" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.question}</td>
      <td>${dirPill}</td>
      <td>$${(t.size || 0).toFixed(3)}</td>
      <td>${(t.entry_price || 0).toFixed(3)}</td>
      <td>${t.exit_price !== null ? (t.exit_price || 0).toFixed(3) : '—'}</td>
      <td>${pnlStr}</td>
      <td>${stratPill}</td>
      <td><span style="color:${statusColor}">${t.status || '—'}</span></td>
    </tr>`;
  }).join('');
}

function directionPill(d) {
  if (!d) return '';
  const s = d.toUpperCase();
  if (s === 'YES' || s === 'UP')  return `<span class="pill pill-yes">YES</span>`;
  if (s === 'NO'  || s === 'DOWN') return `<span class="pill pill-no">NO</span>`;
  if (s === 'BOTH') return `<span class="pill pill-both">BOTH</span>`;
  return `<span class="pill">${s}</span>`;
}

function strategyPill(s) {
  if (!s) return '';
  const map = { ARBITRAGE: 'arb', MOMENTUM: 'mom', MEAN_REVERSION: 'rev' };
  const cls = map[s] || '';
  return `<span class="pill pill-${cls}">${s.replace('_', ' ')}</span>`;
}

// ── Signals sidebar ───────────────────────────

async function fetchSignals() {
  try {
    const signals = await api('/api/signals');
    renderSignals(signals);
  } catch (_) {}
}

function renderSignals(signals) {
  const el = document.getElementById('signalsList');
  if (!signals || !signals.length) {
    el.innerHTML = '<p style="color:var(--muted);font-size:12px;">Waiting for signals…</p>';
    return;
  }

  el.innerHTML = signals.slice(0, 8).map(s => {
    const dirClass = s.direction === 'UP' ? 'dir-up' : s.direction === 'DOWN' ? 'dir-down' : 'dir-neu';
    const conf = Math.round((s.confidence || 0) * 100);
    const rsi = s.rsi !== undefined && s.rsi !== null ? s.rsi : (s.indicators?.rsi);
    const mom = s.momentum !== undefined ? s.momentum : (s.indicators?.momentum_pct);
    const rsiNorm = rsi !== undefined ? Math.min(100, Math.max(0, rsi)) : null;
    const confColor = conf >= 60 ? '#3fb950' : conf >= 40 ? '#d29922' : '#8b949e';

    return `<div class="signal-item">
      <div class="signal-header">
        <span class="signal-asset">${s.asset || '?'}</span>
        <span class="signal-dir ${dirClass}">${s.direction || 'NEUTRAL'}</span>
      </div>
      <div class="signal-bars">
        ${rsiNorm !== null ? `
        <div class="signal-bar-row">
          <span style="width:55px">RSI ${rsi !== undefined ? rsi.toFixed(1) : '—'}</span>
          <div class="signal-bar-track">
            <div class="signal-bar-fill" style="width:${rsiNorm}%;background:${rsiNorm > 70 ? '#f85149' : rsiNorm < 30 ? '#3fb950' : '#58a6ff'}"></div>
          </div>
        </div>` : ''}
        <div class="signal-bar-row">
          <span style="width:55px">Conf ${conf}%</span>
          <div class="signal-bar-track">
            <div class="signal-bar-fill" style="width:${conf}%;background:${confColor}"></div>
          </div>
        </div>
        ${mom !== undefined && mom !== null ? `<div class="signal-bar-row" style="color:${mom >= 0 ? 'var(--green)' : 'var(--red)'}">Mom: ${mom >= 0 ? '+' : ''}${typeof mom === 'number' ? mom.toFixed(3) : mom}%</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── Positions ─────────────────────────────────

async function fetchPositions() {
  try {
    const positions = await api('/api/positions');
    renderPositions(positions);
  } catch (_) {}
}

function renderPositions(positions) {
  const el = document.getElementById('positionsList');
  if (!positions || !positions.length) {
    el.innerHTML = '<p style="color:var(--muted);font-size:12px;">No open positions</p>';
    return;
  }
  el.innerHTML = positions.map(p => `
    <div class="signal-item">
      <div class="signal-header">
        <span style="font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis">${p.strategy || '?'}</span>
        <span style="color:var(--blue);font-size:11px">$${(p.size || 0).toFixed(3)}</span>
      </div>
      <div style="font-size:10px;color:var(--muted)">Entry: ${(p.entry_price || 0).toFixed(4)}</div>
    </div>
  `).join('');
}

// ── Controls ──────────────────────────────────

function syncPauseBtn() {
  const btn = document.getElementById('pauseBtn');
  if (paused) {
    btn.textContent = '▶ Resume Bot';
    btn.className = 'btn-resume';
  } else {
    btn.textContent = '⏸ Pause Bot';
    btn.className = 'btn-pause';
  }
}

async function togglePause() {
  const endpoint = paused ? '/api/resume' : '/api/pause';
  try {
    await fetch(API + endpoint, { method: 'POST' });
    paused = !paused;
    syncPauseBtn();
    toast(paused ? 'Bot paused' : 'Bot resumed', paused ? 'yellow' : 'green');
  } catch (e) {
    toast('Error: could not contact bot', 'red');
  }
}

function toggleSim(checkbox) {
  if (!checkbox.checked) {
    const confirmed = confirm(
      '⚠ WARNING: You are about to disable Simulation Mode.\n\n' +
      'This will attempt REAL trades with REAL money.\n' +
      'Make sure you have configured valid Polymarket credentials.\n\n' +
      'Are you absolutely sure?'
    );
    if (!confirmed) {
      checkbox.checked = true;
    } else {
      toast('Live mode enabled — restart bot with --live flag', 'red');
    }
  }
}

function updateMaxSize(val) {
  const v = parseFloat(val);
  if (isNaN(v) || v <= 0) return;
  toast(`Max size updated to $${v.toFixed(2)} (requires restart)`, 'blue');
}

// ── Helpers ───────────────────────────────────

async function api(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(res.status);
  return res.json();
}

function fmt$(val, signed = false) {
  if (val === undefined || val === null || isNaN(val)) return '—';
  const prefix = signed && val > 0 ? '+' : '';
  return prefix + '$' + parseFloat(val).toFixed(2);
}

function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch (_) { return iso; }
}

function setVal(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setText(id, text) { setVal(id, text); }

function setValColor(id, text, numVal) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'card-value ' + (numVal > 0 ? 'pos' : numVal < 0 ? 'neg' : 'neu');
}

function toast(msg, color = 'blue') {
  const colorMap = { green: '#3fb950', red: '#f85149', yellow: '#d29922', blue: '#58a6ff' };
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = colorMap[color] || colorMap.blue;
  el.style.color = colorMap[color] || colorMap.blue;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3500);
}
