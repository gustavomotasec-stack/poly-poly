/* ─────────────────────────────────────────────
   Polymarket Bot Dashboard — app.js  v2
   Strategies: ARB · CORR_ARB · MARKET_MAKING · MOMENTUM · COPY · MEAN_REV
   Real-time via SSE + REST polling
───────────────────────────────────────────── */

const API = '';
let paused = false;
let equityChart = null;
let equityData = { labels: [], values: [] };
const MAX_EQUITY_POINTS = 120;

window.addEventListener('DOMContentLoaded', () => {
  initChart();
  loadConfig();
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
  fetchSentiment();
  fetchCopySignals();
}

// ── SSE ───────────────────────────────────────

function connectSSE() {
  const es = new EventSource(`${API}/events`);
  es.onopen = () => setStatus(true);
  es.onerror = () => { setStatus(false); setTimeout(connectSSE, 5000); es.close(); };
  es.onmessage = (e) => {
    try {
      const { type, data } = JSON.parse(e.data);
      if (type === 'metrics_update')   applyMetrics(data);
      if (type === 'signals_update')   renderSignals(data.signals || []);
      if (type === 'sentiment_update') renderSentiment(data);
      if (type === 'copy_signals')     renderCopySignals(data.signals || []);
      if (type === 'trade_opened')     { fetchTrades(); fetchPositions(); toast(`Trade: ${data.strategy}`, 'green'); }
      if (type === 'daily_limit')      toast('⚠ Limite de perda diária atingido!', 'red');
      if (type === 'heartbeat')        setStatus(true);
    } catch (_) {}
  };
}

function setStatus(online) {
  document.getElementById('statusDot').className = 'status-dot' + (online ? '' : ' offline');
  document.getElementById('statusLabel').textContent = online ? 'Online' : 'Offline';
}

// ── Config (tamanho máx. por trade) ───────────

async function loadConfig() {
  const saved = localStorage.getItem('maxSize');
  let val = null;

  if (saved != null) {
    // Valor salvo pelo usuário é a fonte da verdade — reaplica no backend
    // (o bot reinicia com o padrão da config, então reenviamos aqui)
    val = parseFloat(saved);
    try {
      await fetch(API + '/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_position_size: val }),
      });
    } catch (_) {}
  } else {
    try {
      const cfg = await api('/api/config');
      if (cfg && cfg.max_position_size != null) val = parseFloat(cfg.max_position_size);
    } catch (_) {}
  }

  if (val != null) {
    const input = document.getElementById('maxSize');
    if (input) input.value = val;
  }
}

// ── Metrics ───────────────────────────────────

async function fetchMetrics() {
  try { applyMetrics(await api('/api/metrics')); } catch(_) {}
}

function applyMetrics(d) {
  setVal('mBankroll', fmt$(d.bankroll));
  setValColor('mPnl', fmt$(d.total_pnl, true), d.total_pnl);
  setText('mPnlPct', `${d.total_pnl_pct ?? '—'}%`);
  setText('mWinRate', `${d.win_rate ?? '—'}%`);
  setText('mTrades', `${d.total_trades ?? 0} trades no total`);
  setValColor('mDrawdown', `${d.max_drawdown_pct ?? '—'}%`, -(d.max_drawdown_pct ?? 0));
  setText('mSharpe', `Sharpe: ${d.sharpe_ratio ?? '—'}`);
  setText('mToday', d.today_trades ?? '—');
  setText('mAvgPnl', fmt$(d.avg_trade_pnl, true));
  setText('mPositions', d.open_trades ?? d.active_positions ?? '—');
  const risk = d.risk || {};
  const parts = [];
  if (risk.in_cooldown) parts.push('⏳ Cooldown');
  if (risk.consecutive_losses > 0) parts.push(`${risk.consecutive_losses} losses`);
  setText('mRiskStatus', parts.join(' · ') || 'Normal');
  const badge = document.getElementById('modeBadge');
  if (d.mode === 'live') { badge.textContent = '⚠ MODO REAL'; badge.className = 'badge badge-live'; }
  paused = d.paused;
  syncPauseBtn();
}

// ── Equity Chart ──────────────────────────────

function initChart() {
  const ctx = document.getElementById('equityChart').getContext('2d');
  equityChart = new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Bankroll', data: [], borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }] },
    options: {
      responsive: true, animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { display: true, ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 } }, grid: { color: '#21262d' } },
        y: { display: true, ticks: { color: '#8b949e', font: { size: 10 }, callback: v => '$' + v.toFixed(2) }, grid: { color: '#21262d' } }
      },
      plugins: { legend: { display: false } }
    }
  });
}

async function fetchEquity() {
  try {
    const history = await api('/api/equity');
    if (!history.length) return;
    let labels = history.map(h => fmtTime(h.timestamp));
    let values = history.map(h => h.bankroll);
    if (labels.length > MAX_EQUITY_POINTS) {
      const step = Math.ceil(labels.length / MAX_EQUITY_POINTS);
      labels = labels.filter((_, i) => i % step === 0);
      values = values.filter((_, i) => i % step === 0);
    }
    const color = values[values.length-1] >= values[0] ? '#3fb950' : '#f85149';
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = values;
    equityChart.data.datasets[0].borderColor = color;
    equityChart.data.datasets[0].backgroundColor = color + '14';
    equityChart.update('none');
  } catch(_) {}
}

// ── Trades ────────────────────────────────────

async function fetchTrades() {
  try { renderTrades(await api('/api/trades?limit=50')); } catch(_) {}
}

function renderTrades(trades) {
  const tbody = document.getElementById('tradesBody');
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:24px;">Nenhum trade ainda</td></tr>';
    return;
  }
  tbody.innerHTML = trades.map(t => {
    const pnl = t.pnl !== null && t.pnl !== undefined ? t.pnl : null;
    const pnlStr = pnl !== null ? `<span class="${pnl >= 0 ? 'pos' : 'neg'}">${fmt$(pnl, true)}</span>` : '<span class="neu">—</span>';
    const statusColor = t.status === 'open' ? 'var(--blue)' : 'var(--muted)';
    return `<tr>
      <td style="color:var(--muted)">${fmtTime(t.timestamp)}</td>
      <td title="${t.question}" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.question}</td>
      <td>${dirPill(t.direction)}</td>
      <td>$${(t.size||0).toFixed(3)}</td>
      <td>${(t.entry_price||0).toFixed(3)}</td>
      <td>${t.exit_price !== null ? (t.exit_price||0).toFixed(3) : '—'}</td>
      <td>${pnlStr}</td>
      <td>${stratPill(t.strategy)}</td>
      <td><span style="color:${statusColor}">${t.status||'—'}</span></td>
    </tr>`;
  }).join('');
}

function dirPill(d) {
  if (!d) return '';
  const s = d.toUpperCase();
  if (s === 'YES' || s === 'UP')   return `<span class="pill pill-yes">YES</span>`;
  if (s === 'NO'  || s === 'DOWN') return `<span class="pill pill-no">NO</span>`;
  if (s === 'BOTH')                return `<span class="pill pill-both">BOTH</span>`;
  return `<span class="pill">${s}</span>`;
}

const STRAT_CLS = { ARBITRAGE:'arb', CORRELATION_ARB:'corr', MARKET_MAKING:'mm', MOMENTUM:'mom', COPY_TRADE:'copy', MEAN_REVERSION:'rev' };
function stratPill(s) {
  if (!s) return '';
  const cls = STRAT_CLS[s] || '';
  return `<span class="pill pill-${cls}">${s.replace(/_/g,' ')}</span>`;
}

// ── Signals ───────────────────────────────────

async function fetchSignals() {
  try { renderSignals(await api('/api/signals')); } catch(_) {}
}

function renderSignals(signals) {
  const el = document.getElementById('signalsList');
  if (!signals || !signals.length) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">Aguardando…</p>'; return; }
  el.innerHTML = signals.slice(0, 8).map(s => {
    const dirCls = s.direction === 'UP' ? 'dir-up' : s.direction === 'DOWN' ? 'dir-down' : 'dir-neu';
    const conf = Math.round((s.confidence || 0) * 100);
    const rsi = s.rsi ?? s.indicators?.rsi;
    const mom = s.momentum ?? s.indicators?.momentum_pct;
    const rsiNorm = rsi != null ? Math.min(100, Math.max(0, rsi)) : null;
    const confColor = conf >= 60 ? '#3fb950' : conf >= 40 ? '#d29922' : '#8b949e';
    return `<div class="signal-item">
      <div class="signal-header">
        <span class="signal-asset">${s.asset || '?'}</span>
        <span class="signal-dir ${dirCls}">${s.direction || 'NEUTRAL'}</span>
      </div>
      <div class="signal-bars">
        ${rsiNorm != null ? `<div class="signal-bar-row"><span style="width:55px">RSI ${rsi.toFixed(1)}</span><div class="signal-bar-track"><div class="signal-bar-fill" style="width:${rsiNorm}%;background:${rsiNorm>70?'#f85149':rsiNorm<30?'#3fb950':'#58a6ff'}"></div></div></div>` : ''}
        <div class="signal-bar-row"><span style="width:55px">Conf ${conf}%</span><div class="signal-bar-track"><div class="signal-bar-fill" style="width:${conf}%;background:${confColor}"></div></div></div>
        ${mom != null ? `<div class="signal-bar-row" style="color:${mom>=0?'var(--green)':'var(--red)'}">Mom: ${mom>=0?'+':''}${typeof mom==='number'?mom.toFixed(3):mom}%</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── Sentiment ─────────────────────────────────

async function fetchSentiment() {
  try { renderSentiment(await api('/api/news-sentiment')); } catch(_) {}
}

function renderSentiment(data) {
  const el = document.getElementById('sentimentPanel');
  if (!el) return;
  if (!data || (!data.BTC && !data.ETH)) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">Buscando notícias…</p>'; return; }
  el.innerHTML = ['BTC','ETH'].map(asset => {
    const s = data[asset];
    if (!s) return '';
    const color = s.direction === 'BULLISH' ? 'var(--green)' : s.direction === 'BEARISH' ? 'var(--red)' : 'var(--muted)';
    const dirPt = s.direction === 'BULLISH' ? 'OTIMISTA' : s.direction === 'BEARISH' ? 'PESSIMISTA' : 'NEUTRO';
    const bar = Math.round((s.score + 1) / 2 * 100);
    return `<div class="signal-item">
      <div class="signal-header">
        <span class="signal-asset">${asset}</span>
        <span style="font-size:11px;font-weight:700;color:${color}">${dirPt}</span>
      </div>
      <div class="signal-bar-row">
        <span style="width:55px">Score ${s.score > 0 ? '+' : ''}${s.score.toFixed(2)}</span>
        <div class="signal-bar-track"><div class="signal-bar-fill" style="width:${bar}%;background:${color}"></div></div>
      </div>
      <div style="font-size:10px;color:var(--muted);margin-top:3px">há ${s.age_seconds}s</div>
    </div>`;
  }).join('');
}

// ── Copy Signals ──────────────────────────────

async function fetchCopySignals() {
  try {
    const data = await api('/api/copy-signals');
    renderCopySignals(data.signals || []);
  } catch(_) {}
}

function renderCopySignals(signals) {
  const el = document.getElementById('copyPanel');
  if (!el) return;
  if (!signals.length) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">Sem sinais de cópia (defina COPY_TRADE_WALLETS no .env)</p>'; return; }
  el.innerHTML = signals.slice(0, 5).map(s => `
    <div class="signal-item">
      <div class="signal-header">
        <span style="font-size:11px;color:var(--blue)">${s.wallet}</span>
        <span style="font-size:10px;color:var(--muted)">WR: ${s.wallet_win_rate}%</span>
      </div>
      <div style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.question}</div>
      <div style="font-size:10px;color:var(--muted)">→ ${s.direction} @ $${(s.entry_price||0).toFixed(3)}</div>
    </div>`).join('');
}

// ── Positions ─────────────────────────────────

async function fetchPositions() {
  try { renderPositions(await api('/api/positions')); } catch(_) {}
}

function renderPositions(positions) {
  const el = document.getElementById('positionsList');
  if (!positions || !positions.length) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">Sem posições abertas</p>'; return; }
  el.innerHTML = positions.map(p => `
    <div class="signal-item">
      <div class="signal-header">
        <span style="font-size:11px;flex:1">${p.strategy||'?'}</span>
        <span style="color:var(--blue);font-size:11px">$${(p.size||0).toFixed(3)}</span>
      </div>
      <div style="font-size:10px;color:var(--muted)">Entrada: ${(p.entry_price||0).toFixed(4)}</div>
    </div>`).join('');
}

// ── Backtest ──────────────────────────────────

async function runBacktest() {
  const btn = document.getElementById('backtestBtn');
  btn.textContent = '⏳ Rodando…';
  btn.disabled = true;
  try {
    const report = await fetch(API + '/api/backtest?limit=100', { method: 'POST' }).then(r => r.json());
    if (report.error) { toast('Erro no backtest: ' + report.error, 'red'); return; }
    const el = document.getElementById('backtestResult');
    el.style.display = 'block';
    const sourceNote = report.data_source === 'synthetic'
      ? '<div style="font-size:10px;color:var(--yellow);margin-bottom:6px">⚠ Cenário sintético (sem dados históricos reais disponíveis)</div>'
      : '<div style="font-size:10px;color:var(--green);margin-bottom:6px">✓ Dados históricos reais</div>';
    el.innerHTML = `
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Backtest — ${report.total_trades} trades simulados</div>
      ${sourceNote}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:12px">
        <span>PnL</span><span class="${report.total_pnl>=0?'pos':'neg'}">${fmt$(report.total_pnl, true)} (${report.total_pnl_pct>0?'+':''}${report.total_pnl_pct}%)</span>
        <span>Taxa de Acerto</span><span>${report.win_rate}%</span>
        <span>Drawdown Máx.</span><span class="neg">${report.max_drawdown_pct}%</span>
        ${Object.entries(report.per_strategy||{}).map(([k,v])=>`<span>${k.replace(/_/g,' ')}</span><span class="${v.total_pnl>=0?'pos':'neg'}">${v.win_rate}% acerto · ${fmt$(v.total_pnl,true)}</span>`).join('')}
      </div>`;
    toast('Backtest concluído!', 'green');
  } catch(e) { toast('Não foi possível alcançar a API do bot', 'red'); }
  finally { btn.textContent = '🔁 Rodar Backtest'; btn.disabled = false; }
}

// ── Controles ─────────────────────────────────

function syncPauseBtn() {
  const btn = document.getElementById('pauseBtn');
  if (paused) { btn.textContent = '▶ Retomar Bot'; btn.className = 'btn-resume'; }
  else        { btn.textContent = '⏸ Pausar Bot';  btn.className = 'btn-pause'; }
}

async function togglePause() {
  const ep = paused ? '/api/resume' : '/api/pause';
  try { await fetch(API + ep, { method: 'POST' }); paused = !paused; syncPauseBtn(); toast(paused ? 'Bot pausado' : 'Bot retomado', paused ? 'yellow' : 'green'); }
  catch(e) { toast('Erro ao contatar o bot', 'red'); }
}

function toggleSim(checkbox) {
  if (!checkbox.checked) {
    if (!confirm('⚠ Desativar o Modo Simulação?\n\nIsso fará trades com DINHEIRO REAL.\nReinicie o bot com a flag --live.\n\nDeseja continuar?')) {
      checkbox.checked = true;
    } else { toast('Reinicie com --live para usar fundos reais', 'red'); }
  }
}

async function updateMaxSize(val) {
  const v = parseFloat(val);
  if (isNaN(v) || v <= 0) { toast('Valor inválido', 'red'); return; }
  try {
    const res = await fetch(API + '/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_position_size: v }),
    });
    const data = await res.json();
    const applied = parseFloat(data.max_position_size);
    localStorage.setItem('maxSize', applied);
    const input = document.getElementById('maxSize');
    if (input) input.value = applied;
    toast(`Tamanho máx. atualizado para $${applied.toFixed(2)}`, 'green');
  } catch (e) {
    // Backend offline — persiste localmente mesmo assim
    localStorage.setItem('maxSize', v);
    toast(`Tamanho máx. salvo: $${v.toFixed(2)}`, 'blue');
  }
}

// ── Helpers ───────────────────────────────────

async function api(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(res.status);
  return res.json();
}

function fmt$(val, signed = false) {
  if (val === undefined || val === null || isNaN(val)) return '—';
  return (signed && val > 0 ? '+' : '') + '$' + parseFloat(val).toFixed(2);
}

function fmtTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  catch(_) { return iso; }
}

function setVal(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
function setText(id, text) { setVal(id, text); }
function setValColor(id, text, num) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'card-value ' + (num > 0 ? 'pos' : num < 0 ? 'neg' : 'neu');
}

function toast(msg, color = 'blue') {
  const map = { green: '#3fb950', red: '#f85149', yellow: '#d29922', blue: '#58a6ff' };
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = map[color] || map.blue;
  el.style.color = map[color] || map.blue;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3500);
}
