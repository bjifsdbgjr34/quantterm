/* QuantTerm front-end core: panels, command line, helpers, market & security functions.
   Quant/analytics functions live in quant.js and register themselves into FUNCS. */
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isNum = x => typeof x === 'number' && isFinite(x);
function fmt(x, d = 2) { return isNum(x) ? x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—'; }
function fmtPx(x) { if (!isNum(x)) return '—'; const a = Math.abs(x); return fmt(x, a >= 1000 ? 2 : a >= 10 ? 2 : a >= 1 ? 4 : 5); }
function pct(x, d = 2, mult = 100) { return isNum(x) ? (x * mult).toFixed(d) + '%' : '—'; }
function big(x) {
  if (!isNum(x)) return '—'; const a = Math.abs(x);
  for (const [v, s] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) if (a >= v) return (x / v).toFixed(2) + s;
  return fmt(x, 0);
}
function cls(x) { return isNum(x) ? (x > 0 ? 'pos' : x < 0 ? 'neg' : '') : ''; }
function signed(x, d = 2, suffix = '') { return isNum(x) ? `<span class="${cls(x)}">${x > 0 ? '+' : ''}${fmt(x, d)}${suffix}</span>` : '—'; }
function spark(vals, w = 80, h = 18) {
  if (!vals || vals.length < 2) return '';
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - (v - mn) / r * h).toFixed(1)}`).join(' ');
  const c = vals[vals.length - 1] >= vals[0] ? '#2bd47d' : '#ff5252';
  return `<svg class="spark" width="${w}" height="${h}"><polyline fill="none" stroke="${c}" stroke-width="1.2" points="${pts}"/></svg>`;
}
async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let j; try { j = await r.json(); } catch { throw new Error(`HTTP ${r.status}`); }
  if (!r.ok) throw new Error(j.error || j.detail || `HTTP ${r.status}`);
  return j;
}
function table(cols, rows, opts = {}) {
  // cols: [{k, h, f(row)=>html, l:left}]
  const th = cols.map(c => `<th class="${c.l ? 'l' : ''}">${c.h}</th>`).join('');
  const tr = rows.map((r, i) => `<tr class="${opts.click ? 'click' : ''}" data-i="${i}">${cols.map(c => `<td class="${c.l ? 'l' : ''} ${c.cls ? c.cls(r) : ''}">${c.f ? c.f(r) : esc(r[c.k])}</td>`).join('')}</tr>`).join('');
  return `<table class="t">${opts.nohead ? '' : `<thead><tr>${th}</tr></thead>`}<tbody>${tr}</tbody></table>`;
}
function kv(obj, fmts = {}) {
  return `<div class="kv">${Object.entries(obj).map(([k, v]) => `<div><span>${esc(k)}</span><span>${fmts[k] ? fmts[k](v) : (isNum(v) ? fmt(v) : esc(v ?? '—'))}</span></div>`).join('')}</div>`;
}
const PCT_KEYS = /Return|CAGR|Volatility|Drawdown|VaR|CVaR|Best|Worst|Hit|Alpha|Tracking|Exposure/;
function statsKV(stats) {
  const o = {};
  for (const [k, v] of Object.entries(stats || {})) o[k] = PCT_KEYS.test(k) && !/days|DD \(/.test(k) ? pct(v) : (k === 'Days' || k.includes('days') ? fmt(v, 0) : fmt(v, 2));
  return kv(o);
}
function card(k, v, c = '') { return `<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`; }
function opts(list, sel) { return list.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${v}" ${String(v) === String(sel) ? 'selected' : ''}>${l}</option>`; }).join(''); }
function formData(form) {
  const o = {};
  $$('input,select,textarea', form).forEach(el => { if (!el.name) return; o[el.name] = el.type === 'checkbox' ? el.checked : el.value; });
  return o;
}

// ------------------------------------------------------------------ charts
const COLORS = ['#fb8b1e', '#4da3ff', '#2bd47d', '#e056fd', '#ffd23f', '#ff5252', '#9be7ff', '#c0c0c0', '#ff9ff3', '#7bed9f'];
function deepMerge(a, b) {
  for (const k in b) {
    if (b[k] && typeof b[k] === 'object' && !Array.isArray(b[k]) && a[k] && typeof a[k] === 'object') deepMerge(a[k], b[k]);
    else a[k] = b[k];
  }
  return a;
}
function layout(extra = {}) {
  const ax = { gridcolor: '#1b1b1b', zerolinecolor: '#333', linecolor: '#333', tickfont: { size: 10 } };
  return deepMerge({
    paper_bgcolor: '#070707', plot_bgcolor: '#070707', font: { family: 'IBM Plex Mono, Menlo, Consolas, monospace', color: '#bdbdbd', size: 11 },
    margin: { l: 52, r: 16, t: 26, b: 28 }, xaxis: { ...ax }, yaxis: { ...ax }, showlegend: true,
    legend: { orientation: 'h', x: 0, y: 1.1, font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
    hovermode: 'x unified', hoverlabel: { bgcolor: '#111', bordercolor: '#fb8b1e', font: { family: 'IBM Plex Mono, monospace' } },
    title: { font: { size: 12, color: '#fb8b1e' }, x: 0.01, y: 0.98 },
  }, extra);
}
function plot(el, traces, lay = {}, cfg = {}) {
  if (typeof el === 'string') el = document.getElementById(el);
  if (!el) return;
  Plotly.newPlot(el, traces, layout(lay), { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'], ...cfg });
}
let _uid = 0; const uid = () => 'c' + (++_uid);
function heatColors(diverging = true) {
  return diverging ? [[0, '#c0392b'], [0.5, '#111'], [1, '#1e8449']] : [[0, '#111'], [1, '#fb8b1e']];
}

// ------------------------------------------------------------------ function registry
const FUNCS = {};
const GROUPS = {};
function reg(code, group, desc, render, o = {}) { FUNCS[code] = { code, group, desc, render, ...o }; (GROUPS[group] ||= []).push(code); }

// ------------------------------------------------------------------ panels
const state = { panels: [], active: 0, layout: 4, history: JSON.parse(localStorage.getItem('qt.hist') || '[]'), hidx: -1 };
const DEFAULT_PANELS = ['WEI', 'SPY GP', 'MC SPY', 'GC', 'W', 'TRADE'];

class Panel {
  constructor(idx) {
    this.idx = idx; this.cmd = ''; this.timer = null; this.st = {};
    this.el = document.createElement('section'); this.el.className = 'panel';
    this.el.innerHTML = `<div class="ptitle"><span class="pn">${idx + 1}</span><span class="pc"></span><span class="pd"></span><span class="src hidden"></span>
      <button title="Refresh" data-a="r">⟳</button><button title="Maximise / restore" data-a="m">▢</button></div><div class="pbody"></div>`;
    this.body = $('.pbody', this.el);
    this.el.addEventListener('mousedown', () => setActive(this.idx));
    $('[data-a=r]', this.el).onclick = e => { e.stopPropagation(); this.run(this.cmd); };
    $('[data-a=m]', this.el).onclick = e => { e.stopPropagation(); toggleMax(this.idx); };
    new ResizeObserver(() => $$('.js-plotly-plot', this.body).forEach(p => Plotly.Plots.resize(p))).observe(this.body);
  }
  setTitle(code, desc) { $('.pc', this.el).textContent = code; $('.pd', this.el).textContent = desc || ''; }
  setSource(src) {
    const s = $('.src', this.el);
    if (!src) { s.classList.add('hidden'); return; }
    s.classList.remove('hidden'); s.className = 'src ' + (src === 'demo' ? 'demo' : 'live');
    s.textContent = src === 'demo' ? 'DEMO DATA' : src.toUpperCase();
  }
  loading(msg = 'LOADING') { this.body.innerHTML = `<div class="loading">${msg}…</div>`; }
  error(e) { this.body.innerHTML = `<div class="err">⚠ ${esc(e.message || e)}</div>`; }
  every(sec, fn) { clearInterval(this.timer); this.timer = setInterval(() => { if (document.visibilityState === 'visible') fn().catch(() => {}); }, sec * 1000); }
  async run(cmdline) {
    clearInterval(this.timer); this.timer = null; this.started = true;
    const p = parse(cmdline);
    if (!p) { this.error(`Unknown command "${cmdline}". Type HELP for the function list.`); return; }
    this.cmd = p.canonical; this.st = {};
    this.setTitle(p.canonical, FUNCS[p.fn].desc); this.setSource(null);
    savePanels();
    this.loading();
    try { await FUNCS[p.fn].render(this, p.sym, p.args); }
    catch (e) { console.error(e); this.error(e); }
  }
}

function parse(line) {
  const toks = line.trim().toUpperCase().replace(/<GO>|\bGO\b/g, '').split(/\s+/).filter(Boolean);
  if (!toks.length) return null;
  let fn, sym = null, args = [];
  if (FUNCS[toks[0]]) { fn = toks[0]; args = toks.slice(1); }
  else {
    const i = toks.findIndex(t => FUNCS[t]);
    if (i > 0) { fn = toks[i]; sym = toks.slice(0, i).join(' '); args = toks.slice(i + 1); }
    else { fn = 'DES'; sym = toks.join(' '); }
  }
  const f = FUNCS[fn];
  if (!f.sym && sym) { args = [...sym.split(' '), ...args]; sym = null; }
  if (f.sym && !sym) { sym = args.shift() || f.defaultSym || null; }
  if (f.sym && !sym) return null;
  const canonical = f.sym ? `${sym} ${fn}${args.length ? ' ' + args.join(' ') : ''}` : `${fn}${args.length ? ' ' + args.join(' ') : ''}`;
  return { fn, sym, args, canonical };
}

function buildGrid() {
  const g = $('#grid'); g.innerHTML = ''; g.className = 'l' + state.layout;
  state.panels.forEach((p, i) => { p.el.classList.toggle('hidden', i >= state.layout); g.appendChild(p.el); });
  $$('#layouts button').forEach(b => b.classList.toggle('on', +b.dataset.layout === state.layout));
  if (state.active >= state.layout) setActive(0);
  setTimeout(() => $$('.js-plotly-plot').forEach(p => Plotly.Plots.resize(p)), 50);
}
function setLayout(n) {
  state.layout = n;
  state.panels.forEach((p, i) => { if (i < n && !p.started) p.run(p.cmd || DEFAULT_PANELS[i] || 'HELP'); });
  localStorage.setItem('qt.layout', n); buildGrid();
}
let _maxRestore = null;
function toggleMax(i) {
  if (_maxRestore) { const n = _maxRestore; _maxRestore = null; state.panels.forEach(p => p.el.classList.remove('hidden')); setLayout(n); return; }
  _maxRestore = state.layout; const g = $('#grid'); g.className = 'l1';
  state.panels.forEach((p, j) => p.el.classList.toggle('hidden', j !== i)); setActive(i);
  setTimeout(() => $$('.js-plotly-plot').forEach(p => Plotly.Plots.resize(p)), 50);
}
function setActive(i) { state.active = i; state.panels.forEach((p, j) => p.el.classList.toggle('active', i === j)); }
function savePanels() { localStorage.setItem('qt.panels', JSON.stringify(state.panels.map(p => p.cmd))); }
function runIn(cmdline, idx = state.active) { state.panels[idx].run(cmdline); setActive(idx); }
function goto(cmdline) { runIn(cmdline, state.active); }

// ------------------------------------------------------------------ command line + autocomplete
const cmd = $('#cmd'), sug = $('#suggest');
let sugItems = [], sugSel = -1, searchTimer = null;
function renderSuggest() {
  if (!sugItems.length) { sug.classList.add('hidden'); return; }
  sug.innerHTML = sugItems.map((s, i) => `<div class="${i === sugSel ? 'sel' : ''}" data-i="${i}"><b>${esc(s.label)}</b><span>${esc(s.desc)}</span><i>${esc(s.tag || '')}</i></div>`).join('');
  sug.classList.remove('hidden');
  $$('div', sug).forEach(d => d.onmousedown = e => { e.preventDefault(); applySuggest(sugItems[+d.dataset.i]); });
}
function applySuggest(s) { cmd.value = s.value; sugItems = []; renderSuggest(); cmd.focus(); if (s.run) submit(); }
function updateSuggest() {
  const v = cmd.value.trim().toUpperCase(); sugSel = -1;
  if (!v) { sugItems = []; renderSuggest(); return; }
  const toks = v.split(/\s+/), last = toks[toks.length - 1];
  const fnMatches = Object.values(FUNCS).filter(f => f.code.startsWith(last) || (toks.length === 1 && f.desc.toUpperCase().includes(v)))
    .slice(0, 8).map(f => ({ label: f.code, desc: f.desc, tag: f.group, value: toks.length > 1 ? [...toks.slice(0, -1), f.code].join(' ') : (f.sym ? f.code + ' ' : f.code), run: !f.sym || toks.length > 1 }));
  sugItems = fnMatches; renderSuggest();
  clearTimeout(searchTimer);
  if (toks.length === 1 && v.length >= 2 && !FUNCS[v]) {
    searchTimer = setTimeout(async () => {
      try {
        const r = await api('/api/search?q=' + encodeURIComponent(v));
        if (cmd.value.trim().toUpperCase() !== v) return;
        sugItems = [...fnMatches.slice(0, 4), ...r.results.map(x => ({ label: x.symbol, desc: x.name || '', tag: `${x.type || ''} ${x.exchange || ''}`, value: x.symbol + ' ', run: false }))];
        renderSuggest();
      } catch { }
    }, 220);
  }
}
function submit(newPanel = false) {
  const v = cmd.value.trim(); if (!v) return;
  state.history = [v, ...state.history.filter(h => h !== v)].slice(0, 100); localStorage.setItem('qt.hist', JSON.stringify(state.history)); state.hidx = -1;
  const target = newPanel ? (state.active + 1) % state.layout : state.active;
  runIn(v, target); cmd.value = ''; sugItems = []; renderSuggest();
}
cmd.addEventListener('input', updateSuggest);
cmd.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (sugSel >= 0 && sugItems[sugSel]) applySuggest({ ...sugItems[sugSel], run: sugItems[sugSel].run });
    else submit(e.shiftKey);
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (sugItems.length) { sugSel = Math.min(sugSel + 1, sugItems.length - 1); renderSuggest(); }
    else if (state.hidx > 0) { state.hidx--; cmd.value = state.history[state.hidx]; } else { state.hidx = -1; cmd.value = ''; }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (sugItems.length && sugSel > 0) { sugSel--; renderSuggest(); }
    else if (!sugItems.length || sugSel <= 0) { if (state.hidx < state.history.length - 1) { state.hidx++; cmd.value = state.history[state.hidx]; sugItems = []; renderSuggest(); } }
  } else if (e.key === 'Escape') { cmd.value = ''; sugItems = []; renderSuggest(); }
  else if (e.key === 'Tab' && sugItems.length) { e.preventDefault(); applySuggest({ ...sugItems[Math.max(sugSel, 0)], run: false }); }
});
cmd.addEventListener('blur', () => setTimeout(() => { sugItems = []; renderSuggest(); }, 150));
document.addEventListener('keydown', e => {
  if (e.key === 'F1') { e.preventDefault(); goto('HELP'); }
  if (e.ctrlKey && /^[1-6]$/.test(e.key)) { e.preventDefault(); const i = +e.key - 1; if (i < state.layout) setActive(i); }
  const tag = (document.activeElement || {}).tagName;
  if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) cmd.focus();
});
$$('#layouts button').forEach(b => b.onclick = () => { _maxRestore = null; setLayout(+b.dataset.layout); });

// ------------------------------------------------------------------ clocks / tape / status
const ZONES = [['ZRH', 'Europe/Zurich', 9, 17.5], ['LDN', 'Europe/London', 8, 16.5], ['NY', 'America/New_York', 9.5, 16], ['TKY', 'Asia/Tokyo', 9, 15.5]];
function tickClocks() {
  const now = new Date();
  $('#clocks').innerHTML = ZONES.map(([n, tz, o, c]) => {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', weekday: 'short', hour12: false }).formatToParts(now);
    const h = +parts.find(p => p.type === 'hour').value, m = +parts.find(p => p.type === 'minute').value, wd = parts.find(p => p.type === 'weekday').value;
    const t = h + m / 60, open = !['Sat', 'Sun'].includes(wd) && t >= o && t < c;
    return `<span>${n} <b class="${open ? 'open' : ''}">${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}</b></span>`;
  }).join('');
  $('#st-time').textContent = now.toLocaleString();
}
async function loadTape() {
  try {
    const groups = await Promise.all(['WEI', 'FX', 'CMDT', 'CRYPTO'].map(g => api('/api/monitor/' + g)));
    const qs = groups.flatMap(g => g.quotes).filter(q => isNum(q.last));
    $('#tapeinner').innerHTML = qs.map(q => `<span class="tk" data-s="${esc(q.symbol)}"><b>${esc(q.name || q.symbol)}</b>${fmtPx(q.last)} <span class="${cls(q.pct)}">${q.pct > 0 ? '▲' : '▼'} ${fmt(q.pct)}%</span></span>`).join('');
    $$('.tk').forEach(t => t.onclick = () => goto(t.dataset.s + ' GP'));
  } catch { }
}
async function loadStatus() {
  try {
    const s = await api('/api/status');
    $('#st-mode').innerHTML = `MODE <b class="amb">${s.mode.toUpperCase()}</b>`;
    $('#st-src').innerHTML = `YAHOO <b class="${s.yahoo ? 'pos' : 'neg'}">${s.yahoo ? 'ONLINE' : 'OFFLINE'}</b> · FRED <b class="${s.fred ? 'pos' : 'neg'}">${s.fred ? 'ONLINE' : 'OFFLINE'}</b>${!s.yahoo ? ' · <span class="neg">showing synthetic demo data</span>' : ''}`;
  } catch { $('#st-mode').innerHTML = '<span class="neg">SERVER UNREACHABLE</span>'; }
}

// ================================================================== MARKET MONITORS
const MONITOR_DESC = { WEI: 'World Equity Indices', FX: 'Currency Monitor', CMDT: 'Commodities', CRYPTO: 'Crypto', RATES: 'US Treasury Yields',
  MAG7: 'Magnificent 7', SWISS: 'Swiss Blue Chips (SMI)', SECTORS: 'US Sector ETFs' };
for (const [code, desc] of Object.entries(MONITOR_DESC)) {
  reg(code, 'Markets', desc, async p => {
    const load = async () => {
      const r = await api('/api/monitor/' + code); p.setSource(r.source);
      const rows = r.quotes.filter(q => q.last !== null);
      const sorted = code === 'SECTORS' ? [...rows].sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0)) : rows;
      p.body.innerHTML = table([
        { h: 'Security', l: 1, f: q => `<b>${esc(q.name || q.symbol)}</b> <span class="dim">${esc(q.symbol)}</span>` },
        { h: 'Last', f: q => fmtPx(q.last) }, { h: 'Net Chg', f: q => signed(q.chg, q.last < 10 ? 4 : 2) },
        { h: '%Chg', f: q => signed(q.pct, 2, '%') }, { h: 'WTD', f: q => signed(q.wtd, 2, '%') }, { h: 'YTD', f: q => signed(q.ytd, 2, '%') },
        { h: '30D', f: q => spark(q.spark) }, { h: 'Date', f: q => `<span class="dim">${q.time}</span>` },
      ], sorted, { click: 1 }) + `<div class="dim" style="margin-top:6px">Click a row for chart · refreshes every 30s</div>`;
      $$('tr.click', p.body).forEach(tr => tr.onclick = () => goto(sorted[+tr.dataset.i].symbol + ' GP'));
    };
    await load(); p.every(30, load);
  });
}

// ================================================================== WATCHLIST
function wl() { return JSON.parse(localStorage.getItem('qt.watch') || '["AAPL","MSFT","NVDA","SPY","QQQ","NESN.SW","BTC-USD","GC=F"]'); }
reg('W', 'Markets', 'Watchlist (add/remove symbols, saved locally)', async (p, _s, args) => {
  if (args.length) { localStorage.setItem('qt.watch', JSON.stringify([...new Set([...wl(), ...args])])); }
  const load = async () => {
    const list = wl();
    const tools = `<div class="toolbar"><input id="wa${p.idx}" placeholder="add symbol(s)…" style="width:200px"><button id="wb${p.idx}">ADD</button><span class="dim">Click a symbol for its overview</span></div>`;
    if (!list.length) { p.body.innerHTML = tools; }
    else {
      const r = await api('/api/quote?symbols=' + encodeURIComponent(list.join(','))); p.setSource(r.source);
      p.body.innerHTML = tools + table([
        { h: 'Symbol', l: 1, f: q => `<b class="amb">${esc(q.symbol)}</b>` }, { h: 'Last', f: q => fmtPx(q.last) },
        { h: 'Chg', f: q => signed(q.chg) }, { h: '%Chg', f: q => signed(q.pct, 2, '%') }, { h: 'WTD', f: q => signed(q.wtd, 2, '%') },
        { h: 'YTD', f: q => signed(q.ytd, 2, '%') }, { h: 'Volume', f: q => big(q.volume) }, { h: '30D', f: q => spark(q.spark) },
        { h: '', f: q => `<button class="danger" data-rm="${esc(q.symbol)}">✕</button>` },
      ], r.quotes, { click: 1 });
      $$('tr.click', p.body).forEach(tr => tr.onclick = e => { if (e.target.dataset.rm) return; goto(r.quotes[+tr.dataset.i].symbol + ' DES'); });
      $$('[data-rm]', p.body).forEach(b => b.onclick = e => { e.stopPropagation(); localStorage.setItem('qt.watch', JSON.stringify(wl().filter(s => s !== b.dataset.rm))); load(); });
    }
    const add = () => { const v = $(`#wa${p.idx}`).value.toUpperCase().split(/[\s,]+/).filter(Boolean); if (v.length) { localStorage.setItem('qt.watch', JSON.stringify([...new Set([...wl(), ...v])])); load(); } };
    $(`#wb${p.idx}`).onclick = add; $(`#wa${p.idx}`).onkeydown = e => { if (e.key === 'Enter') { e.stopPropagation(); add(); } };
  };
  await load(); p.every(30, load);
});

// ================================================================== DES
reg('DES', 'Security', 'Description, key stats & risk profile', async (p, sym) => {
  const r = await api('/api/des/' + encodeURIComponent(sym)); p.setSource(r.source);
  const i = r.info, q = r.quote, s = r.stats_1y || {}, id = uid();
  p.setTitle(`${r.symbol} DES`, i.longName || i.shortName || '');
  const fundamentals = {
    'Market Cap': big(i.marketCap), 'Enterprise Value': big(i.enterpriseValue), 'P/E (TTM)': fmt(i.trailingPE), 'P/E (Fwd)': fmt(i.forwardPE),
    'PEG': fmt(i.pegRatio), 'P/B': fmt(i.priceToBook), 'P/S': fmt(i.priceToSalesTrailing12Months), 'EV/EBITDA': fmt(i.enterpriseToEbitda),
    'EPS (TTM)': fmt(i.trailingEps), 'EPS (Fwd)': fmt(i.forwardEps), 'Div Yield': isNum(i.dividendYield) ? fmt(i.dividendYield) + '%' : '—',
    'Beta (5Y)': fmt(i.beta), 'Revenue': big(i.totalRevenue), 'EBITDA': big(i.ebitda), 'Profit Margin': pct(i.profitMargins),
    'Oper. Margin': pct(i.operatingMargins), 'ROE': pct(i.returnOnEquity), 'ROA': pct(i.returnOnAssets), 'Rev Growth': pct(i.revenueGrowth),
    'Debt/Equity': fmt(i.debtToEquity), 'Free Cash Flow': big(i.freeCashflow), 'Short % Float': pct(i.shortPercentOfFloat),
    'Inst. Held': pct(i.heldPercentInstitutions), 'Employees': big(i.fullTimeEmployees),
    'Analyst Rec': (i.recommendationKey || '—').toUpperCase(), 'Target (mean)': fmtPx(i.targetMeanPrice), '# Analysts': i.numberOfAnalystOpinions ?? '—',
  };
  const risk = {
    '1Y Return': pct(s['Total Return']), '1Y Volatility': pct(s['Volatility']), 'Sharpe (1Y)': fmt(s['Sharpe']), 'Sortino (1Y)': fmt(s['Sortino']),
    'Max DD (1Y)': pct(s['Max Drawdown']), 'Beta vs SPX': fmt(s['Beta']), 'Corr vs SPX': fmt(s['Correlation']), 'VaR95 1d': pct(s['VaR 95% (1d)']),
    'RV 10d': fmt(r.realized_vol['10d']) + '%', 'RV 30d': fmt(r.realized_vol['30d']) + '%', 'RV 90d': fmt(r.realized_vol['90d']) + '%',
    '52W High': fmtPx(r.range_52w.high), '52W Low': fmtPx(r.range_52w.low),
  };
  const fromHi = q.last / r.range_52w.high - 1;
  p.body.innerHTML = `
    <div class="row" style="align-items:flex-end">
      <div><h2>${esc(i.longName || i.shortName || r.symbol)}</h2>
        <span class="tag">${esc(r.symbol)}</span><span class="tag">${esc(i.quoteType || '')}</span><span class="tag">${esc(i.exchange || '')}</span>
        <span class="tag">${esc(i.currency || '')}</span><span class="tag">${esc(i.sector || '')}</span><span class="tag">${esc(i.industry || '')}</span><span class="tag">${esc(i.country || '')}</span></div>
      <div style="text-align:right"><span class="big">${fmtPx(q.last)}</span> ${signed(q.chg)} ${signed(q.pct, 2, '%')}<br>
        <span class="dim">YTD</span> ${signed(q.ytd, 2, '%')} · <span class="dim">from 52W high</span> ${signed(fromHi * 100, 1, '%')} · <span class="dim">${q.time}</span></div>
    </div>
    <div class="summary">${esc(i.longBusinessSummary || '')} ${i.website ? `<a href="${esc(i.website)}" target="_blank">${esc(i.website)}</a>` : ''}</div>
    <div class="toolbar">${['GP', 'HP', 'FA', 'N', 'OMON', 'VOL', 'STAT', 'GARCH', 'MC', 'BETA', 'BT', 'OVME'].map(f => `<button data-go="${f}">${f}</button>`).join('')}
      <button data-trade="1" class="primary">TRADE</button><button data-watch="1">+ WATCH</button></div>
    <div id="${id}" class="chart short"></div>
    <div class="row"><div><h3>Fundamentals</h3>${kv(fundamentals)}</div><div><h3>Risk & Performance (1Y)</h3>${kv(risk)}</div></div>`;
  $$('[data-go]', p.body).forEach(b => b.onclick = () => goto(`${r.symbol} ${b.dataset.go}`));
  $('[data-trade]', p.body).onclick = () => goto(`TRADE ${r.symbol}`);
  $('[data-watch]', p.body).onclick = e => { localStorage.setItem('qt.watch', JSON.stringify([...new Set([...wl(), r.symbol])])); e.target.textContent = '✓ WATCHING'; };
  plot(id, [{ x: r.spark.dates, y: r.spark.close, type: 'scatter', mode: 'lines', line: { color: '#fb8b1e', width: 1.5 }, fill: 'tozeroy', fillcolor: 'rgba(251,139,30,0.08)', name: r.symbol }],
    { showlegend: false, margin: { t: 8 }, yaxis: { range: [Math.min(...r.spark.close) * 0.97, Math.max(...r.spark.close) * 1.02] } });
}, { sym: 1 });

// ================================================================== GP
const PERIODS = ['1mo', '3mo', '6mo', 'ytd', '1y', '2y', '5y', '10y', 'max'];
const INDS = [['sma20', 'SMA20'], ['sma50', 'SMA50'], ['sma200', 'SMA200'], ['ema21', 'EMA21'], ['bb', 'Bollinger'], ['vwap', 'VWAP'],
  ['rsi', 'RSI'], ['macd', 'MACD'], ['stoch', 'Stoch'], ['atr', 'ATR'], ['vol', 'RealVol'], ['dd', 'Drawdown']];
reg('GP', 'Security', 'Price chart: candles, volume, technical indicators', async (p, sym, args) => {
  const st = p.st;
  st.period ||= (args.find(a => PERIODS.includes(a.toLowerCase())) || '1y').toLowerCase();
  st.inds ||= new Set(['sma50', 'sma200', 'bb', 'rsi', 'macd']);
  st.type ||= 'candle'; st.log ??= false;
  const draw = async () => {
    const r = await api(`/api/chart/${encodeURIComponent(sym)}?period=${st.period}&interval=${['5y', '10y', 'max'].includes(st.period) ? '1wk' : '1d'}&ind=${[...st.inds].join(',')}`);
    p.setSource(r.source); p.setTitle(`${r.symbol} GP`, `Price chart · ${st.period.toUpperCase()}`);
    const id = uid(), last = r.close[r.close.length - 1], first = r.close[0];
    p.body.innerHTML = `<div class="toolbar">${PERIODS.map(x => `<button data-p="${x}" class="${x === st.period ? 'on' : ''}">${x.toUpperCase()}</button>`).join('')}<span class="sep"></span>
      ${INDS.map(([k, l]) => `<label><input type="checkbox" data-i="${k}" ${st.inds.has(k) ? 'checked' : ''}>${l}</label>`).join('')}<span class="sep"></span>
      <select data-t>${opts([['candle', 'Candles'], ['ohlc', 'OHLC'], ['line', 'Line']], st.type)}</select><label><input type="checkbox" data-log ${st.log ? 'checked' : ''}>Log</label>
      <span style="margin-left:auto"><b>${fmtPx(last)}</b> <span class="dim">period</span> ${signed((last / first - 1) * 100, 2, '%')}</span></div>
      <div id="${id}" class="chart fill"></div>`;
    const panels = Object.entries(r.panels), np = panels.length;
    const mainH = np ? 0.62 - np * 0.02 : 0.82, volH = 0.12, gap = 0.02;
    const traces = [];
    if (st.type === 'line') traces.push({ x: r.dates, y: r.close, type: 'scatter', mode: 'lines', name: r.symbol, line: { color: '#fb8b1e', width: 1.6 } });
    else traces.push({ x: r.dates, open: r.open, high: r.high, low: r.low, close: r.close, type: st.type === 'ohlc' ? 'ohlc' : 'candlestick', name: r.symbol,
      increasing: { line: { color: '#2bd47d' }, fillcolor: '#2bd47d' }, decreasing: { line: { color: '#ff5252' }, fillcolor: '#ff5252' } });
    Object.entries(r.overlays).forEach(([k, v], i) => traces.push({ x: r.dates, y: v, type: 'scatter', mode: 'lines', name: k,
      line: { width: 1, color: k.startsWith('BB') ? '#4da3ff' : COLORS[(i + 1) % COLORS.length], dash: k.startsWith('BB') ? 'dot' : 'solid' } }));
    traces.push({ x: r.dates, y: r.volume, type: 'bar', name: 'Volume', yaxis: 'y2', marker: { color: r.close.map((c, i) => i && c < r.close[i - 1] ? 'rgba(255,82,82,.45)' : 'rgba(43,212,125,.45)') }, showlegend: false });
    const lay = { margin: { t: 26, r: 50 }, xaxis: { rangeslider: { visible: false }, type: 'date' }, hovermode: 'x unified',
      yaxis: { domain: [1 - mainH, 1], side: 'right', type: st.log ? 'log' : 'linear' },
      yaxis2: { domain: [1 - mainH - volH - gap, 1 - mainH - gap], side: 'right', showgrid: false, tickformat: '.2s' } };
    if (!/^(BTC|ETH|SOL)|-USD$/.test(r.symbol)) lay.xaxis.rangebreaks = [{ bounds: ['sat', 'mon'] }];
    const rest = 1 - mainH - volH - 2 * gap, each = np ? rest / np : 0;
    panels.forEach(([name, series], k) => {
      const ax = `y${k + 3}`, top = rest - k * each;
      lay[`yaxis${k + 3}`] = { domain: [Math.max(top - each + gap, 0), top], side: 'right', title: { text: name, font: { size: 9, color: '#fb8b1e' } } };
      Object.entries(series).forEach(([sn, v], j) => traces.push(sn === 'Hist'
        ? { x: r.dates, y: v, type: 'bar', name: sn, yaxis: ax, marker: { color: v.map(x => x >= 0 ? '#2bd47d' : '#ff5252') }, showlegend: false }
        : { x: r.dates, y: v, type: 'scatter', mode: 'lines', name: `${name}:${sn}`, yaxis: ax, line: { width: 1.1, color: COLORS[(j + 1) % COLORS.length] }, showlegend: false }));
      if (name.startsWith('RSI')) lay.shapes = [...(lay.shapes || []), ...[30, 70].map(y => ({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: ax, y0: y, y1: y, line: { color: '#555', dash: 'dot', width: 1 } }))];
    });
    plot(id, traces, lay);
    $$('[data-p]', p.body).forEach(b => b.onclick = () => { st.period = b.dataset.p; draw(); });
    $$('[data-i]', p.body).forEach(c => c.onchange = () => { c.checked ? st.inds.add(c.dataset.i) : st.inds.delete(c.dataset.i); draw(); });
    $('[data-t]', p.body).onchange = e => { st.type = e.target.value; draw(); };
    $('[data-log]', p.body).onchange = e => { st.log = e.target.checked; draw(); };
  };
  await draw();
}, { sym: 1 });

// ================================================================== HP
reg('HP', 'Security', 'Historical prices table', async (p, sym, args) => {
  const per = (args[0] || '3mo').toLowerCase();
  const r = await api(`/api/hp/${encodeURIComponent(sym)}?period=${per}`); p.setSource(r.source);
  p.body.innerHTML = `<div class="toolbar">${['1mo', '3mo', '6mo', '1y', '2y', '5y'].map(x => `<button data-p="${x}" class="${x === per ? 'on' : ''}">${x.toUpperCase()}</button>`).join('')}
    <button data-csv>⤓ CSV</button></div>` + table([{ h: 'Date', k: 'date' }, { h: 'Open', f: x => fmtPx(x.Open) }, { h: 'High', f: x => fmtPx(x.High) },
    { h: 'Low', f: x => fmtPx(x.Low) }, { h: 'Close', f: x => `<b>${fmtPx(x.Close)}</b>` }, { h: 'Chg %', f: x => signed(x['Change %'], 2, '%') }, { h: 'Volume', f: x => big(x.Volume) }], r.rows);
  $$('[data-p]', p.body).forEach(b => b.onclick = () => p.run(`${sym} HP ${b.dataset.p}`));
  $('[data-csv]', p.body).onclick = () => {
    const csv = 'date,open,high,low,close,volume\n' + r.rows.map(x => [x.date, x.Open, x.High, x.Low, x.Close, x.Volume].join(',')).join('\n');
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = `${sym}_${per}.csv`; a.click();
  };
}, { sym: 1 });

// ================================================================== FA
reg('FA', 'Security', 'Financial statements (income, balance sheet, cash flow)', async (p, sym, args) => {
  const st = p.st; st.q ??= args.includes('Q'); st.tab ||= 'income';
  const draw = async () => {
    const r = await api(`/api/fa/${encodeURIComponent(sym)}?quarterly=${st.q}`); p.setSource(r.source);
    const d = r[st.tab], id = uid();
    p.body.innerHTML = `<div class="toolbar">${[['income', 'Income Statement'], ['balance', 'Balance Sheet'], ['cashflow', 'Cash Flow']].map(([k, l]) => `<button data-t="${k}" class="${k === st.tab ? 'on' : ''}">${l}</button>`).join('')}
      <span class="sep"></span><button data-q="0" class="${!st.q ? 'on' : ''}">Annual</button><button data-q="1" class="${st.q ? 'on' : ''}">Quarterly</button><span class="dim">Click a row to chart it</span></div>
      <div id="${id}" class="chart short"></div>` +
      (d.rows.length ? table([{ h: 'Item', l: 1, f: x => esc(x.item) }, ...d.columns.map((c, j) => ({ h: c, f: x => big(x.values[j]) }))], d.rows, { click: 1 }) : '<div class="dim">No data</div>');
    const chartRow = row => plot(id, [{ x: [...d.columns].reverse(), y: [...row.values].reverse(), type: 'bar', marker: { color: '#fb8b1e' }, name: row.item }], { title: { text: row.item }, showlegend: false, hovermode: 'closest' });
    const pick = d.rows.find(x => /Total Revenue|Total Assets|Free Cash Flow/.test(x.item)) || d.rows[0];
    if (pick) chartRow(pick);
    $$('tr.click', p.body).forEach(tr => tr.onclick = () => chartRow(d.rows[+tr.dataset.i]));
    $$('[data-t]', p.body).forEach(b => b.onclick = () => { st.tab = b.dataset.t; draw(); });
    $$('[data-q]', p.body).forEach(b => b.onclick = () => { st.q = b.dataset.q === '1'; draw(); });
  };
  await draw();
}, { sym: 1 });

// ================================================================== NEWS
reg('N', 'Security', 'Company news headlines', async (p, sym) => {
  const r = await api('/api/news/' + encodeURIComponent(sym)); p.setSource(r.source);
  p.body.innerHTML = `<div class="news">${r.items.map(n => `<div>${n.url ? `<a href="${esc(n.url)}" target="_blank">${esc(n.title)}</a>` : esc(n.title)}<br>
    <span class="amb">${esc(n.publisher || '')}</span> <span class="dim">${esc((n.time || '').replace('T', ' ').slice(0, 16))}</span>
    ${n.summary ? `<div class="dim" style="border:none;padding:2px 0">${esc(n.summary)}</div>` : ''}</div>`).join('')}</div>`;
}, { sym: 1, defaultSym: 'SPY' });

// ================================================================== SEARCH
reg('SRCH', 'Markets', 'Search securities by name or ticker', async (p, _s, args) => {
  const q = args.join(' ');
  if (!q) { p.body.innerHTML = '<div class="dim">Usage: SRCH apple</div>'; return; }
  const r = await api('/api/search?q=' + encodeURIComponent(q)); p.setSource(r.source);
  p.body.innerHTML = table([{ h: 'Symbol', l: 1, f: x => `<b class="amb">${esc(x.symbol)}</b>` }, { h: 'Name', l: 1, k: 'name' }, { h: 'Type', l: 1, k: 'type' }, { h: 'Exchange', l: 1, k: 'exchange' }], r.results, { click: 1 });
  $$('tr.click', p.body).forEach(tr => tr.onclick = () => goto(r.results[+tr.dataset.i].symbol + ' DES'));
});

// ================================================================== HELP
reg('HELP', 'System', 'Function directory & keyboard guide', async p => {
  const order = ['Markets', 'Security', 'Analytics', 'Derivatives', 'Quant Lab', 'Monte Carlo', 'Portfolio & Trading', 'Macro', 'System'];
  const ex = { DES: 'AAPL', GP: 'NVDA GP 2Y', HP: 'MSFT HP', FA: 'AAPL FA', N: 'TSLA N', G: 'G AAPL MSFT NVDA', CORR: 'CORR SPY TLT GLD BTC-USD', BETA: 'NVDA BETA',
    VOL: 'SPY VOL', OMON: 'AAPL OMON', VOLS: 'SPY VOLS', OVME: 'OVME AAPL', BT: 'BT SPY', PORT: 'PORT SPY TLT GLD QQQ', MC: 'MC NVDA', MCO: 'MCO',
    MCP: 'MCP SPY TLT GLD', GARCH: 'SPY GARCH', STAT: 'SPY STAT', PAIR: 'PAIR XOM CVX', PCA: 'PCA XLK XLF XLE XLV XLI XLY XLP', TRADE: 'TRADE AAPL',
    GC: 'GC', ECO: 'ECO', W: 'W', SRCH: 'SRCH nestle' };
  p.body.innerHTML = `<h2>QuantTerm function directory</h2>
    <p class="dim">Syntax like Bloomberg: <span class="code">TICKER FUNCTION</span> or <span class="code">FUNCTION ARGS</span>. A bare ticker opens DES.
    Bloomberg-style tickers work too: <span class="code">SPX INDEX</span>, <span class="code">EURUSD CURNCY</span>, <span class="code">SMI</span>. Swiss stocks use <span class="code">.SW</span> (e.g. NESN.SW).</p>
    <div class="helpgrid">${order.filter(g => GROUPS[g]).map(g => `<section><h3>${g}</h3>${GROUPS[g].map(c => `<p data-c="${esc(ex[c] || c)}"><b>${c}</b>${esc(FUNCS[c].desc)} <span class="dim">${ex[c] ? '· ' + esc(ex[c]) : ''}</span></p>`).join('')}</section>`).join('')}</div>
    <h3>Keyboard</h3><p class="dim">Start typing anywhere to focus the command line · Enter = run in focused panel · Shift+Enter = run in next panel · Tab = accept suggestion · ↑/↓ = history · Ctrl+1…6 = focus panel · F1 = help · ▢ on a panel = maximise.</p>`;
  $$('[data-c]', p.body).forEach(e => e.onclick = () => goto(e.dataset.c));
});

// ------------------------------------------------------------------ boot
window.addEventListener('load', () => {
  const saved = JSON.parse(localStorage.getItem('qt.panels') || 'null');
  const n = +(localStorage.getItem('qt.layout') || 4);
  for (let i = 0; i < 6; i++) state.panels.push(new Panel(i));
  state.layout = n; buildGrid(); setActive(0);
  state.panels.forEach((p, i) => { if (i < n) p.run((saved && saved[i]) || DEFAULT_PANELS[i] || 'HELP'); else p.cmd = (saved && saved[i]) || ''; });
  tickClocks(); setInterval(tickClocks, 15000);
  loadStatus(); setInterval(loadStatus, 60000);
  loadTape(); setInterval(loadTape, 60000);
  cmd.focus();
});
