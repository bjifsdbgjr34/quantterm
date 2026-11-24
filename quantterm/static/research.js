/* QuantTerm research & workflow functions: screener, heatmap, live intraday, alerts, events/earnings,
   factor models, walk-forward, multi-asset backtests, option skew & scanner, notes, workspaces,
   AI assistant and the morning briefing. */
'use strict';

const errBox = e => `<div class="err">⚠ ${esc(e.message || e)}</div>`;
const sourceOf = (...xs) => xs.includes('demo') ? 'demo' : xs.find(Boolean);

// ================================================================== EQS — equity screener
reg('EQS', 'Research', 'Equity screener: filter S&P 100 by valuation, momentum, volatility, sector', async (p, _s, args) => {
  const st = p.st; st.filter ??= args.join(' ').toLowerCase(); st.sort ||= 'mom_12_1';
  p.loading('SCREENING ~100 STOCKS (first load fetches fundamentals, can take 30s+)');
  const draw = async () => {
    const r = await api('/api/screener', { filter: st.filter, sort: st.sort, ascending: st.asc || false }); p.setSource(r.source);
    const cols = [['symbol', 'Symbol'], ['name', 'Name'], ['sector', 'Sector'], ['last', 'Last'], ['chg_1d', '1D %'], ['ret_1m', '1M %'], ['ret_ytd', 'YTD %'], ['mom_12_1', '12-1 Mom %'],
      ['vol_63', 'Vol 63d'], ['from_high', 'From 52W Hi'], ['rsi14', 'RSI'], ['beta', 'Beta'], ['pe', 'P/E'], ['fpe', 'Fwd P/E'], ['pb', 'P/B'], ['mcap_b', 'Mcap $bn'], ['div', 'Div %'], ['roe', 'ROE %'], ['margin', 'Margin %'], ['growth', 'Rev Gr %']];
    const pctCols = new Set(['chg_1d', 'ret_1m', 'ret_ytd', 'mom_12_1', 'from_high']);
    p.body.innerHTML = `<form class="form" style="grid-template-columns:1fr auto">
        <label>Filter (fields below; and / or / not; text in quotes)<input name="f" value="${esc(st.filter)}" placeholder='e.g. pe < 25 and mom_12_1 > 10 and sector == "Technology"'></label>
        <div class="actions" style="align-items:flex-end"><button class="primary">SCREEN</button></div></form>
      <div class="toolbar"><span class="dim">Presets:</span>${Object.entries(r.presets).map(([k, v]) => `<button data-pre="${esc(v)}">${esc(k)}</button>`).join('')}<button data-pre="">Clear</button>
        <span style="margin-left:auto"><b class="amb">${r.count}</b> of ${r.total} match</span></div>
      <div style="max-height:calc(100% - 120px);overflow:auto">${table(cols.map(([k, h]) => ({ h: `<span data-sort="${k}" style="cursor:pointer">${h}${st.sort === k ? (st.asc ? ' ▲' : ' ▼') : ''}</span>`, l: ['symbol', 'name', 'sector'].includes(k),
        f: x => k === 'symbol' ? `<b class="amb">${esc(x.symbol)}</b>` : ['name', 'sector'].includes(k) ? `<span class="dim">${esc((x[k] || '').slice(0, 22))}</span>` : k === 'mcap_b' ? fmt(x[k], 0) : pctCols.has(k) ? signed(x[k], 1) : k === 'last' ? fmtPx(x[k]) : fmt(x[k], 1) })), r.rows, { click: 1 })}</div>
      <details><summary class="dim">Field reference</summary>${kv(r.fields)}</details>
      <p class="dim">Universe: ~100 S&P 500 large caps across 11 sectors. Fundamentals are cached 6h. Screens describe the past and do not predict returns.</p>`;
    $('form', p.body).onsubmit = e => { e.preventDefault(); st.filter = $('[name=f]', p.body).value; draw().catch(er => p.error(er)); };
    $$('[data-pre]', p.body).forEach(b => b.onclick = () => { st.filter = b.dataset.pre; draw().catch(er => p.error(er)); });
    $$('[data-sort]', p.body).forEach(h => h.onclick = e => { e.stopPropagation(); const k = h.dataset.sort; st.asc = st.sort === k ? !st.asc : false; st.sort = k; draw().catch(er => p.error(er)); });
    $$('tr.click', p.body).forEach(tr => tr.onclick = () => goto(r.rows[+tr.dataset.i].symbol + ' DES'));
  };
  await draw();
});

// ================================================================== MAP — sector heatmap / treemap
reg('MAP', 'Research', 'Market heatmap: S&P 100 treemap by sector, coloured by move', async (p, _s, args) => {
  const st = p.st; st.metric ||= ({ '1M': 'ret_1m', 'YTD': 'ret_ytd', 'MOM': 'mom_12_1' })[args[0]] || 'chg_1d';
  p.loading('BUILDING HEATMAP (first load can take 30s+)');
  const draw = async () => {
    const r = await api('/api/heatmap?metric=' + st.metric); p.setSource(r.source);
    const id = uid(), b = r.breadth, rows = r.rows.filter(x => isNum(x.value));
    const lim = st.metric === 'chg_1d' ? 3 : st.metric === 'ret_1m' ? 10 : 30;
    const ids = ['All', ...Object.keys(r.sectors).map(s => 'S:' + s), ...rows.map(x => x.symbol)];
    const parents = ['', ...Object.keys(r.sectors).map(() => 'All'), ...rows.map(x => 'S:' + x.sector)];
    const labels = ['S&P 100', ...Object.keys(r.sectors), ...rows.map(x => `<b>${x.symbol}</b><br>${x.value > 0 ? '+' : ''}${x.value.toFixed(st.metric === 'chg_1d' ? 2 : 1)}%`)];
    const values = [0, ...Object.keys(r.sectors).map(() => 0), ...rows.map(x => Math.max(x.mcap_b || 50, 5))];
    const colors = [0, ...Object.values(r.sectors), ...rows.map(x => x.value)];
    p.body.innerHTML = `<div class="toolbar">${[['chg_1d', '1 DAY'], ['ret_1m', '1 MONTH'], ['ret_ytd', 'YTD'], ['mom_12_1', '12-1 MOM']].map(([k, l]) => `<button data-m="${k}" class="${k === st.metric ? 'on' : ''}">${l}</button>`).join('')}
      <span class="sep"></span><span>Breadth <b class="pos">${b.up}▲</b> <b class="neg">${b.down}▼</b> · <b>${fmt(b.above_200, 0)}%</b> above 200d SMA</span>
      <span class="treemap-legend" style="margin-left:auto">tile size = market cap · click a stock for its overview</span></div><div id="${id}" class="chart fill"></div>`;
    plot(id, [{ type: 'treemap', ids, labels, parents, values, branchvalues: 'remainder', textinfo: 'label', hovertemplate: '%{label}<extra></extra>',
      marker: { colors, colorscale: [[0, '#b71c1c'], [0.5, '#1b1b1b'], [1, '#1b8a4c']], cmin: -lim, cmax: lim, cmid: 0, line: { color: '#000', width: 1 } },
      pathbar: { visible: false }, tiling: { pad: 1 }, textfont: { family: 'IBM Plex Mono, monospace', color: '#fff' } }], { margin: { l: 2, r: 2, t: 2, b: 2 }, showlegend: false });
    $(`#${id}`).on('plotly_click', ev => { const s = ev.points[0].id; if (s && !s.includes(':') && s !== 'All') goto(s + ' DES'); return false; });
    $$('[data-m]', p.body).forEach(x => x.onclick = () => { st.metric = x.dataset.m; draw().catch(er => p.error(er)); });
  };
  await draw(); p.every(120, draw);
});

// ================================================================== LIVE — intraday chart + streaming quote
reg('LIVE', 'Markets', 'Live intraday chart with streaming last price & VWAP', async (p, sym, args) => {
  const st = p.st; st.iv ||= (args[0] || '5m').toLowerCase();
  const id = uid();
  const draw = async () => {
    const r = await api(`/api/intraday/${encodeURIComponent(sym)}?interval=${st.iv}&days=${st.iv === '1m' ? 1 : st.iv === '15m' || st.iv === '30m' ? 5 : 1}`); p.setSource(r.source);
    p.setTitle(`${r.symbol} LIVE`, `Intraday ${st.iv}`);
    if (!$('#' + id)) {
      p.body.innerHTML = `<div class="toolbar">${['1m', '5m', '15m', '30m'].map(x => `<button data-iv="${x}" class="${x === st.iv ? 'on' : ''}">${x}</button>`).join('')}<span class="sep"></span>
        <span class="livepx" data-lp="${esc(r.symbol)}" data-v="${r.last}">${fmtPx(r.last)}</span> <span class="lpct"></span>
        <span class="dim" style="margin-left:auto">chart: session ${esc(r.times[r.times.length - 1].slice(0, 10))} vs prev close ${fmtPx(r.prev_close)} · big number: latest quote vs last close, streamed every 5s · Yahoo's free feed can lag 15 min</span>
        <button data-alert>+ ALERT</button></div><div id="${id}" class="chart fill"></div>`;
      $$('[data-iv]', p.body).forEach(b => b.onclick = () => { st.iv = b.dataset.iv; p.body.innerHTML = ''; draw().catch(er => p.error(er)); });
      $('[data-alert]', p.body).onclick = () => goto(`ALRT ${r.symbol}`);
    }
    $('.lpct', p.body).innerHTML = signed(r.pct, 2, '%');
    plot(id, [{ x: r.times, open: r.open, high: r.high, low: r.low, close: r.close, type: 'candlestick', name: r.symbol, increasing: { line: { color: '#2bd47d' } }, decreasing: { line: { color: '#ff5252' } } },
      { x: r.times, y: r.vwap, name: 'VWAP', line: { color: '#4da3ff', width: 1 } },
      { x: [r.times[0], r.times[r.times.length - 1]], y: [r.prev_close, r.prev_close], name: 'Prev close', line: { color: '#777', dash: 'dot', width: 1 } },
      { x: r.times, y: r.volume, type: 'bar', yaxis: 'y2', name: 'Volume', marker: { color: 'rgba(251,139,30,.35)' }, showlegend: false }],
      { xaxis: { rangeslider: { visible: false }, type: 'category', nticks: 8 }, yaxis: { domain: [0.25, 1], side: 'right' }, yaxis2: { domain: [0, 0.2], side: 'right', showgrid: false, tickformat: '.2s' }, margin: { t: 10 } });
  };
  await draw(); p.every(60, draw);
  liveStream(p, [sym], (qs, src) => {
    flashPrices(p.body, qs); const q = qs[0]; if (q) { const el = $('.lpct', p.body); if (el) el.innerHTML = signed(q.pct, 2, '%'); }
    if (src === 'demo') p.setSource('demo');
  }, 5);
}, { sym: 1, defaultSym: 'SPY' });

// ================================================================== ALRT — alerts manager
reg('ALRT', 'Markets', 'Price / move / volatility alerts with desktop notifications', async (p, _s, args) => {
  const sym0 = args[0] || '';
  const load = async () => {
    const r = await api('/api/alerts');
    p.body.innerHTML = `<form class="form"><label>Symbol<input name="symbol" value="${esc(sym0)}" required></label>
      <label>Condition<select name="kind">${opts(Object.entries(r.kinds), 'above')}</select></label><label>Level<input name="level" type="number" step="any" required></label>
      <label>Note<input name="note" placeholder="optional"></label><label>Repeat<select name="repeat">${opts([['false', 'Once'], ['true', 'Every check']], 'false')}</select></label>
      <div class="actions"><button class="primary">ADD ALERT</button><button type="button" data-perm>ENABLE DESKTOP NOTIFICATIONS</button>
        <span class="dim">Checked every 20s while the terminal is open. Examples: NVDA above 150 · SPY move 2 (%) · ^VIX above 25 · SPY vol 25 (10d realized %).</span></div></form>
      <h3>Alerts</h3>${r.alerts.length ? table([{ h: 'ID', k: 'id' }, { h: 'Symbol', l: 1, f: a => `<b class="amb">${esc(a.symbol)}</b>` }, { h: 'Condition', l: 1, f: a => esc(r.kinds[a.kind]) }, { h: 'Level', f: a => fmt(a.level) },
        { h: 'Status', f: a => a.active ? '<span class="yel">ACTIVE</span>' : '<span class="dim">DONE</span>' }, { h: 'Triggered', f: a => a.triggered ? `${a.triggered.replace('T', ' ')} @ ${fmt(a.trigger_value)}` : '—' },
        { h: 'Note', l: 1, f: a => esc(a.note || '') }, { h: '', f: a => `<button class="danger" data-del="${a.id}">✕</button>` }], r.alerts) : '<div class="dim">No alerts yet.</div>'}`;
    $('form', p.body).onsubmit = async e => {
      e.preventDefault(); const f = formData(e.target);
      try { await api('/api/alerts', { ...f, repeat: f.repeat === 'true' }); toast(`Alert added: ${esc(f.symbol.toUpperCase())} ${esc(f.kind)} ${esc(f.level)}`); load(); pollAlerts(); } catch (er) { toast(esc(er.message), 'err'); }
    };
    $('[data-perm]', p.body).onclick = async () => { if (!window.Notification) return toast('This browser has no notification support', 'warn'); toast('Notifications: ' + await Notification.requestPermission()); };
    $$('[data-del]', p.body).forEach(b => b.onclick = async () => { await fetch('/api/alerts/' + b.dataset.del, { method: 'DELETE' }); load(); pollAlerts(); });
  };
  await load(); p.every(30, load);
});

// ================================================================== EVT — event calendar
reg('EVT', 'Research', 'Event calendar: watchlist earnings + FOMC / CPI / payrolls', async (p, _s, args) => {
  const syms = args.length ? args : wl().filter(s => !/[=^]|-USD/.test(s));
  const r = await api(`/api/events?symbols=${encodeURIComponent(syms.join(','))}&days=60`); p.setSource(r.source === 'static' ? null : r.source);
  const today = new Date().toISOString().slice(0, 10);
  const days = d => Math.round((new Date(d) - new Date(today)) / 864e5);
  p.body.innerHTML = `<div class="toolbar"><span class="dim">Earnings for: ${syms.map(s => `<span class="code">${esc(s)}</span>`).join(' ')} · change with <span class="code">EVT AAPL MSFT …</span> or edit your watchlist (W)</span></div>` +
    table([{ h: 'Date', l: 1, f: e => `<b>${e.date}</b>` }, { h: 'In', f: e => days(e.date) + 'd' }, { h: 'Event', l: 1, f: e => e.kind === 'earnings' ? `<b class="amb">${esc(e.event)}</b>` : e.kind === 'central bank' ? `<b class="yel">${esc(e.event)}</b>` : esc(e.event) },
      { h: 'Type', l: 1, f: e => `<span class="dim">${e.kind}</span>` }, { h: 'EPS est.', f: e => fmt(e.eps_est) }, { h: 'Date quality', l: 1, f: e => e.estimated ? '<span class="dim">estimated</span>' : 'scheduled' },
      { h: '', f: e => e.symbol ? `<button data-ern="${esc(e.symbol)}">IMPLIED MOVE</button>` : '' }], r.events) +
    `<p class="dim">FOMC dates are the Fed's published schedule. CPI / PCE / GDP dates are rule-based estimates; check the BLS/BEA calendars before trading around them.</p>`;
  $$('[data-ern]', p.body).forEach(b => b.onclick = () => goto(`${b.dataset.ern} ERN`));
});

// ================================================================== ERN — earnings & implied move
reg('ERN', 'Research', 'Earnings: next date, options-implied move vs historical reactions', async (p, sym) => {
  const r = await api('/api/earnings/' + encodeURIComponent(sym)); p.setSource(r.source);
  const im = r.implied || {}, id = uid();
  const ratio = isNum(im.move_pct) && isNum(r.avg_abs_move) ? im.move_pct / r.avg_abs_move : null;
  p.body.innerHTML = `<div class="cards">${card('Next earnings', r.next ? r.next.date : '—')}${card('EPS estimate', fmt(r.next && r.next.eps_est))}${card('Implied move', isNum(im.move_pct) ? '±' + fmt(im.move_pct) + '%' : '—')}
    ${card('Avg past move', isNum(r.avg_abs_move) ? '±' + fmt(r.avg_abs_move) + '%' : '—')}${card('Max past move', isNum(r.max_abs_move) ? '±' + fmt(r.max_abs_move) + '%' : '—')}${card('Implied / hist.', ratio ? fmt(ratio) + 'x' : '—', ratio > 1.2 ? 'neg' : ratio && ratio < 0.8 ? 'pos' : '')}
    ${card('EPS beat rate', isNum(r.beat_rate) ? fmt(r.beat_rate, 0) + '%' : '—')}${card('Straddle expiry', im.expiry || '—')}</div>
    ${im.error ? `<div class="banner">Implied move unavailable: ${esc(im.error)}</div>` : ''}
    ${ratio ? `<div class="warnbox">Options price a ±${fmt(im.move_pct)}% move through ${im.expiry} vs an average ±${fmt(r.avg_abs_move)}% reaction over the last ${r.history.length} reports: the straddle looks <b>${ratio > 1.2 ? 'expensive' : ratio < 0.8 ? 'cheap' : 'fairly priced'}</b> against history. The straddle also includes non-earnings days to expiry, and past reactions are a small sample.</div>` : ''}
    <div id="${id}" class="chart short"></div>
    <h3>Past reports (close before → close after)</h3>${table([{ h: 'Date', k: 'date' }, { h: 'EPS est.', f: x => fmt(x.eps_est) }, { h: 'EPS actual', f: x => fmt(x.eps_act) }, { h: 'Surprise', f: x => signed(x.surprise_pct, 1, '%') }, { h: 'Price move', f: x => signed(x.move_pct, 2, '%') }], [...r.history].reverse())}`;
  plot(id, [{ x: r.history.map(x => x.date), y: r.history.map(x => x.move_pct), type: 'bar', marker: { color: r.history.map(x => x.move_pct >= 0 ? '#2bd47d' : '#ff5252') }, name: 'Move' }],
    { title: { text: `${r.symbol} earnings reactions vs implied ±move` }, showlegend: false, yaxis: { ticksuffix: '%' },
      shapes: isNum(im.move_pct) ? [im.move_pct, -im.move_pct].map(y => ({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, line: { color: '#ffd23f', dash: 'dot' } })) : [] });
}, { sym: 1, defaultSym: 'AAPL' });

// ================================================================== FF — factor model
reg('FF', 'Research', 'Factor exposure (Fama-French style ETF proxies) for a stock or portfolio', async (p, sym, args) => {
  const syms = [sym, ...args.filter(a => !/^[\d.]+$/.test(a))], w = args.filter(a => /^[\d.]+$/.test(a));
  const r = await api(`/api/factors?symbols=${encodeURIComponent(syms.join(','))}&weights=${w.join(',')}&period=3y`); p.setSource(r.source);
  const a = uid(), b = uid(), c = uid(), d = uid();
  p.setTitle(`${syms.join(' ')} FF`, r.label);
  p.body.innerHTML = `<div class="cards">${card('R²', fmt(r.r2, 3))}${card('Alpha (ann.)', pct(r.alpha_ann), cls(r.alpha_ann))}${card('Alpha t-stat', fmt(r.alpha_t), Math.abs(r.alpha_t) > 2 ? 'yel' : '')}${card('Residual vol', pct(r.resid_vol))}
    ${card('Market β', fmt(r.loadings.find(l => l.factor === 'MKT')?.beta))}${card('Idiosyncratic var.', pct(r.variance_share.Idiosyncratic, 0))}${card('Obs (days)', r.n)}</div>
    <div class="row"><div id="${a}" class="chart"></div><div id="${b}" class="chart"></div></div><div class="row"><div id="${c}" class="chart"></div><div id="${d}" class="chart"></div></div>
    <h3>Loadings</h3>${table([{ h: 'Factor', l: 1, f: x => `<b>${x.factor}</b> <span class="dim">${esc(x.desc)}</span>` }, { h: 'Beta', f: x => fmt(x.beta, 3) }, { h: 't-stat', f: x => `<span class="${Math.abs(x.t) > 2 ? 'yel' : 'dim'}">${fmt(x.t)}</span>` }, { h: 'p-value', f: x => fmt(x.p, 4) },
      { h: 'Return contribution', f: x => signed(r.total_contrib[x.factor], 1, ' pp') }], r.loadings)}
    <p class="dim">Factors are long-only ETF spreads (e.g. value = IWD − IWF), not the academic Fama-French portfolios, and they overlap. |t| &gt; 2 ≈ significant. Portfolio: <span class="code">AAPL FF MSFT TLT 0.5 0.3 0.2</span>.</p>`;
  plot(a, [{ x: r.loadings.map(l => l.factor), y: r.loadings.map(l => l.beta), type: 'bar', marker: { color: r.loadings.map(l => Math.abs(l.t) > 2 ? '#fb8b1e' : '#555') },
    error_y: { type: 'data', array: r.loadings.map(l => Math.abs(l.beta / l.t) * 1.96), color: '#aaa' }, name: 'beta' }], { title: { text: 'Factor betas (±95% CI; amber = significant)' }, showlegend: false });
  const vs = Object.entries(r.variance_share).filter(([, v]) => isNum(v));
  plot(b, [{ labels: vs.map(x => x[0]), values: vs.map(x => Math.max(x[1], 0)), type: 'pie', hole: .5, marker: { colors: COLORS }, textinfo: 'label+percent' }], { title: { text: 'Where the variance comes from' }, showlegend: false });
  plot(c, Object.entries(r.attribution.series).map(([k, v], i) => ({ x: r.attribution.dates, y: v, name: k, line: { color: k === 'Residual' ? '#777' : k === 'Alpha' ? '#ffd23f' : COLORS[i % COLORS.length], width: 1.2 } })),
    { title: { text: 'Cumulative return attribution (% points)' } });
  if (r.rolling) plot(d, Object.entries(r.rolling.series).map(([k, v], i) => ({ x: r.rolling.dates, y: v, name: k, line: { color: COLORS[i % COLORS.length], width: 1.2 } })), { title: { text: `Rolling ${r.window}d betas` } });
}, { sym: 1, defaultSym: 'SPY' });

// ================================================================== WF — walk-forward
reg('WF', 'Quant Lab', 'Walk-forward backtest: fit on past, trade the next period, out-of-sample', async (p, _s, args) => {
  const strats = await api('/api/strategies'); delete strats._functions; delete strats.buy_hold;
  const s0 = (args[1] || 'sma_cross').toLowerCase();
  p.body.innerHTML = `<form class="form">
    <label>Symbol<input name="symbol" value="${esc(args[0] || 'SPY')}"></label><label>Strategy<select name="strategy">${opts(Object.entries(strats).map(([k, v]) => [k, v.label]), strats[s0] ? s0 : 'sma_cross')}</select></label>
    <label>History<select name="period">${opts(['5y', '10y', '20y', 'max'], '10y')}</select></label><label>Train window (days)<input name="train_days" type="number" value="756"></label>
    <label>Test window (days)<input name="test_days" type="number" value="126"></label><label>Window<select name="anchored">${opts([['false', 'Rolling'], ['true', 'Anchored (expanding)']], 'false')}</select></label>
    <label>Optimise for<select name="metric">${opts(['Sharpe', 'CAGR', 'Calmar', 'Sortino'], 'Sharpe')}</select></label><label>Cost (bps)<input name="cost_bps" type="number" step="any" value="5"></label>
    <div class="actions"><button class="primary">RUN WALK-FORWARD</button><span class="dim">Each fold picks the best parameters on the train window only, then trades the following test window with them.</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body);
  const run = async () => {
    out.innerHTML = '<div class="loading">FITTING FOLDS…</div>';
    const f = formData(form);
    const r = await api('/api/walkforward', { ...f, train_days: +f.train_days, test_days: +f.test_days, cost_bps: +f.cost_bps, anchored: f.anchored === 'true' });
    p.setSource(r.source); p.setTitle(`WF ${r.symbol}`, `${strats[r.strategy].label} walk-forward`);
    const s = r.stats, is = r.in_sample_stats, a = uid(), b = uid();
    const eff = r.wf_efficiency;
    out.innerHTML = `<div class="cards">${card('OOS Sharpe', fmt(s.Sharpe), cls(s.Sharpe))}${card('OOS CAGR', pct(s.CAGR), cls(s.CAGR))}${card('OOS Max DD', pct(s['Max Drawdown']), 'neg')}
      ${card('In-sample Sharpe', fmt(is.Sharpe))}${card('Avg fold IS Sharpe', fmt(r.avg_is_sharpe))}${card('WF efficiency', eff === null ? '—' : pct(eff, 0), eff > 0.5 ? 'pos' : 'neg')}
      ${card('Buy & hold Sharpe', fmt(r.bench_stats.Sharpe))}${card('Folds', r.n_folds)}${card('Distinct param sets', r.distinct_param_sets)}</div>
      <div class="${eff !== null && eff > 0.5 && s.Sharpe > r.bench_stats.Sharpe ? 'warnbox' : 'banner'}">${eff === null ? 'In-sample Sharpe ≤ 0: the optimiser found nothing even with hindsight.' :
        `Out-of-sample kept ${pct(eff, 0)} of the in-sample Sharpe. ${eff < 0.5 ? 'Most of the optimised edge disappears on unseen data: likely overfit.' : 'The edge partly survives on unseen data.'}`}
        ${r.distinct_param_sets > r.n_folds / 2 ? ' Parameters jump between folds: the "best" setting is unstable.' : ''} ${s.Sharpe < r.bench_stats.Sharpe ? 'It still trails buy & hold on a risk-adjusted basis.' : ''}</div>
      <div id="${a}" class="chart tall"></div><div id="${b}" class="chart short"></div>
      <h3>Folds</h3>${table([{ h: 'Train', l: 1, f: x => `${x.train_start} → ${x.train_end}` }, { h: 'Test', l: 1, f: x => `${x.test_start} → ${x.test_end}` },
        { h: 'Chosen params', l: 1, f: x => `<span class="code">${esc(Object.entries(x.params).map(([k, v]) => k + '=' + v).join(' '))}</span>` },
        { h: 'IS ' + r.metric, f: x => fmt(x.is_metric) }, { h: 'OOS Sharpe', f: x => signed(x.oos_sharpe) }, { h: 'OOS return', f: x => signed(x.oos_return * 100, 1, '%') }], r.folds)}`;
    plot(a, [{ x: r.dates, y: r.equity, name: 'Walk-forward (out-of-sample)', line: { color: '#fb8b1e', width: 1.8 } },
      { x: r.dates, y: r.in_sample_equity, name: `Hindsight-optimised (${Object.entries(r.in_sample_params).map(([k, v]) => k + '=' + v).join(' ')})`, line: { color: '#4da3ff', dash: 'dot', width: 1.2 } },
      { x: r.dates, y: r.benchmark, name: 'Buy & hold', line: { color: '#777', width: 1 } }],
      { title: { text: `${r.symbol}: honest (orange) vs flattering (blue dotted) backtest` }, yaxis: { type: 'log', side: 'right' },
        shapes: r.folds.map(x => ({ type: 'line', x0: x.test_start, x1: x.test_start, yref: 'paper', y0: 0, y1: 1, line: { color: '#2a2a2a', width: 1 } })) });
    plot(b, [{ x: r.folds.map(x => x.test_start), y: r.folds.map(x => x.is_sharpe), type: 'bar', name: 'In-sample Sharpe', marker: { color: '#4da3ff' } },
      { x: r.folds.map(x => x.test_start), y: r.folds.map(x => x.oos_sharpe), type: 'bar', name: 'Out-of-sample Sharpe', marker: { color: '#fb8b1e' } }], { title: { text: 'Sharpe per fold: in-sample vs out-of-sample' }, barmode: 'group' });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(er => out.innerHTML = errBox(er)); };
  await run();
});

// ================================================================== MBT — multi-asset backtest
reg('MBT', 'Quant Lab', 'Multi-asset strategies: cross-sectional momentum, ETF rotation, long/short baskets', async (p, _s, args) => {
  const meta = await api('/api/mbt/strategies');
  const syms = args.length >= 2 ? args : ['SPY', 'EFA', 'EEM', 'TLT', 'IEF', 'GLD', 'DBC', 'VNQ'];
  p.body.innerHTML = `<form class="form">
    <label class="wide">Universe<input name="symbols" value="${esc(syms.join(' '))}"></label>
    <label>Strategy<select name="strategy">${opts(Object.keys(meta.strategies).map(k => [k, k.replace('_', ' ')]), 'dual_momentum')}</select></label>
    <label>Top N<input name="top_n" type="number" value="3"></label><label>Lookback (days)<input name="lookback" type="number" value="126"></label><label>Skip recent (days)<input name="skip" type="number" value="21"></label>
    <label>Rebalance<select name="rebalance">${opts(meta.rebalance, 'monthly')}</select></label><label>Period<select name="period">${opts(['5y', '10y', '20y', 'max'], '10y')}</select></label>
    <label>Cost (bps)<input name="cost_bps" type="number" step="any" value="5"></label><label>Benchmark<input name="bench" value="SPY"></label>
    <div class="actions"><button class="primary">RUN</button><span class="dim sdesc"></span></div>
    <div class="actions"><span class="dim">Universes:</span><button type="button" data-u="SPY EFA EEM TLT IEF GLD DBC VNQ">Global multi-asset</button><button type="button" data-u="XLK XLF XLV XLE XLI XLY XLP XLU XLB XLRE XLC">US sectors</button>
      <button type="button" data-u="AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM LLY XOM UNH">Mega caps</button><button type="button" data-u="SPY TLT GLD">Risk parity trio</button></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body), sel = $('[name=strategy]', form);
  const desc = () => $('.sdesc', form).textContent = meta.strategies[sel.value]; sel.onchange = desc; desc();
  $$('[data-u]', form).forEach(b => b.onclick = () => { $('[name=symbols]', form).value = b.dataset.u; });
  const run = async () => {
    out.innerHTML = '<div class="loading">REBALANCING…</div>';
    const f = formData(form);
    const r = await api('/api/mbt', { ...f, top_n: +f.top_n, lookback: +f.lookback, skip: +f.skip, cost_bps: +f.cost_bps });
    p.setSource(r.source); p.setTitle('MBT', r.description);
    const s = r.stats, bs = r.bench_stats, a = uid(), b = uid(), c = uid();
    out.innerHTML = `<div class="cards">${card('CAGR', pct(s.CAGR), cls(s.CAGR))}${card('Sharpe', fmt(s.Sharpe))}${card('Max DD', pct(s['Max Drawdown']), 'neg')}${card('Volatility', pct(s.Volatility))}
      ${card(r.bench_name + ' CAGR', pct(bs.CAGR))}${card(r.bench_name + ' Sharpe', fmt(bs.Sharpe))}${card('Turnover/yr', fmt(s['Turnover (ann.)'], 1) + 'x')}${card('Gross exp.', fmt(s['Avg Gross Exposure'], 2) + 'x')}</div>
      <div class="warnbox">Lookback, top-N and universe were chosen with hindsight; ETF universes have survivorship bias. Check robustness by varying the lookback and rebalance frequency.</div>
      <div id="${a}" class="chart tall"></div><div class="row"><div id="${b}" class="chart"></div><div id="${c}" class="chart"></div></div>
      <h3>Current target weights (as of last rebalance)</h3>${kv(Object.fromEntries(Object.entries(r.current).map(([k, v]) => [k, pct(v, 1)])))}
      <h3>Recent rebalances</h3>${table([{ h: 'Date', k: 'date' }, { h: 'Holdings', l: 1, f: x => Object.entries(x.weights).map(([k, v]) => `<span class="${v > 0 ? 'pos' : 'neg'}">${k} ${(v * 100).toFixed(0)}%</span>`).join(' ') || '<span class="dim">cash</span>' }], [...r.rebalances].reverse().slice(0, 24))}`;
    plot(a, [{ x: r.dates, y: r.equity, name: 'Strategy', line: { color: '#fb8b1e', width: 1.6 } }, { x: r.dates, y: r.benchmark, name: r.bench_name, line: { color: '#777', width: 1 } },
      { x: r.dates, y: r.drawdown, name: 'Drawdown %', yaxis: 'y2', fill: 'tozeroy', line: { color: '#ff5252', width: .8 }, fillcolor: 'rgba(255,82,82,.25)' }],
      { title: { text: 'Equity (log) & drawdown' }, yaxis: { domain: [0.3, 1], type: 'log', side: 'right' }, yaxis2: { domain: [0, 0.25], side: 'right', ticksuffix: '%' } });
    plot(b, Object.entries(r.weights).map(([k, v], i) => ({ x: r.dates, y: v, name: k, stackgroup: 'one', line: { width: 0 }, fillcolor: COLORS[i % COLORS.length] })), { title: { text: 'Weights over time' } });
    const ct = Object.entries(r.contribution).sort((x, y) => y[1] - x[1]);
    plot(c, [{ x: ct.map(x => x[0]), y: ct.map(x => x[1] * 100), type: 'bar', marker: { color: ct.map(x => x[1] >= 0 ? '#2bd47d' : '#ff5252') } }], { title: { text: 'Return contribution by asset (sum of daily, % pts)' }, showlegend: false });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(er => out.innerHTML = errBox(er)); };
  await run();
});

// ================================================================== SKEW — skew & term structure signals
reg('SKEW', 'Derivatives', 'Option skew & term-structure signals: 25Δ risk reversal, butterfly, IV vs RV', async (p, sym) => {
  const r = await api('/api/skew/' + encodeURIComponent(sym)); p.setSource(r.source);
  const e = r.expiries, f = e[0] || {}, a = uid(), b = uid();
  p.body.innerHTML = `<div class="cards">${card('Front ATM IV', pct(f.atm_iv, 1))}${card('30d realized', pct(r.rv30, 1))}${card('IV − RV', isNum(r.vrp) ? signed(r.vrp * 100, 1, ' pts') : '—')}
    ${card('25Δ RR (front)', isNum(f.rr25) ? signed(f.rr25 * 100, 1, ' pts') : '—')}${card('25Δ fly (front)', isNum(f.bf25) ? fmt(f.bf25 * 100, 1) + ' pts' : '—')}${card('Term slope /30d', r.term.slope_per_30d !== undefined ? signed(r.term.slope_per_30d * 100, 2, ' pts') : '—')}</div>
    <div class="warnbox">${r.signals.map(esc).join('<br>')}</div>
    <div class="row"><div id="${a}" class="chart"></div><div id="${b}" class="chart"></div></div>
    ${table([{ h: 'Expiry', k: 'expiry' }, { h: 'Days', k: 'days' }, { h: 'ATM IV', f: x => pct(x.atm_iv, 1) }, { h: '25Δ put', f: x => pct(x.put25_iv, 1) }, { h: '25Δ call', f: x => pct(x.call25_iv, 1) }, { h: '10Δ put', f: x => pct(x.put10_iv, 1) },
      { h: 'RR25', f: x => isNum(x.rr25) ? signed(x.rr25 * 100, 1) : '—' }, { h: 'Fly25', f: x => isNum(x.bf25) ? fmt(x.bf25 * 100, 1) : '—' }, { h: 'Implied move', f: x => isNum(x.implied_move_pct) ? '±' + fmt(x.implied_move_pct) + '%' : '—' }, { h: 'P/C OI', f: x => fmt(x.pc_oi) }], e)}
    <p class="dim">RR25 = IV(25Δ call) − IV(25Δ put); negative means puts are richer (normal for equities, extreme values signal hedging demand). Fly25 measures tail (kurtosis) pricing. Next: <span class="code">${esc(r.symbol)} OSCAN</span> to rank structures.</p>`;
  plot(a, [{ x: e.map(x => x.days), y: e.map(x => x.atm_iv * 100), name: 'ATM', mode: 'lines+markers', line: { color: '#fb8b1e' } }, { x: e.map(x => x.days), y: e.map(x => x.put25_iv * 100), name: '25Δ put', line: { color: '#ff5252', dash: 'dot' } },
    { x: e.map(x => x.days), y: e.map(x => x.call25_iv * 100), name: '25Δ call', line: { color: '#2bd47d', dash: 'dot' } }, { x: [0, Math.max(...e.map(x => x.days))], y: [r.rv30 * 100, r.rv30 * 100], name: '30d realized', line: { color: '#777', dash: 'dash' } }],
    { title: { text: 'Implied vol term structure' }, xaxis: { title: { text: 'days' } }, yaxis: { ticksuffix: '%' } });
  plot(b, [{ x: e.map(x => x.days), y: e.map(x => isNum(x.rr25) ? x.rr25 * 100 : null), type: 'bar', name: 'RR25', marker: { color: '#4da3ff' } }, { x: e.map(x => x.days), y: e.map(x => isNum(x.bf25) ? x.bf25 * 100 : null), name: 'Fly25', line: { color: '#e056fd' } }],
    { title: { text: 'Skew by expiry (vol points)' }, xaxis: { title: { text: 'days' } } });
}, { sym: 1, defaultSym: 'SPY' });

// ================================================================== OSCAN — option strategy scanner
reg('OSCAN', 'Derivatives', 'Option strategy scanner: rank spreads/straddles/condors by EV under your vol view', async (p, sym, args) => {
  const st = p.st;
  const draw = async () => {
    const r = await api('/api/optscan', { symbol: sym, expiry: st.exp, vol: st.vol, width_pct: st.width || 5 }); p.setSource(r.source);
    st.exp = r.expiry;
    p.body.innerHTML = `<form class="form"><label>Expiry<select name="exp">${opts(r.expiries, r.expiry)}</select></label>
      <label>Your vol forecast % (blank = 21d realized ${fmt(r.rv * 100, 1)})<input name="vol" type="number" step="any" value="${st.vol || ''}"></label>
      <label>Wing width % of spot<input name="width" type="number" step="any" value="${st.width || 5}"></label>
      <div class="actions"><button class="primary">SCAN</button><span>Spot <b>${fmtPx(r.spot)}</b> · ${r.days}d · ATM IV <b>${pct(r.atm_iv, 1)}</b> vs forecast <b class="amb">${pct(r.forecast_vol, 1)}</b></span></div></form>
      <div class="banner">Model risk: expected value comes from a lognormal with YOUR vol forecast and no skew, priced at mid. Real fills are worse, and the forecast drives the ranking. POP(IV) is the market-implied probability.</div>
      ${r.strategies.length ? table([{ h: 'Structure', l: 1, f: x => `<b class="amb">${x.name}</b>` }, { h: 'Legs', l: 1, f: x => x.legs.map(l => `${l.qty > 0 ? '+' : ''}${l.qty} ${l.kind[0].toUpperCase()}${fmt(l.strike, l.strike % 1 ? 1 : 0)}`).join(' ') },
        { h: 'Credit(+) / debit(−)', f: x => signed(-x.net_premium * 100, 0) }, { h: 'Max profit', f: x => isNum(x.max_profit) ? fmt(x.max_profit * 100, 0) : '∞' }, { h: 'Max loss', f: x => isNum(x.max_loss) ? fmt(x.max_loss * 100, 0) : '<span class="neg">unlimited</span>' },
        { h: 'Breakevens', f: x => x.breakevens.map(v => fmt(v, 1)).join(' / ') }, { h: 'POP (your vol)', f: x => pct(x.pop, 0) }, { h: 'POP (IV)', f: x => pct(x.pop_iv, 0) },
        { h: 'EV (your vol)', f: x => signed(x.ev * 100, 0) }, { h: 'EV / risk', f: x => isNum(x.ev_per_risk) ? signed(x.ev_per_risk * 100, 1, '%') : '—' },
        { h: 'Δ', f: x => fmt(x.delta, 2) }, { h: 'Vega', f: x => fmt(x.vega * 100, 0) }, { h: 'Θ/day', f: x => signed(x.theta * 100, 0) }], r.strategies, { click: 1 }) : '<div class="dim">Chain too sparse to build structures for this expiry.</div>'}
      <p class="dim">Dollar figures are per 1 contract (×100). Click a row to open it in the OVME pricer. Ranking is by EV per dollar of defined risk; undefined-risk structures sort last.</p>`;
    const form = $('form', p.body);
    form.onsubmit = e => { e.preventDefault(); const f = formData(form); st.exp = f.exp; st.vol = f.vol; st.width = f.width; draw().catch(er => p.error(er)); };
    $$('tr.click', p.body).forEach(tr => tr.onclick = () => goto(`OVME ${r.symbol}`));
  };
  await draw();
}, { sym: 1, defaultSym: 'SPY' });

// ================================================================== NOTE — research notes
reg('NOTE', 'Research', 'Research notes: jot ideas next to charts (saved locally)', async (p, _s, args) => {
  const st = p.st; st.sym ??= args[0] || '';
  const load = async () => {
    const r = await api('/api/notes' + (st.filterSym ? '?symbol=' + encodeURIComponent(st.filterSym) : ''));
    const cur = r.notes.find(n => n.id === st.id) || null;
    p.body.innerHTML = `<div class="row" style="height:100%"><div style="flex:0 0 220px" class="notelist"><div class="toolbar"><button data-new class="primary">+ NEW</button>
        <input data-fs placeholder="filter ticker" value="${esc(st.filterSym || '')}" style="width:100px"></div>
        ${r.notes.map(n => `<div data-id="${n.id}" class="${n.id === st.id ? 'on' : ''}"><b class="amb">${esc(n.symbol || '—')}</b> ${esc(n.title || n.body.slice(0, 30))}<br><span class="dim">${n.updated.replace('T', ' ').slice(0, 16)}</span></div>`).join('') || '<div class="dim">No notes</div>'}</div>
      <div class="notes" style="flex:1"><div class="toolbar"><input data-t placeholder="Title" value="${esc(cur ? cur.title : '')}" style="flex:1"><input data-s placeholder="Ticker" value="${esc(cur ? cur.symbol : st.sym)}" style="width:90px">
        <button data-save class="primary">SAVE</button>${cur ? `<button data-open>OPEN ${esc(cur.symbol)}</button><button data-del class="danger">DELETE</button>` : ''}<span class="dim saved"></span></div>
        <textarea data-b placeholder="Thesis, levels, risks, what would change your mind…  (auto-saves)">${esc(cur ? cur.body : '')}</textarea></div></div>`;
    const save = async () => {
      const body = $('[data-b]', p.body).value; if (!body.trim() && !st.id) return;
      const n = (await api('/api/notes', { id: st.id, body, title: $('[data-t]', p.body).value, symbol: $('[data-s]', p.body).value })).note;
      const first = !st.id; st.id = n.id; $('.saved', p.body).textContent = 'saved ' + n.updated.slice(11, 19); if (first) load();
    };
    let tmr; $('[data-b]', p.body).oninput = () => { clearTimeout(tmr); tmr = setTimeout(() => save().catch(() => { }), 1200); };
    p.onDispose(() => clearTimeout(tmr));
    $('[data-save]', p.body).onclick = () => save().then(load);
    $('[data-new]', p.body).onclick = () => { st.id = null; load(); };
    $('[data-fs]', p.body).onchange = e => { st.filterSym = e.target.value.toUpperCase(); load(); };
    $$('[data-id]', p.body).forEach(d => d.onclick = () => { st.id = +d.dataset.id; load(); });
    if (cur) {
      $('[data-del]', p.body).onclick = async () => { if (confirm('Delete this note?')) { await fetch('/api/notes/' + cur.id, { method: 'DELETE' }); st.id = null; load(); } };
      $('[data-open]', p.body).onclick = () => cur.symbol && runIn(`${cur.symbol} GP`, (p.idx + 1) % state.layout);
    }
  };
  await load();
});

// ================================================================== WS — workspaces
reg('WS', 'System', 'Saved workspaces: name and reload whole panel layouts', async (p, _s, args) => {
  if (args.length) await saveWorkspace(args.join(' '));
  const load = async () => {
    await refreshWorkspaces(); const ws = state.workspaces || [];
    p.body.innerHTML = `<div class="toolbar"><input data-n placeholder="name, e.g. Morning" style="width:200px"><button data-save class="primary">SAVE CURRENT LAYOUT</button>
      <span class="dim">Also: the WORKSPACE menu in the header, or <span class="code">WS Options desk</span></span></div>` +
      (ws.length ? table([{ h: 'Name', l: 1, f: w => `<b class="amb">${esc(w.name)}</b>` }, { h: 'Layout', f: w => w.layout + ' panels' }, { h: 'Panels', l: 1, f: w => w.panels.slice(0, w.layout).map(c => `<span class="code">${esc(c)}</span>`).join(' ') },
        { h: 'Saved', f: w => w.updated.replace('T', ' ').slice(0, 16) }, { h: '', f: w => `<button data-load="${esc(w.name)}">LOAD</button><button class="danger" data-del="${esc(w.name)}">✕</button>` }], ws)
        : '<div class="dim">No saved workspaces yet.</div>');
    $('[data-save]', p.body).onclick = async () => { const n = $('[data-n]', p.body).value.trim(); if (n) { await saveWorkspace(n); load(); } };
    $$('[data-load]', p.body).forEach(b => b.onclick = () => loadWorkspace(b.dataset.load));
    $$('[data-del]', p.body).forEach(b => b.onclick = async () => { await fetch('/api/workspaces/' + encodeURIComponent(b.dataset.del), { method: 'DELETE' }); load(); });
  };
  await load();
});

// ================================================================== ASK — AI research assistant
reg('ASK', 'Research', 'AI research assistant: ask a market question, get an answer + the right panels', async p => {
  const q = (p.raw || '').replace(/^\s*(\?|ask\b)\s*/i, '');
  const st = p.st;
  const render = (r, question) => {
    p.setSource(r.demo ? 'demo' : r.mode === 'ai' ? 'claude' : null);
    p.body.innerHTML = `<form class="form" style="grid-template-columns:1fr auto"><label>Question<input name="q" value="${esc(question)}" placeholder="e.g. why is SPY vol rising?  ·  is NVDA mostly a momentum bet?  ·  what is the curve saying?"></label>
        <div class="actions" style="align-items:flex-end"><button class="primary">ASK</button></div></form>
      ${r.note ? `<div class="warnbox">${esc(r.note)}</div>` : ''}
      ${r.answer ? `<div class="answer">${esc(r.answer)}</div>` : ''}
      ${r.commands && r.commands.length ? `<div class="toolbar"><span class="dim">Open:</span>${r.commands.map(c => `<button data-c="${esc(c)}">${esc(c)}</button>`).join('')}<button data-all class="primary">OPEN ALL IN OTHER PANELS</button></div>` : ''}
      ${r.tools && r.tools.length ? `<details><summary class="dim">Data the assistant looked at (${r.tools.length} calls)</summary>${table([{ h: 'Tool', l: 1, k: 'tool' }, { h: 'Input', l: 1, f: t => `<span class="dim">${esc(JSON.stringify(t.input))}</span>` }, { h: '', l: 1, f: t => t.error ? `<span class="neg">${esc(t.error)}</span>` : '' }], r.tools)}</details>` : ''}
      <p class="dim">${r.mode === 'ai' ? `Answer by ${esc(r.model || 'Claude')} from terminal data. It can be wrong; check the numbers in the panels. Your question and the market data it fetched were sent to Anthropic.` : ''} Not investment advice.</p>`;
    $('form', p.body).onsubmit = e => { e.preventDefault(); ask($('[name=q]', p.body).value); };
    $$('[data-c]', p.body).forEach(b => b.onclick = () => runIn(b.dataset.c, (p.idx + 1) % state.layout));
    const all = $('[data-all]', p.body);
    if (all) all.onclick = () => r.commands.forEach((c, i) => { const j = (p.idx + 1 + i) % state.layout; if (j !== p.idx) runIn(c, j); });
  };
  const ask = async question => {
    if (!question.trim()) { render({ mode: '', answer: '', commands: [] }, ''); return; }
    p.body.innerHTML = '<div class="loading">THINKING… (pulling data, this can take 20-60s)</div>';
    try {
      const r = await api('/api/ask', { question, context: { panels: state.panels.slice(0, state.layout).map(x => x.cmd), watchlist: wl() } });
      st.last = r; render(r, question);
    } catch (e) { p.error(e); }
  };
  await ask(q);
});

// ================================================================== BRIEF — morning briefing
reg('BRIEF', 'Research', 'Morning briefing: overnight moves, watchlist, events, portfolio risk', async p => {
  const load = async () => {
    const list = wl();
    const r = await api('/api/brief?watch=' + encodeURIComponent(list.join(','))); p.setSource(r.source);
    const row = q => `<tr data-s="${esc(q.symbol)}" class="click"><td class="l"><b>${esc(q.name || q.symbol)}</b></td><td>${fmtPx(q.last)}</td><td>${signed(q.pct, 2, '%')}</td><td>${spark(q.spark, 60, 14)}</td></tr>`;
    const tbl = qs => `<table class="t"><tbody>${qs.map(row).join('')}</tbody></table>`;
    const pf = r.portfolio || {}, rk = pf.risk;
    const vix = r.vix, cv = r.curve;
    const today = new Date().toISOString().slice(0, 10);
    p.body.innerHTML = `<div class="cards">${vix ? card('VIX', `${fmt(vix.last)} <span class="dim" style="font-size:11px">${fmt(vix.pctile_1y, 0)}th pct 1Y</span>`, vix.last > 25 ? 'neg' : '') : ''}${vix ? card('VIX 5d Δ', signed(vix.chg_5d)) : ''}
        ${cv ? card('UST 10Y', fmt(cv['10Y']) + '%') : ''}${cv ? card('2s10s', fmt(cv['2s10s_bp'], 0) + 'bp', cls(cv['2s10s_bp'])) : ''}
        ${pf.equity ? card(`Portfolio (${esc(pf.broker)})`, '$' + big(pf.equity)) : ''}${pf.equity ? card('P&L', signed(pf.pnl_pct, 2, '%')) : ''}${rk ? card('1d VaR 95%', '$' + big(rk.var95_1d), 'neg') : ''}</div>
      <div class="brief">
        <section><h3>Biggest moves</h3>${tbl(r.biggest)}</section>
        <section><h3>Your watchlist</h3>${r.watch ? tbl(r.watch) : '<div class="dim">Empty: add with W</div>'}</section>
        <section><h3>Equity indices</h3>${tbl(r.WEI)}</section>
        <section><h3>FX · Commodities · Crypto</h3>${tbl([...r.FX.slice(0, 5), ...r.CMDT.slice(0, 5), ...r.CRYPTO])}</section>
        <section><h3>Next 7 days</h3>${r.events.length ? table([{ h: 'Date', l: 1, f: e => e.date === today ? '<b class="yel">TODAY</b>' : e.date }, { h: 'Event', l: 1, f: e => e.kind === 'earnings' ? `<b class="amb">${esc(e.event)}</b>` : esc(e.event) + (e.estimated ? ' <span class="dim">(est.)</span>' : '') }], r.events) : '<div class="dim">Nothing scheduled</div>'}</section>
        <section><h3>Portfolio risk</h3>${pf.error ? `<div class="err">${esc(pf.error)}</div>` : pf.top && pf.top.length ? `${kv({ 'Gross exposure': fmt(pf.gross, 0) + '%', 'Net exposure': fmt(pf.net, 0) + '%', 'VaR 99% 1d': rk ? '$' + big(rk.var99_1d) : '—', 'Worst day (1Y hist.)': rk ? '$' + big(rk.worst_day) : '—' })}
          ${table([{ h: 'Position', l: 1, f: x => `<b class="amb">${esc(x.symbol)}</b>` }, { h: 'Value', f: x => big(x.mkt_value) }, { h: 'Weight', f: x => fmt(x.weight, 1) + '%' }, { h: 'P&L', f: x => signed(x.unrealized_pct, 1, '%') }], pf.top)}` : '<div class="dim">No open positions. Trade with TRADE.</div>'}</section>
        <section><h3>Triggered alerts</h3>${r.alerts.length ? table([{ h: 'When', l: 1, f: a => a.triggered.replace('T', ' ').slice(5, 16) }, { h: 'Alert', l: 1, f: a => `<b class="amb">${esc(a.symbol)}</b> ${a.kind} ${fmt(a.level)}` }, { h: 'Value', f: a => fmt(a.trigger_value) }], r.alerts) : '<div class="dim">None. Set some with ALRT.</div>'}</section>
      </div><p class="dim">As of ${r.asof.replace('T', ' ')} · click any row for a chart · VaR is historical simulation on 1Y of daily returns for current positions.</p>`;
    $$('tr[data-s]', p.body).forEach(tr => tr.onclick = () => goto(tr.dataset.s + ' GP'));
  };
  await load(); p.every(120, load);
});
