/* QuantTerm analytics / derivatives / Monte Carlo / portfolio / trading / macro functions. */
'use strict';

const symList = (sym, args) => [sym, ...args].filter(Boolean).join(',');
const pctAxis = { ticksuffix: '%' };

// ================================================================== G (compare)
reg('G', 'Analytics', 'Compare securities: rebased performance + stats', async (p, _s, args) => {
  const st = p.st; st.syms ||= args.filter(a => !PERIODS.includes(a.toLowerCase())); if (!st.syms.length) st.syms = ['SPY', 'QQQ', 'TLT', 'GLD'];
  st.period ||= (args.find(a => PERIODS.includes(a.toLowerCase())) || '1y').toLowerCase();
  const draw = async () => {
    const r = await api(`/api/compare?symbols=${encodeURIComponent(st.syms.join(','))}&period=${st.period}`); p.setSource(r.source);
    const id = uid(), syms = Object.keys(r.series);
    p.body.innerHTML = `<div class="toolbar">${PERIODS.map(x => `<button data-p="${x}" class="${x === st.period ? 'on' : ''}">${x.toUpperCase()}</button>`).join('')}</div>
      <div id="${id}" class="chart tall"></div><h3>Statistics (beta/corr vs ${esc(syms[0])})</h3>` +
      table([{ h: 'Symbol', l: 1, f: x => `<b class="amb">${x}</b>` }, ...['Total Return', 'CAGR', 'Volatility', 'Sharpe', 'Sortino', 'Max Drawdown', 'Beta', 'Correlation', 'Skew'].map(k => ({ h: k, f: x => PCT_KEYS.test(k) ? pct(r.stats[x][k]) : fmt(r.stats[x][k]) }))], syms);
    plot(id, syms.map((s, i) => ({ x: r.dates, y: r.series[s], name: s, type: 'scatter', mode: 'lines', line: { color: COLORS[i % COLORS.length], width: 1.5 } })),
      { title: { text: 'Rebased to 100' }, yaxis: { side: 'right' } });
    $$('[data-p]', p.body).forEach(b => b.onclick = () => { st.period = b.dataset.p; draw(); });
  };
  await draw();
});

// ================================================================== CORR
reg('CORR', 'Analytics', 'Correlation matrix + rolling correlations', async (p, _s, args) => {
  const syms = args.length >= 2 ? args : ['SPY', 'QQQ', 'TLT', 'GLD', 'BTC-USD', 'EURUSD=X'];
  const r = await api(`/api/corr?symbols=${encodeURIComponent(syms.join(','))}&period=2y&window=63`); p.setSource(r.source);
  const a = uid(), b = uid();
  p.body.innerHTML = `<div class="row"><div id="${a}" class="chart tall"></div><div id="${b}" class="chart tall"></div></div>`;
  plot(a, [{ z: r.matrix, x: r.symbols, y: r.symbols, type: 'heatmap', zmin: -1, zmax: 1, colorscale: heatColors(), text: r.matrix.map(rw => rw.map(v => fmt(v))), texttemplate: '%{text}', hovertemplate: '%{y} / %{x}: %{z:.2f}<extra></extra>' }],
    { title: { text: 'Daily-return correlation (2Y)' }, yaxis: { autorange: 'reversed' }, margin: { l: 80, b: 60 }, hovermode: 'closest' });
  plot(b, Object.entries(r.rolling).map(([k, v], i) => ({ x: r.dates, y: v, name: k, type: 'scatter', mode: 'lines', line: { width: 1.2, color: COLORS[i % COLORS.length] } })),
    { title: { text: `Rolling ${r.window}d correlation` }, yaxis: { range: [-1, 1] } });
});

// ================================================================== BETA
reg('BETA', 'Analytics', 'Regression vs benchmark: alpha, beta, R², rolling beta', async (p, sym, args) => {
  const bench = args[0] || '^GSPC';
  const r = await api(`/api/beta?symbol=${encodeURIComponent(sym)}&bench=${encodeURIComponent(bench)}&period=2y`); p.setSource(r.source);
  const g = r.regression, a = uid(), b = uid();
  const xs = [Math.min(...g.x), Math.max(...g.x)];
  p.body.innerHTML = `<div class="cards">${card('Beta', fmt(g.beta, 3))}${card('Alpha (ann.)', pct(g.alpha_ann), cls(g.alpha_ann))}${card('R²', fmt(g.r2, 3))}${card('Correlation', fmt(g.corr, 3))}
    ${card('t(beta)', fmt(g.t_beta))}${card('t(alpha)', fmt(g.t_alpha))}${card('Residual vol', fmt(g.resid_vol) + '%')}${card('Obs', g.n)}</div>
    <div class="row"><div id="${a}" class="chart"></div><div id="${b}" class="chart"></div></div><p class="dim">Benchmark: ${esc(r.bench)} — change with e.g. <span class="code">${esc(sym)} BETA QQQ</span></p>`;
  plot(a, [{ x: g.x.map(v => v * 100), y: g.y.map(v => v * 100), mode: 'markers', type: 'scattergl', marker: { size: 3, color: '#4da3ff', opacity: .6 }, name: 'daily returns' },
    { x: xs.map(v => v * 100), y: xs.map(v => (g.alpha_ann / 252 + g.beta * v) * 100), mode: 'lines', line: { color: '#fb8b1e' }, name: `y = ${fmt(g.beta, 2)}x` }],
    { title: { text: `${r.symbol} vs ${r.bench} daily returns` }, xaxis: { title: { text: r.bench + ' %' } }, yaxis: { title: { text: r.symbol + ' %' } }, hovermode: 'closest' });
  plot(b, [{ x: r.dates, y: r.rolling_beta, name: `Beta ${r.window}d`, type: 'scatter', line: { color: '#fb8b1e' } },
    { x: r.dates, y: r.rolling_corr, name: `Corr ${r.window}d`, type: 'scatter', line: { color: '#4da3ff' }, yaxis: 'y2' }],
    { title: { text: 'Rolling beta / correlation' }, yaxis2: { overlaying: 'y', side: 'right', range: [-1, 1], showgrid: false } });
}, { sym: 1 });

// ================================================================== VOL
reg('VOL', 'Analytics', 'Realized volatility, vol cone, return distribution, VaR', async (p, sym) => {
  const r = await api(`/api/vol/${encodeURIComponent(sym)}?period=2y`); p.setSource(r.source);
  const a = uid(), b = uid(), c = uid();
  p.body.innerHTML = `<div id="${a}" class="chart"></div><div class="row"><div id="${b}" class="chart"></div><div id="${c}" class="chart"></div></div>
    <h3>Value-at-Risk (1-day, per unit of exposure)</h3>` + table([{ h: 'Confidence', f: x => pct(x.level, 0) }, { h: 'Historical', f: x => pct(x.historical) }, { h: 'Hist. CVaR', f: x => pct(x.historical_cvar) },
    { h: 'Parametric', f: x => pct(x.parametric) }, { h: 'Param. CVaR', f: x => pct(x.parametric_cvar) }, { h: 'Cornish-Fisher', f: x => pct(x.cornish_fisher) }], r.var);
  plot(a, Object.entries(r.series).map(([k, v], i) => ({ x: r.dates, y: v, name: k, type: 'scatter', mode: 'lines', line: { width: 1.2, color: COLORS[i] } })), { title: { text: `${r.symbol} realized volatility (annualised %)` }, yaxis: pctAxis });
  const w = r.cone.map(x => x.window);
  plot(b, [
    { x: w, y: r.cone.map(x => x.max), name: 'max', line: { color: '#ff5252', dash: 'dot' } }, { x: w, y: r.cone.map(x => x.p90), name: '90%', line: { color: '#fb8b1e' } },
    { x: w, y: r.cone.map(x => x.p50), name: 'median', line: { color: '#bbb' } }, { x: w, y: r.cone.map(x => x.p10), name: '10%', line: { color: '#4da3ff' } },
    { x: w, y: r.cone.map(x => x.min), name: 'min', line: { color: '#2bd47d', dash: 'dot' } },
    { x: w, y: r.cone.map(x => x.current), name: 'current', mode: 'markers+lines', marker: { size: 8, color: '#ffd23f' }, line: { color: '#ffd23f' } }],
    { title: { text: 'Volatility cone (window, trading days)' }, xaxis: { type: 'log', tickvals: w }, yaxis: pctAxis, hovermode: 'x' });
  plot(c, [{ x: r.hist.returns, type: 'histogram', nbinsx: 80, marker: { color: '#fb8b1e' }, name: 'daily %' }], { title: { text: 'Daily return distribution (%)' }, showlegend: false, hovermode: 'closest' });
}, { sym: 1 });

// ================================================================== OMON
reg('OMON', 'Derivatives', 'Option monitor: chain, IV, greeks, OI, max pain', async (p, sym, args) => {
  const st = p.st;
  const draw = async () => {
    const r = await api(`/api/options/${encodeURIComponent(sym)}${st.exp ? '?expiry=' + st.exp : ''}`); p.setSource(r.source);
    st.exp = r.expiry; const s = r.summary, a = uid(), b = uid();
    const strikes = [...new Set([...r.calls.map(x => x.strike), ...r.puts.map(x => x.strike)])].sort((x, y) => x - y);
    const C = Object.fromEntries(r.calls.map(x => [x.strike, x])), P = Object.fromEntries(r.puts.map(x => [x.strike, x]));
    const atmK = strikes.reduce((b2, k) => Math.abs(k - r.spot) < Math.abs(b2 - r.spot) ? k : b2, strikes[0]);
    const near = strikes.filter(k => k > r.spot * 0.75 && k < r.spot * 1.25);
    const side = (o, kind) => o ? [fmt(o.bid), fmt(o.ask), fmt(o.mid), pct(o.iv, 1), fmt(o.delta, 2), fmt(o.gamma, 4), fmt(o.theta, 3), big(o.volume), big(o.openInterest)] : Array(9).fill('');
    const rows = near.map(k => {
      const c = side(C[k]), pp = side(P[k]), cI = k < r.spot ? 'itm' : '', pI = k > r.spot ? 'itm' : '';
      return `<tr>${c.map(v => `<td class="${cI}">${v}</td>`).join('')}<td class="${k === atmK ? 'atm' : ''}" style="text-align:center;color:#fff;font-weight:600">${fmt(k)}</td>${pp.map(v => `<td class="${pI}">${v}</td>`).join('')}</tr>`;
    }).join('');
    const hdr = ['Bid', 'Ask', 'Mid', 'IV', 'Δ', 'Γ', 'Θ/d', 'Vol', 'OI'];
    p.body.innerHTML = `<div class="toolbar"><b class="amb">${esc(r.symbol)}</b> spot <b>${fmtPx(r.spot)}</b><span class="sep"></span>Expiry
      <select data-e>${opts(r.expiries, r.expiry)}</select><span class="dim">${Math.round(r.T * 365)} days · r=${pct(r.rate)}</span><span class="sep"></span>
      <button data-go="VOLS">VOL SURFACE</button><button data-go="OVME">PRICER</button></div>
      <div class="cards">${card('ATM IV', pct(s.atm_iv, 1))}${card('ATM straddle', fmt(s.straddle))}${card('Implied move', fmt(s.implied_move_pct) + '%')}${card('Put/Call vol', fmt(s.pc_volume))}
      ${card('Put/Call OI', fmt(s.pc_oi))}${card('Max pain', fmt(s.max_pain))}${card('Call OI', big(s.call_oi))}${card('Put OI', big(s.put_oi))}</div>
      <div class="row"><div id="${a}" class="chart short"></div><div id="${b}" class="chart short"></div></div>
      <table class="t"><thead><tr><th colspan="9" style="text-align:center;color:#2bd47d">CALLS</th><th></th><th colspan="9" style="text-align:center;color:#ff5252">PUTS</th></tr>
      <tr>${hdr.map(h => `<th>${h}</th>`).join('')}<th style="text-align:center">Strike</th>${hdr.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
    const smile = (arr, kind) => arr.filter(x => x.iv && x.strike > r.spot * 0.7 && x.strike < r.spot * 1.3 && x.iv < 3).map(x => [x.strike, x.iv * 100]);
    const cs = smile(r.calls), ps = smile(r.puts);
    plot(a, [{ x: cs.map(v => v[0]), y: cs.map(v => v[1]), name: 'Calls IV', mode: 'lines+markers', marker: { size: 4 }, line: { color: '#2bd47d' } },
      { x: ps.map(v => v[0]), y: ps.map(v => v[1]), name: 'Puts IV', mode: 'lines+markers', marker: { size: 4 }, line: { color: '#ff5252' } }],
      { title: { text: 'Volatility smile' }, yaxis: pctAxis, shapes: [{ type: 'line', x0: r.spot, x1: r.spot, yref: 'paper', y0: 0, y1: 1, line: { color: '#ffd23f', dash: 'dot' } }], hovermode: 'x' });
    plot(b, [{ x: near, y: near.map(k => C[k]?.openInterest || 0), type: 'bar', name: 'Call OI', marker: { color: '#2bd47d' } },
      { x: near, y: near.map(k => -(P[k]?.openInterest || 0)), type: 'bar', name: 'Put OI', marker: { color: '#ff5252' } }],
      { title: { text: 'Open interest by strike' }, barmode: 'relative', shapes: [{ type: 'line', x0: r.spot, x1: r.spot, yref: 'paper', y0: 0, y1: 1, line: { color: '#ffd23f', dash: 'dot' } }] });
    $('[data-e]', p.body).onchange = e => { st.exp = e.target.value; draw(); };
    $$('[data-go]', p.body).forEach(bb => bb.onclick = () => goto(`${r.symbol} ${bb.dataset.go}`));
  };
  await draw();
}, { sym: 1 });

// ================================================================== VOLS
reg('VOLS', 'Derivatives', 'Implied volatility surface, smiles & term structure', async (p, sym) => {
  const r = await api(`/api/volsurface/${encodeURIComponent(sym)}?n=8`); p.setSource(r.source);
  if (!r.smiles.length) { p.body.innerHTML = '<div class="dim">No usable option quotes.</div>'; return; }
  const a = uid(), b = uid(), c = uid();
  p.body.innerHTML = `<div id="${a}" class="chart tall"></div><div class="row"><div id="${b}" class="chart"></div><div id="${c}" class="chart"></div></div>`;
  // interpolate each smile onto a common moneyness grid for the 3D surface
  const grid = Array.from({ length: 25 }, (_, i) => 0.8 + i * 0.4 / 24);
  const interp = (pts, m) => {
    if (m <= pts[0].moneyness) return pts[0].iv; if (m >= pts[pts.length - 1].moneyness) return pts[pts.length - 1].iv;
    for (let i = 1; i < pts.length; i++) if (pts[i].moneyness >= m) { const p0 = pts[i - 1], p1 = pts[i], w = (m - p0.moneyness) / (p1.moneyness - p0.moneyness); return p0.iv + w * (p1.iv - p0.iv); }
  };
  const z = r.smiles.map(s => grid.map(m => interp(s.points, m) * 100));
  plot(a, [{ type: 'surface', x: grid.map(m => (m * 100).toFixed(0) + '%'), y: r.smiles.map(s => s.days), z, colorscale: 'Portland', showscale: true, colorbar: { ticksuffix: '%', len: .6 } }],
    { title: { text: `${r.symbol} implied volatility surface (OTM options)` }, margin: { l: 0, r: 0, t: 30, b: 0 },
      scene: { xaxis: { title: { text: 'Moneyness K/S' }, color: '#aaa' }, yaxis: { title: { text: 'Days' }, color: '#aaa' }, zaxis: { title: { text: 'IV %' }, color: '#aaa' }, bgcolor: '#070707' } });
  plot(b, r.smiles.map((s, i) => ({ x: s.points.map(q => q.moneyness * 100), y: s.points.map(q => q.iv * 100), name: `${s.days}d`, mode: 'lines', line: { color: COLORS[i % COLORS.length], width: 1.2 } })),
    { title: { text: 'Smiles by expiry' }, xaxis: { title: { text: 'K/S %' } }, yaxis: pctAxis, hovermode: 'closest' });
  plot(c, [{ x: r.smiles.map(s => s.days), y: r.smiles.map(s => s.atm_iv * 100), mode: 'lines+markers', line: { color: '#fb8b1e' }, name: 'ATM IV' }],
    { title: { text: 'ATM term structure' }, xaxis: { title: { text: 'Days to expiry' } }, yaxis: pctAxis, showlegend: false });
}, { sym: 1, defaultSym: 'SPY' });

// ================================================================== OVME
const STRATS = {
  single: { label: 'Single option', legs: (K, w, kind) => [{ kind, strike: K, qty: 1 }] },
  long_straddle: { label: 'Long straddle', legs: K => [{ kind: 'call', strike: K, qty: 1 }, { kind: 'put', strike: K, qty: 1 }] },
  long_strangle: { label: 'Long strangle', legs: (K, w) => [{ kind: 'call', strike: K + w, qty: 1 }, { kind: 'put', strike: K - w, qty: 1 }] },
  bull_call: { label: 'Bull call spread', legs: (K, w) => [{ kind: 'call', strike: K, qty: 1 }, { kind: 'call', strike: K + w, qty: -1 }] },
  bear_put: { label: 'Bear put spread', legs: (K, w) => [{ kind: 'put', strike: K, qty: 1 }, { kind: 'put', strike: K - w, qty: -1 }] },
  iron_condor: { label: 'Short iron condor', legs: (K, w) => [{ kind: 'put', strike: K - 2 * w, qty: 1 }, { kind: 'put', strike: K - w, qty: -1 }, { kind: 'call', strike: K + w, qty: -1 }, { kind: 'call', strike: K + 2 * w, qty: 1 }] },
  butterfly: { label: 'Long call butterfly', legs: (K, w) => [{ kind: 'call', strike: K - w, qty: 1 }, { kind: 'call', strike: K, qty: -2 }, { kind: 'call', strike: K + w, qty: 1 }] },
  covered_call: { label: 'Covered call', legs: (K, w, k, S) => [{ kind: 'stock', qty: 1, premium: S }, { kind: 'call', strike: K + w, qty: -1 }] },
  protective_put: { label: 'Protective put', legs: (K, w, k, S) => [{ kind: 'stock', qty: 1, premium: S }, { kind: 'put', strike: K - w, qty: 1 }] },
  risk_reversal: { label: 'Risk reversal', legs: (K, w) => [{ kind: 'call', strike: K + w, qty: 1 }, { kind: 'put', strike: K - w, qty: -1 }] },
};
reg('OVME', 'Derivatives', 'Option pricer: BSM + American binomial, greeks, strategy payoffs', async (p, _s, args) => {
  let spot = 100, vol = 20;
  if (args[0]) { try { const d = await api('/api/des/' + encodeURIComponent(args[0])); spot = d.quote.last; vol = d.realized_vol['30d'] || 20; p.setSource(d.source); } catch { } }
  const r0 = Math.round(spot / (spot > 200 ? 5 : 1)) * (spot > 200 ? 5 : 1);
  p.body.innerHTML = `<form class="form">
    <label>Underlying<input name="und" value="${esc(args[0] || '')}" placeholder="optional"></label><label>Spot<input name="spot" type="number" step="any" value="${spot.toFixed(2)}"></label>
    <label>Strike<input name="strike" type="number" step="any" value="${r0}"></label><label>Days to expiry<input name="days" type="number" value="30"></label>
    <label>Volatility %<input name="vol" type="number" step="any" value="${vol.toFixed(1)}"></label><label>Rate %<input name="rate" type="number" step="any" value="4"></label>
    <label>Div yield %<input name="div" type="number" step="any" value="0"></label><label>Type<select name="kind">${opts([['call', 'Call'], ['put', 'Put']], 'call')}</select></label>
    <label>Strategy<select name="strat">${opts(Object.entries(STRATS).map(([k, v]) => [k, v.label]), 'single')}</select></label><label>Wing width<input name="width" type="number" step="any" value="${(r0 * 0.05).toFixed(0)}"></label>
    <div class="actions"><button class="primary" type="submit">PRICE</button><span class="dim">Also try MCO for Monte Carlo pricing of exotic payoffs (Asian, barrier, lookback)</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body);
  const run = async () => {
    const f = formData(form), S = +f.spot, K = +f.strike, w = +f.width;
    const legs = STRATS[f.strat].legs(K, w, f.kind, S);
    const r = await api('/api/pricer', { spot: S, strike: K, days: +f.days, vol: +f.vol / 100, rate: +f.rate / 100, div: +f.div / 100, kind: f.kind, legs });
    const g = r.european, a = uid(), b = uid();
    const net = r.legs.reduce((s, l) => s + (l.kind === 'stock' ? 0 : l.qty * l.premium), 0);
    out.innerHTML = `<div class="cards">${card('BSM price', fmt(g.price, 4))}${card('American (CRR)', fmt(r.american, 4))}${card('Early-ex premium', fmt(r.early_ex_premium, 4))}
      ${card('Delta', fmt(g.delta, 4))}${card('Gamma', fmt(g.gamma, 5))}${card('Vega (1 vol)', fmt(g.vega, 4))}${card('Theta /day', fmt(g.theta, 4))}${card('Rho (1%)', fmt(g.rho, 4))}
      ${card('Vanna', fmt(g.vanna, 5))}${card('Volga', fmt(g.volga, 6))}${card('Charm /day', fmt(g.charm, 5))}${card('P(ITM)', pct(g.prob_itm, 1))}</div>
      <h3>${esc(STRATS[f.strat].label)} — net premium ${net >= 0 ? 'paid' : 'received'}: ${fmt(Math.abs(net), 3)}</h3>
      ${table([{ h: 'Leg', l: 1, f: l => l.kind.toUpperCase() }, { h: 'Qty', f: l => (l.qty > 0 ? '+' : '') + l.qty }, { h: 'Strike', f: l => l.kind === 'stock' ? '—' : fmt(l.strike) }, { h: 'Premium', f: l => fmt(l.premium, 3) }], r.legs)}
      <div class="row"><div id="${a}" class="chart"></div><div id="${b}" class="chart"></div></div>`;
    plot(a, [{ x: r.payoff.spot, y: r.payoff.expiry, name: 'At expiry', line: { color: '#fb8b1e' } }, { x: r.payoff.spot, y: r.payoff.today, name: 'Today (BSM)', line: { color: '#4da3ff', dash: 'dot' } }],
      { title: { text: 'Strategy P&L vs spot' }, shapes: [{ type: 'line', x0: S, x1: S, yref: 'paper', y0: 0, y1: 1, line: { color: '#ffd23f', dash: 'dot' } }, { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 0, y1: 0, line: { color: '#555' } }] });
    plot(b, [{ x: r.by_spot.spot, y: r.by_spot.delta, name: 'Delta', line: { color: '#2bd47d' } }, { x: r.by_spot.spot, y: r.by_spot.gamma, name: 'Gamma', yaxis: 'y2', line: { color: '#e056fd' } }],
      { title: { text: 'Single-option greeks vs spot' }, yaxis2: { overlaying: 'y', side: 'right', showgrid: false } });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  await run();
});

// ================================================================== BT
reg('BT', 'Quant Lab', 'Strategy backtester: built-in or custom formula, costs, vol targeting, optimisation', async (p, _s, args) => {
  const sym = args[0];
  const strats = await api('/api/strategies'); const funcs = strats._functions; delete strats._functions;
  p.body.innerHTML = `<form class="form">
    <label>Symbol<input name="symbol" value="${esc(sym || 'SPY')}"></label><label>Period<select name="period">${opts(['1y', '2y', '5y', '10y', 'max'], '10y')}</select></label>
    <label>Strategy<select name="strategy">${opts([...Object.entries(strats).map(([k, v]) => [k, v.label]), ['custom', '✎ Custom formula']], 'sma_cross')}</select></label>
    <label>Cost (bps/turnover)<input name="cost_bps" type="number" step="any" value="5"></label><label>Vol target % (blank=off)<input name="vol_target" type="number" step="any" placeholder="e.g. 15"></label>
    <label>Max leverage<input name="max_leverage" type="number" step="any" value="2"></label><label>Allow short<select name="allow_short">${opts([['false', 'No'], ['true', 'Yes']], 'false')}</select></label>
    <label class="wide">Signal formula (position = value; true→1, false→0)<textarea name="expr"></textarea></label>
    <div class="actions"><button class="primary" type="submit">RUN BACKTEST</button><button type="button" data-opt>OPTIMISE PARAMETERS</button><button type="button" data-wf>WALK-FORWARD TEST →</button>
      <span class="dim">Functions: ${funcs.join(', ')} · variables: close open high low volume · operators: + - * / > < and or not</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body), sel = $('[name=strategy]', form), ex = $('[name=expr]', form);
  const fill = () => { if (sel.value !== 'custom') { const s = strats[sel.value]; ex.value = s.expr.replace(/\{(\w+)\}/g, (_, k) => s.params[k]); } };
  sel.onchange = fill; fill(); ex.oninput = () => { sel.value = 'custom'; };
  const run = async () => {
    out.innerHTML = '<div class="loading">BACKTESTING…</div>';
    const f = formData(form);
    const r = await api('/api/backtest', { symbol: f.symbol, period: f.period, expr: f.expr, cost_bps: +f.cost_bps, vol_target: f.vol_target || null, max_leverage: +f.max_leverage, allow_short: f.allow_short === 'true' });
    p.setSource(r.source); p.setTitle(`BT ${r.symbol}`, r.expr);
    const a = uid(), b = uid(), s = r.stats, bs = r.bench_stats;
    const keys = ['Total Return', 'CAGR', 'Volatility', 'Sharpe', 'Sortino', 'Max Drawdown', 'Calmar', 'Longest DD (days)', 'VaR 95% (1d)', 'Hit Rate', 'Skew', 'Beta', 'Alpha (ann.)', 'Information Ratio', 'Avg Exposure', 'Turnover (ann.)'];
    const m = r.monthly;
    out.innerHTML = `<div class="cards">${card('CAGR', pct(s.CAGR), cls(s.CAGR))}${card('Sharpe', fmt(s.Sharpe))}${card('Max DD', pct(s['Max Drawdown']), 'neg')}${card('Volatility', pct(s.Volatility))}
      ${card('B&H CAGR', pct(bs.CAGR))}${card('B&H Sharpe', fmt(bs.Sharpe))}${card('Trades', r.n_trades)}${card('Exposure', pct(s['Avg Exposure']))}</div>
      <div id="${a}" class="chart tall"></div>
      <div class="row"><div><h3>Strategy vs buy & hold</h3>${table([{ h: 'Metric', l: 1, f: k => k }, { h: 'Strategy', f: k => PCT_KEYS.test(k) || k === 'Avg Exposure' ? pct(s[k]) : fmt(s[k]) }, { h: 'Buy & Hold', f: k => PCT_KEYS.test(k) ? pct(bs[k]) : fmt(bs[k]) }], keys)}</div>
      <div><h3>Monthly returns</h3><div id="${b}" class="chart" style="height:${Math.max(200, 24 * m.years.length + 60)}px"></div></div></div>
      <h3>Trades (last ${r.trades.length} of ${r.n_trades})</h3><div style="max-height:260px;overflow:auto">${table([{ h: 'Entry', k: 'entry' }, { h: 'Exit', k: 'exit' }, { h: 'Side', f: t => t.side }, { h: 'Entry px', f: t => fmtPx(t.entry_px) }, { h: 'Exit px', f: t => fmtPx(t.exit_px) }, { h: 'Return', f: t => signed(t.ret * 100, 2, '%') }, { h: 'Bars', k: 'bars' }], [...r.trades].reverse())}</div>`;
    plot(a, [{ x: r.dates, y: r.equity, name: 'Strategy', line: { color: '#fb8b1e', width: 1.6 } }, { x: r.dates, y: r.benchmark, name: 'Buy & hold', line: { color: '#777', width: 1 } },
      { x: r.dates, y: r.drawdown, name: 'Strategy DD', yaxis: 'y2', fill: 'tozeroy', line: { color: '#ff5252', width: .8 }, fillcolor: 'rgba(255,82,82,.25)' },
      { x: r.dates, y: r.position, name: 'Position', yaxis: 'y3', line: { color: '#4da3ff', width: 1, shape: 'hv' } }],
      { title: { text: `${r.symbol}: ${r.expr}` }, yaxis: { domain: [0.42, 1], type: 'log', side: 'right' }, yaxis2: { domain: [0.17, 0.38], side: 'right', ticksuffix: '%' }, yaxis3: { domain: [0, 0.13], side: 'right' } });
    plot(b, [{ z: m.values.map(rw => rw.map(v => v === null ? null : v * 100)), x: m.months, y: m.years.map(String), type: 'heatmap', colorscale: heatColors(), zmid: 0,
      text: m.values.map(rw => rw.map(v => v === null ? '' : (v * 100).toFixed(1))), texttemplate: '%{text}', showscale: false, hovertemplate: '%{y} %{x}: %{z:.2f}%<extra></extra>' }],
      { margin: { l: 44, t: 10, b: 24 }, yaxis: { autorange: 'reversed', type: 'category' }, xaxis: { side: 'top' }, hovermode: 'closest' });
  };
  const optimise = async () => {
    const f = formData(form); if (f.strategy === 'custom' || f.strategy === 'buy_hold' || f.strategy === 'macd') { out.innerHTML = '<div class="err">Pick a parameterised built-in strategy (SMA/EMA cross, momentum, RSI, Bollinger, Donchian) to optimise.</div>'; return; }
    out.innerHTML = '<div class="loading">RUNNING PARAMETER GRID…</div>';
    const r = await api('/api/backtest/optimize', { symbol: f.symbol, period: f.period, strategy: f.strategy, cost_bps: +f.cost_bps });
    const keys = Object.keys(r.grid), a = uid();
    out.innerHTML = `<div class="banner">In-sample optimisation — beware of overfitting. Prefer broad plateaus over single peaks, and validate out-of-sample with <span class="code">WF</span> (walk-forward).</div><div id="${a}" class="chart tall"></div>`;
    if (keys.length === 2) {
      const [k1, k2] = keys, z = r.grid[k2].map(v2 => r.grid[k1].map(v1 => (r.results.find(x => x[k1] === v1 && x[k2] === v2) || {}).value ?? null));
      plot(a, [{ z, x: r.grid[k1].map(String), y: r.grid[k2].map(String), type: 'heatmap', colorscale: 'Viridis', text: z.map(rw => rw.map(v => v === null ? '' : v.toFixed(2))), texttemplate: '%{text}' }],
        { title: { text: `${r.symbol} ${r.strategy}: ${r.metric} by parameters` }, xaxis: { title: { text: k1 }, type: 'category' }, yaxis: { title: { text: k2 }, type: 'category' }, hovermode: 'closest' });
    } else {
      plot(a, [{ x: r.results.map(x => String(x[keys[0]])), y: r.results.map(x => x.value), type: 'bar', marker: { color: '#fb8b1e' } }], { title: { text: `${r.metric} by ${keys[0]}` }, showlegend: false });
    }
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  $('[data-opt]', p.body).onclick = () => optimise().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`);
  $('[data-wf]', p.body).onclick = () => { const f = formData(form); goto(`WF ${f.symbol} ${['custom', 'buy_hold'].includes(f.strategy) ? 'sma_cross' : f.strategy}`.toUpperCase()); };
  await run();
});

// ================================================================== PORT
const METHODS = [['max_sharpe', 'Max Sharpe'], ['min_var', 'Minimum variance'], ['risk_parity', 'Risk parity (ERC)'], ['max_diversification', 'Max diversification'], ['inverse_vol', 'Inverse volatility'], ['equal', 'Equal weight']];
reg('PORT', 'Portfolio & Trading', 'Portfolio optimiser: frontier, risk budget, VaR, stress tests', async (p, _s, args) => {
  const syms = args.length >= 2 ? args.join(' ') : 'SPY QQQ TLT GLD IEF VNQ';
  p.body.innerHTML = `<form class="form"><label class="wide">Symbols<input name="symbols" value="${esc(syms)}"></label>
    <label>Method<select name="method">${opts(METHODS, 'max_sharpe')}</select></label><label>Lookback<select name="period">${opts(['1y', '2y', '3y', '5y', '10y'], '5y')}</select></label>
    <label>Max weight %<input name="max_weight" type="number" value="40"></label><label>Long only<select name="long_only">${opts([['true', 'Yes'], ['false', 'No (130/30 style)']], 'true')}</select></label>
    <label>Portfolio value<input name="value" type="number" value="1000000"></label>
    <div class="actions"><button class="primary" type="submit">OPTIMISE</button><button type="button" data-mc>SIMULATE IN MONTE CARLO →</button><span class="dim">Covariance uses Ledoit-Wolf shrinkage; expected returns are historical means (noisy — treat Max Sharpe with care).</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body); let last = null;
  const run = async () => {
    out.innerHTML = '<div class="loading">OPTIMISING…</div>';
    const f = formData(form);
    const r = last = await api('/api/portfolio', { symbols: f.symbols, method: f.method, period: f.period, max_weight: +f.max_weight / 100, long_only: f.long_only === 'true', value: +f.value });
    p.setSource(r.source);
    const ids = [uid(), uid(), uid(), uid(), uid()];
    out.innerHTML = `<div class="cards">${card('Exp. return', pct(r.exp_return))}${card('Exp. vol', pct(r.exp_vol))}${card('Sharpe (ex-ante)', fmt((r.exp_return - 0.04) / r.exp_vol))}
      ${card('Hist. CAGR', pct(r.stats.CAGR))}${card('Hist. max DD', pct(r.stats['Max Drawdown']), 'neg')}${card('VaR 95% 1d', '$' + big(r.var[0].historical))}${card('VaR 99% 10d', '$' + big(r.var_10d[1].historical))}${card('CVaR 95% 1d', '$' + big(r.var[0].historical_cvar))}</div>
      <div class="row"><div id="${ids[0]}" class="chart"></div><div id="${ids[1]}" class="chart"></div></div>
      <div class="row"><div id="${ids[2]}" class="chart"></div><div id="${ids[3]}" class="chart"></div></div>
      <div class="row"><div><h3>Allocation</h3>${table([{ h: 'Symbol', l: 1, f: x => `<b class="amb">${x.symbol}</b>` }, { h: 'Weight', f: x => pct(x.weight) }, { h: 'Value', f: x => big(x.value) }, { h: 'Price', f: x => fmtPx(x.price) }, { h: 'Shares', f: x => fmt(x.shares, 0) }], r.allocation)}
        <h3>Optimiser comparison</h3>${table([{ h: 'Method', l: 1, f: k => k }, { h: 'Return', f: k => pct(r.compare[k].ret) }, { h: 'Vol', f: k => pct(r.compare[k].vol) }, { h: 'Sharpe', f: k => fmt(r.compare[k].sharpe) }, ...r.symbols.map(s => ({ h: s, f: k => pct(r.compare[k].weights[s], 0) }))], Object.keys(r.compare))}</div>
      <div><h3>Risk decomposition</h3>${table([{ h: 'Symbol', l: 1, k: 'symbol' }, { h: 'Weight', f: x => pct(x.weight) }, { h: 'Marginal CTR', f: x => pct(x.mctr) }, { h: 'Risk contrib.', f: x => pct(x.ctr) }, { h: '% of risk', f: x => pct(x.pct) }], r.risk)}
        <h3>VaR (portfolio $)</h3>${table([{ h: 'Level', f: x => pct(x.level, 0) }, { h: 'Hist 1d', f: x => big(x.historical) }, { h: 'Param 1d', f: x => big(x.parametric) }, { h: 'CF 1d', f: x => big(x.cornish_fisher) }, { h: 'CVaR 1d', f: x => big(x.historical_cvar) }], r.var)}
        <h3>Stress scenarios (beta-based)</h3>${table([{ h: 'Scenario', l: 1, k: 'scenario' }, { h: 'Shock', f: x => pct(x.shock, 1) }, { h: 'P&L', f: x => signed(x.pnl, 0) }], r.stress)}</div></div>
      <div id="${ids[4]}" class="chart"></div>`;
    const fr = r.frontier;
    plot(ids[0], [{ x: fr.map(q => q.vol * 100), y: fr.map(q => q.ret * 100), name: 'Efficient frontier', mode: 'lines', line: { color: '#fb8b1e', width: 2 } },
      { x: r.assets.map(a => a.vol * 100), y: r.assets.map(a => a.mu * 100), text: r.assets.map(a => a.symbol), mode: 'markers+text', textposition: 'top center', name: 'Assets', marker: { color: '#4da3ff', size: 8 } },
      { x: Object.values(r.compare).map(c => c.vol * 100), y: Object.values(r.compare).map(c => c.ret * 100), text: Object.keys(r.compare), mode: 'markers', name: 'Optimisers', marker: { color: '#aaa', size: 7, symbol: 'diamond' } },
      { x: [r.exp_vol * 100], y: [r.exp_return * 100], mode: 'markers', name: 'Selected', marker: { color: '#ffd23f', size: 14, symbol: 'star' } }],
      { title: { text: 'Mean-variance space (annualised %)' }, xaxis: { title: { text: 'Volatility %' } }, yaxis: { title: { text: 'Return %' } }, hovermode: 'closest' });
    plot(ids[1], [{ labels: r.symbols, values: r.symbols.map(s => Math.max(r.weights[s], 0)), type: 'pie', hole: .55, marker: { colors: COLORS }, textinfo: 'label+percent' }], { title: { text: `Weights — ${r.method}` }, showlegend: false });
    plot(ids[2], [{ x: r.risk.map(x => x.symbol), y: r.risk.map(x => x.weight * 100), type: 'bar', name: 'Weight %', marker: { color: '#4da3ff' } }, { x: r.risk.map(x => x.symbol), y: r.risk.map(x => x.pct * 100), type: 'bar', name: 'Risk %', marker: { color: '#fb8b1e' } }], { title: { text: 'Capital vs risk allocation' }, yaxis: pctAxis });
    plot(ids[3], [{ z: r.corr.matrix, x: r.corr.symbols, y: r.corr.symbols, type: 'heatmap', zmin: -1, zmax: 1, colorscale: heatColors(), text: r.corr.matrix.map(rw => rw.map(v => fmt(v))), texttemplate: '%{text}' }], { title: { text: 'Correlation' }, yaxis: { autorange: 'reversed' }, hovermode: 'closest' });
    plot(ids[4], [{ x: r.dates, y: r.equity, name: 'Portfolio (monthly rebal.)', line: { color: '#fb8b1e' } }, { x: r.dates, y: r.bench, name: r.bench_name, line: { color: '#777' } }], { title: { text: 'Historical simulation (in-sample)' }, yaxis: { type: 'log' } });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  $('[data-mc]', p.body).onclick = () => { if (last) { sessionStorage.setItem('qt.mcp', JSON.stringify(last.weights)); goto('MCP ' + last.symbols.join(' ')); } };
  await run();
});

// ================================================================== MONTE CARLO: price paths
const MODEL_LABELS = [['gbm', 'Geometric Brownian motion'], ['student_t', 'Fat tails (Student-t)'], ['bootstrap', 'Historical block bootstrap'], ['jump', 'Merton jump-diffusion'], ['garch', 'GARCH(1,1) vol clustering']];
reg('MC', 'Monte Carlo', 'Monte Carlo price simulation: 5 models, fan chart, hit probabilities, VaR', async (p, _s, args) => {
  const sym = args[0];
  p.body.innerHTML = `<form class="form"><label>Symbol<input name="symbol" value="${esc(sym || 'SPY')}"></label><label>Model<select name="model">${opts(MODEL_LABELS, 'bootstrap')}</select></label>
    <label>Horizon (trading days)<input name="days" type="number" value="252"></label><label>Paths<input name="paths" type="number" value="10000"></label>
    <label>Calibration window<select name="period">${opts(['1y', '2y', '5y', '10y'], '5y')}</select></label>
    <label>Drift % p.a. (blank = hist.)<input name="mu" type="number" step="any" placeholder="historical"></label><label>Vol % p.a. (blank = hist.)<input name="sigma" type="number" step="any" placeholder="historical"></label>
    <label>Upside target %<input name="up_pct" type="number" step="any" value="20"></label><label>Downside level %<input name="down_pct" type="number" step="any" value="20"></label>
    <label>Seed (blank = random)<input name="seed" type="number"></label>
    <div class="actions"><button class="primary" type="submit">SIMULATE</button><button type="button" data-cmp>COMPARE ALL MODELS</button>
      <span class="dim">Historical drift is noisy — consider setting a conservative drift (e.g. 5–7%) for planning.</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body);
  const body = () => { const f = formData(form); return { ...f, days: +f.days, paths: +f.paths, seed: f.seed ? +f.seed : null }; };
  const run = async () => {
    out.innerHTML = '<div class="loading">SIMULATING PATHS…</div>';
    const r = await api('/api/mc', body()); p.setSource(r.source); p.setTitle(`MC ${r.symbol}`, MODEL_LABELS.find(m => m[0] === r.model)[1]);
    const a = uid(), b = uid(), T = r.terminal;
    const f = r.fan, d = r.dates;
    const sample = r.sample.slice(0, 25).map(path => ({ x: d, y: path, mode: 'lines', line: { width: .5, color: 'rgba(77,163,255,.25)' }, hoverinfo: 'skip', showlegend: false }));
    out.innerHTML = `<div class="cards">${card('Spot', fmtPx(r.S0))}${card('Median terminal', fmtPx(T.median))}${card('Mean terminal', fmtPx(T.mean))}${card('P(end above spot)', pct(r.prob_up, 1))}
      ${r.up ? card(`P(touch +${body().up_pct}%)`, pct(r.prob_touch_up, 1), 'pos') : ''}${r.down ? card(`P(touch −${body().down_pct}%)`, pct(r.prob_touch_down, 1), 'neg') : ''}
      ${card('Expected return', pct(r.exp_return), cls(r.exp_return))}${card('VaR 95%', pct(r.var95), 'neg')}${card('CVaR 95%', pct(r.cvar95), 'neg')}${card('VaR 99%', pct(r.var99), 'neg')}
      ${card('Median max DD', pct(r.median_max_dd), 'neg')}${card('5% worst max DD', pct(r.p95_max_dd), 'neg')}</div>
      <div id="${a}" class="chart tall"></div><div class="row"><div id="${b}" class="chart"></div>
      <div><h3>Calibration (${r.calibration.n} daily obs.)</h3>${kv({ 'Drift used': pct(r.mu), 'Vol used': pct(r.sigma), 'Hist. drift (log)': pct(r.calibration.mu), 'Hist. vol': pct(r.calibration.sigma),
        'Student-t d.o.f.': fmt(r.calibration.t_df, 1), 'Jumps / year': fmt(r.calibration.jump_lambda, 1), 'Avg jump': pct(r.calibration.jump_mean), 'Jump std': pct(r.calibration.jump_std),
        ...(r.garch ? { 'GARCH α': fmt(r.garch.alpha, 3), 'GARCH β': fmt(r.garch.beta, 3), 'GARCH current vol': fmt(r.garch.current_vol) + '%', 'GARCH long-run vol': fmt(r.garch.long_run_vol) + '%' } : {}),
        'Paths': fmt(r.paths, 0), 'Horizon': r.days + ' days' })}
        <h3>Terminal price percentiles</h3>${kv({ '1%': fmtPx(T.p1), '5%': fmtPx(T.p5), '50%': fmtPx(T.median), '95%': fmtPx(T.p95), '99%': fmtPx(T.p99) })}</div></div>`;
    const lines = [];
    if (r.up) lines.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.up, y1: r.up, line: { color: '#2bd47d', dash: 'dash', width: 1 } });
    if (r.down) lines.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.down, y1: r.down, line: { color: '#ff5252', dash: 'dash', width: 1 } });
    plot(a, [...sample,
      { x: r.hist_dates, y: r.hist, name: 'History', line: { color: '#ddd', width: 1.3 } },
      { x: d, y: f.p95, line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }, { x: d, y: f.p5, fill: 'tonexty', fillcolor: 'rgba(251,139,30,.13)', line: { width: 0 }, name: '5–95%' },
      { x: d, y: f.p75, line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }, { x: d, y: f.p25, fill: 'tonexty', fillcolor: 'rgba(251,139,30,.28)', line: { width: 0 }, name: '25–75%' },
      { x: d, y: f.p50, name: 'Median', line: { color: '#fb8b1e', width: 2 } }, { x: d, y: f.mean, name: 'Mean', line: { color: '#ffd23f', width: 1, dash: 'dot' } }],
      { title: { text: `${r.symbol}: ${r.paths.toLocaleString()} simulated paths` }, shapes: lines, yaxis: { side: 'right' } });
    plot(b, [{ x: r.hist.x, y: r.hist.y, type: 'bar', marker: { color: r.hist.x.map(v => v >= 0 ? '#2bd47d' : '#ff5252') }, name: 'paths' }],
      { title: { text: 'Distribution of terminal return (%)' }, showlegend: false, bargap: 0.02, hovermode: 'closest' });
  };
  const compare = async () => {
    out.innerHTML = '<div class="loading">RUNNING ALL 5 MODELS…</div>';
    const base = body(), res = await Promise.all(MODEL_LABELS.map(([m]) => api('/api/mc', { ...base, model: m, seed: base.seed || 42 })));
    p.setSource(res[0].source);
    const a = uid();
    out.innerHTML = `<div id="${a}" class="chart tall"></div>` + table([{ h: 'Model', l: 1, f: r => MODEL_LABELS.find(m => m[0] === r.model)[1] }, { h: 'P(up)', f: r => pct(r.prob_up, 1) },
      { h: 'Exp. ret', f: r => pct(r.exp_return) }, { h: 'Median ret', f: r => pct(r.median_return) }, { h: 'VaR 95%', f: r => pct(r.var95) }, { h: 'CVaR 95%', f: r => pct(r.cvar95) }, { h: 'VaR 99%', f: r => pct(r.var99) },
      { h: 'P(touch up)', f: r => pct(r.prob_touch_up, 1) }, { h: 'P(touch down)', f: r => pct(r.prob_touch_down, 1) }, { h: 'Median max DD', f: r => pct(r.median_max_dd) }], res) +
      `<p class="dim">Same drift/vol inputs, different dynamics: the spread between models in the tails (VaR 99%, touch-down probability) is a measure of model risk.</p>`;
    plot(a, res.map((r, i) => ({ x: r.hist.x, y: r.hist.y, name: r.model, mode: 'lines', line: { shape: 'spline', color: COLORS[i], width: 1.5 } })), { title: { text: 'Terminal return distributions by model (%)' }, hovermode: 'closest' });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  $('[data-cmp]', p.body).onclick = () => compare().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`);
  await run();
});

// ================================================================== MONTE CARLO: options
const STYLE_LABELS = [['european', 'European (vs BSM check)'], ['asian', 'Asian (arithmetic avg)'], ['up_and_out', 'Up-and-out barrier'], ['up_and_in', 'Up-and-in barrier'],
  ['down_and_out', 'Down-and-out barrier'], ['down_and_in', 'Down-and-in barrier'], ['lookback_floating', 'Lookback (floating strike)'], ['digital', 'Digital (cash-or-nothing, 1)']];
reg('MCO', 'Monte Carlo', 'Monte Carlo option pricer: exotics, convergence, confidence intervals', async (p, _s, args) => {
  let spot = 100, vol = 20;
  if (args[0]) { try { const d = await api('/api/des/' + encodeURIComponent(args[0])); spot = d.quote.last; vol = d.realized_vol['30d'] || 20; p.setSource(d.source); } catch { } }
  p.body.innerHTML = `<form class="form"><label>Spot<input name="spot" type="number" step="any" value="${spot.toFixed(2)}"></label><label>Strike<input name="strike" type="number" step="any" value="${spot.toFixed(0)}"></label>
    <label>Days<input name="days" type="number" value="180"></label><label>Vol %<input name="vol" type="number" step="any" value="${vol.toFixed(1)}"></label><label>Rate %<input name="rate" type="number" step="any" value="4"></label>
    <label>Type<select name="kind">${opts([['call', 'Call'], ['put', 'Put']], 'call')}</select></label><label>Payoff<select name="style">${opts(STYLE_LABELS, 'up_and_out')}</select></label>
    <label>Barrier<input name="barrier" type="number" step="any" value="${(spot * 1.2).toFixed(0)}"></label><label>Paths<input name="paths" type="number" value="100000"></label>
    <label>Antithetic variates<select name="antithetic">${opts([['true', 'On'], ['false', 'Off']], 'true')}</select></label>
    <div class="actions"><button class="primary" type="submit">PRICE BY SIMULATION</button></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body);
  const run = async () => {
    out.innerHTML = '<div class="loading">SIMULATING…</div>';
    const f = formData(form);
    const r = await api('/api/mc/option', { spot: +f.spot, strike: +f.strike, days: +f.days, vol: +f.vol / 100, rate: +f.rate / 100, kind: f.kind, style: f.style, barrier: +f.barrier, paths: +f.paths, antithetic: f.antithetic === 'true' });
    const a = uid(), b = uid(), steps = r.sample[0].length;
    out.innerHTML = `<div class="cards">${card('MC price', fmt(r.price, 4))}${card('Std error', fmt(r.std_error, 4))}${card('95% CI', `${fmt(r.ci95[0], 3)} – ${fmt(r.ci95[1], 3)}`)}
      ${card('Vanilla BSM', fmt(r.bs_european, 4))}${card(r.style === 'european' ? 'MC − BSM' : 'vs vanilla', r.style === 'european' ? fmt(r.price - r.bs_european, 4) : pct(r.price / r.bs_european - 1, 1))}
      ${card('P(payoff > 0)', pct(r.prob_payoff, 1))}${card('Paths × steps', `${big(r.paths)} × ${r.steps}`)}</div>
      <div class="row"><div id="${a}" class="chart"></div><div id="${b}" class="chart"></div></div>`;
    const ref = [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: +f.strike, y1: +f.strike, line: { color: '#ffd23f', dash: 'dot', width: 1 } }];
    if (f.style.includes('_and_')) ref.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: +f.barrier, y1: +f.barrier, line: { color: '#ff5252', dash: 'dash', width: 1.2 } });
    plot(a, r.sample.map((path, i) => ({ x: path.map((_, j) => j / (steps - 1) * +f.days), y: path, mode: 'lines', line: { width: .7, color: COLORS[i % COLORS.length] }, showlegend: false, opacity: .7 })),
      { title: { text: 'Sample paths (yellow = strike, red = barrier)' }, shapes: ref, xaxis: { title: { text: 'Days' } }, hovermode: false });
    plot(b, [{ x: r.convergence.n, y: r.convergence.price, name: 'Running MC estimate', line: { color: '#fb8b1e' } },
      { x: [r.convergence.n[0], r.convergence.n[r.convergence.n.length - 1]], y: [r.price, r.price], name: 'Final', line: { color: '#777', dash: 'dot' } },
      ...(r.style === 'european' ? [{ x: [r.convergence.n[0], r.convergence.n[r.convergence.n.length - 1]], y: [r.bs_european, r.bs_european], name: 'BSM exact', line: { color: '#2bd47d', dash: 'dash' } }] : [])],
      { title: { text: 'Convergence' }, xaxis: { type: 'log', title: { text: 'paths' } } });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  await run();
});

// ================================================================== MONTE CARLO: portfolio / wealth
reg('MCP', 'Monte Carlo', 'Monte Carlo portfolio & wealth planner: goals, contributions, ruin risk', async (p, _s, args) => {
  const syms = args.length ? args : ['SPY', 'TLT', 'GLD'];
  let w = JSON.parse(sessionStorage.getItem('qt.mcp') || 'null'); sessionStorage.removeItem('qt.mcp');
  const wstr = w ? Object.entries(w).map(([k, v]) => `${k}:${(v * 100).toFixed(1)}`).join(' ') : syms.map(s => `${s}:${(100 / syms.length).toFixed(1)}`).join(' ');
  p.body.innerHTML = `<form class="form"><label class="wide">Weights (SYMBOL:% …)<input name="weights" value="${esc(wstr)}"></label>
    <label>Start value<input name="value" type="number" value="1000000"></label><label>Years<input name="years" type="number" step="any" value="10"></label>
    <label>Annual contribution (− = withdrawal)<input name="contribution" type="number" value="0"></label><label>Goal value<input name="goal" type="number" value="2000000"></label>
    <label>Method<select name="method">${opts([['bootstrap', 'Correlated block bootstrap'], ['normal', 'Multivariate normal']], 'bootstrap')}</select></label>
    <label>History used<select name="period">${opts(['5y', '10y', 'max'], '10y')}</select></label><label>Paths<input name="paths" type="number" value="5000"></label>
    <div class="actions"><button class="primary" type="submit">SIMULATE</button><span class="dim">Monthly rebalancing to target weights; bootstrap preserves cross-asset correlation and fat tails.</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body);
  const run = async () => {
    out.innerHTML = '<div class="loading">SIMULATING PORTFOLIO…</div>';
    const f = formData(form);
    const weights = Object.fromEntries(f.weights.split(/[\s,]+/).filter(Boolean).map(t => { const [s, v] = t.split(':'); return [s.toUpperCase(), +(v || 1)]; }));
    const r = await api('/api/mc/portfolio', { symbols: Object.keys(weights).join(','), weights, value: +f.value, years: +f.years, contribution: +f.contribution, goal: f.goal ? +f.goal : null, method: f.method, period: f.period, paths: +f.paths });
    p.setSource(r.source);
    const a = uid(), b = uid(), T = r.terminal, x = r.t_years;
    out.innerHTML = `<div class="cards">${card('Median final', '$' + big(T.median))}${card('Mean final', '$' + big(T.mean))}${card('5th pct final', '$' + big(T.p5), 'neg')}${card('95th pct final', '$' + big(T.p95), 'pos')}
      ${r.prob_goal !== undefined ? card('P(reach goal)', pct(r.prob_goal, 1), r.prob_goal > .5 ? 'pos' : 'neg') : ''}${card('P(below invested)', pct(r.prob_loss, 1))}${card('P(ruin)', pct(r.prob_ruin, 2))}
      ${r.cagr_median !== null ? card('Median CAGR', pct(r.cagr_median)) : card('Total invested', '$' + big(r.invested))}${card('Median max DD', pct(r.median_max_dd), 'neg')}${card('Worst-5% max DD', pct(r.p95_max_dd), 'neg')}</div>
      <div id="${a}" class="chart tall"></div><div id="${b}" class="chart"></div>
      <p class="dim">Bootstrapped from ${esc(r.symbols.join(', '))} history since ${r.history_from}. Weights: ${Object.entries(r.weights).map(([k, v]) => `${k} ${pct(v, 1)}`).join(' · ')}. Past return patterns are not a forecast.</p>`;
    const f2 = r.fan;
    plot(a, [...r.sample.slice(0, 30).map(pth => ({ x, y: pth, mode: 'lines', line: { width: .5, color: 'rgba(77,163,255,.25)' }, hoverinfo: 'skip', showlegend: false })),
      { x, y: f2.p95, line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }, { x, y: f2.p5, fill: 'tonexty', fillcolor: 'rgba(251,139,30,.13)', line: { width: 0 }, name: '5–95%' },
      { x, y: f2.p75, line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }, { x, y: f2.p25, fill: 'tonexty', fillcolor: 'rgba(251,139,30,.3)', line: { width: 0 }, name: '25–75%' },
      { x, y: f2.p50, name: 'Median', line: { color: '#fb8b1e', width: 2 } }],
      { title: { text: 'Portfolio value fan chart' }, xaxis: { title: { text: 'Years' } }, yaxis: { side: 'right', tickformat: '$.3s' },
        shapes: f.goal ? [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: +f.goal, y1: +f.goal, line: { color: '#2bd47d', dash: 'dash' } }] : [] });
    plot(b, [{ x: r.hist.x, y: r.hist.y, type: 'bar', marker: { color: '#4da3ff' } }], { title: { text: 'Distribution of total return to horizon (%)' }, showlegend: false, hovermode: 'closest' });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  await run();
});

// ================================================================== GARCH
reg('GARCH', 'Quant Lab', 'GARCH(1,1) volatility model & vol forecast term structure', async (p, sym) => {
  const r = await api(`/api/garch/${encodeURIComponent(sym)}?period=5y&horizon=126`); p.setSource(r.source);
  const a = uid(), b = uid();
  p.body.innerHTML = `<div class="cards">${card('Current cond. vol', fmt(r.current_vol) + '%')}${card('Long-run vol', fmt(r.long_run_vol) + '%')}${card('α (shock)', fmt(r.alpha, 4))}${card('β (memory)', fmt(r.beta, 4))}
    ${card('Persistence α+β', fmt(r.persistence, 4))}${card('Shock half-life', fmt(r.half_life_days, 1) + 'd')}${card('Log-lik', fmt(r.loglik, 1))}${card('BIC', fmt(r.bic, 1))}</div>
    <div id="${a}" class="chart tall"></div><div id="${b}" class="chart"></div>
    <p class="dim">σ²ₜ = ω + α·ε²ₜ₋₁ + β·σ²ₜ₋₁, fitted by maximum likelihood. Feed these dynamics into simulations via <span class="code">MC ${esc(r.symbol)}</span> → model “GARCH”.</p>`;
  plot(a, [{ x: r.dates, y: r.realized_21d, name: 'Realized 21d', line: { color: '#555', width: 1 } }, { x: r.dates, y: r.cond_vol, name: 'GARCH conditional vol', line: { color: '#fb8b1e', width: 1.3 } }],
    { title: { text: `${r.symbol} conditional volatility (annualised %)` }, yaxis: pctAxis });
  plot(b, [{ x: r.forecast.day, y: r.forecast.daily_vol, name: 'Spot vol forecast', line: { color: '#4da3ff' } }, { x: r.forecast.day, y: r.forecast.term_vol, name: 'Avg vol to horizon (term structure)', line: { color: '#fb8b1e' } },
    { x: [1, r.forecast.day.length], y: [r.long_run_vol, r.long_run_vol], name: 'Long-run', line: { color: '#777', dash: 'dot' } }],
    { title: { text: 'Volatility forecast (trading days ahead)' }, yaxis: pctAxis });
}, { sym: 1 });

// ================================================================== STAT
reg('STAT', 'Quant Lab', 'Return diagnostics: distribution, fat tails, autocorrelation, Hurst, ADF, Kelly', async (p, sym) => {
  const r = await api(`/api/stats/${encodeURIComponent(sym)}?period=5y`); p.setSource(r.source);
  const ids = [uid(), uid(), uid(), uid()], k = r.kelly;
  const verdict = r.hurst < 0.45 ? 'mean-reverting' : r.hurst > 0.55 ? 'trending' : 'random-walk-like';
  p.body.innerHTML = `<div class="cards">${card('Ann. return', pct(r.mean_ann))}${card('Ann. vol', pct(r.vol_ann))}${card('Skew', fmt(r.skew))}${card('Excess kurtosis', fmt(r.kurtosis))}
    ${card('Fitted t d.o.f.', fmt(r.t_df, 1))}${card('Hurst', fmt(r.hurst, 3) + `<span class="dim" style="font-size:10px"> ${verdict}</span>`)}${card('Full Kelly', fmt(k.full_kelly, 2) + 'x')}${card('Half Kelly', fmt(k.half_kelly, 2) + 'x')}</div>
    <div class="row"><div id="${ids[0]}" class="chart"></div><div id="${ids[1]}" class="chart"></div></div>
    <div class="row"><div id="${ids[2]}" class="chart"></div><div id="${ids[3]}" class="chart"></div></div>
    <h3>Statistical tests</h3>${table([{ h: 'Test', l: 1, f: x => x[0] }, { h: 'Statistic', f: x => x[1] }, { h: 'p-value', f: x => x[2] }, { h: 'Interpretation', l: 1, f: x => x[3] }], [
      ['Jarque-Bera (normality)', fmt(r.jarque_bera, 1), fmt(r.jb_p, 4), r.jb_p < 0.05 ? '<span class="neg">Returns are NOT normal (fat tails / skew)</span>' : 'Cannot reject normality'],
      ['Ljung-Box(10) returns', fmt(r.ljung_box.Q, 1), fmt(r.ljung_box.p_value, 4), r.ljung_box.p_value < 0.05 ? '<span class="amb">Serial correlation in returns — some predictability</span>' : 'No significant autocorrelation'],
      ['Ljung-Box(10) squared', fmt(r.ljung_box_sq.Q, 1), fmt(r.ljung_box_sq.p_value, 4), r.ljung_box_sq.p_value < 0.05 ? '<span class="amb">Volatility clustering (ARCH effects) → use GARCH</span>' : 'No volatility clustering'],
      ['ADF on log price', fmt(r.adf_price.stat, 3), fmt(r.adf_price.p_value, 4), r.adf_price.p_value < 0.05 ? '<span class="pos">Stationary — mean-reverting price</span>' : 'Unit root: price is non-stationary (normal for assets)'],
      ['Variance ratio (5d)', fmt(r.variance_ratio_5d, 3), '—', r.variance_ratio_5d > 1.1 ? 'Momentum at weekly horizon' : r.variance_ratio_5d < 0.9 ? 'Mean reversion at weekly horizon' : 'Close to random walk'],
      ['Tail ratio (95/5)', fmt(r.tail_ratio, 3), '—', r.tail_ratio < 1 ? 'Left tail heavier than right' : 'Right tail heavier than left']])}`;
  plot(ids[0], [{ x: r.hist.x, y: r.hist.y, type: 'bar', name: 'Empirical', marker: { color: 'rgba(251,139,30,.6)' } }, { x: r.normal_pdf.x, y: r.normal_pdf.y, name: 'Normal', line: { color: '#4da3ff' } }, { x: r.t_pdf.x, y: r.t_pdf.y, name: 'Student-t', line: { color: '#2bd47d' } }],
    { title: { text: 'Daily log-return density (%)' }, bargap: 0, hovermode: 'closest' });
  const lim = [Math.min(...r.qq.theoretical), Math.max(...r.qq.theoretical)];
  plot(ids[1], [{ x: r.qq.theoretical, y: r.qq.sample, mode: 'markers', marker: { size: 4, color: '#fb8b1e' }, name: 'Quantiles' }, { x: lim, y: lim, mode: 'lines', line: { color: '#555', dash: 'dot' }, name: 'Normal' }],
    { title: { text: 'Q-Q plot vs normal' }, xaxis: { title: { text: 'theoretical z' } }, yaxis: { title: { text: 'sample z' } }, hovermode: 'closest' });
  const lags = r.acf_returns.map((_, i) => i + 1), band = r.acf_band;
  plot(ids[2], [{ x: lags, y: r.acf_returns, type: 'bar', name: 'Returns', marker: { color: '#4da3ff' } }, { x: lags, y: r.acf_squared, type: 'bar', name: 'Squared returns', marker: { color: '#fb8b1e' } }],
    { title: { text: 'Autocorrelation (dotted = 95% band)' }, shapes: [band, -band].map(y => ({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, line: { color: '#888', dash: 'dot' } })) });
  plot(ids[3], [{ x: k.curve.leverage, y: k.curve.growth_theory.map(v => v * 100), name: 'Theory (μL − ½σ²L²)', line: { color: '#4da3ff' } }, { x: k.curve.leverage, y: k.curve.growth_empirical.map(v => v * 100), name: 'Empirical', line: { color: '#fb8b1e' } }],
    { title: { text: 'Kelly: growth rate vs leverage' }, xaxis: { title: { text: 'Leverage' } }, yaxis: pctAxis, shapes: [{ type: 'line', x0: k.full_kelly, x1: k.full_kelly, yref: 'paper', y0: 0, y1: 1, line: { color: '#2bd47d', dash: 'dash' } }] });
}, { sym: 1 });

// ================================================================== PAIR
reg('PAIR', 'Quant Lab', 'Pairs trading: cointegration, hedge ratio, z-score signals, backtest', async (p, _s, args) => {
  const [y0, x0] = args.length >= 2 ? args : ['XOM', 'CVX'];
  p.body.innerHTML = `<form class="form"><label>Long leg (Y)<input name="y" value="${esc(y0)}"></label><label>Hedge leg (X)<input name="x" value="${esc(x0)}"></label>
    <label>Period<select name="period">${opts(['1y', '2y', '3y', '5y', '10y'], '3y')}</select></label><label>Z window<input name="window" type="number" value="60"></label>
    <label>Entry |z|<input name="entry" type="number" step="any" value="2"></label><label>Exit |z|<input name="exit" type="number" step="any" value="0.5"></label>
    <div class="actions"><button class="primary" type="submit">ANALYSE PAIR</button><span class="dim">Ideas: KO/PEP · XOM/CVX · V/MA · GLD/GDX · EWA/EWC · NESN.SW/NOVN.SW</span></div></form><div class="out"></div>`;
  const form = $('form', p.body), out = $('.out', p.body);
  const run = async () => {
    out.innerHTML = '<div class="loading">TESTING COINTEGRATION…</div>';
    const f = formData(form);
    const r = await api(`/api/pairs?y=${encodeURIComponent(f.y)}&x=${encodeURIComponent(f.x)}&period=${f.period}&entry=${f.entry}&exit=${f.exit}&window=${f.window}`); p.setSource(r.source);
    p.setTitle(`PAIR ${r.y}/${r.x}`, 'Pairs trading');
    const ok = r.coint.p_value < 0.05, a = uid(), b = uid(), c = uid();
    out.innerHTML = `<div class="cards">${card('Cointegration p', fmt(r.coint.p_value, 4), ok ? 'pos' : 'neg')}${card('Hedge ratio β', fmt(r.hedge_ratio, 3))}${card('Half-life', r.half_life ? fmt(r.half_life, 1) + 'd' : '∞')}
      ${card('Current z', fmt(r.current_z), Math.abs(r.current_z) > +f.entry ? 'yel' : '')}${card('Return corr', fmt(r.corr, 3))}${card('Strategy Sharpe', fmt(r.stats.Sharpe))}${card('Strategy CAGR', pct(r.stats.CAGR))}${card('Max DD', pct(r.stats['Max Drawdown']), 'neg')}</div>
      <div class="${ok ? '' : 'banner'}">${ok ? `<span class="pos">✓ Engle-Granger test rejects no-cointegration at 5% — the spread log(${r.y}) − ${fmt(r.hedge_ratio, 3)}·log(${r.x}) looks mean-reverting.</span>` : `Not cointegrated at 5% (p=${fmt(r.coint.p_value, 3)}). Spread trades on this pair are statistically unsupported.`}</div>
      <div id="${a}" class="chart"></div><div id="${b}" class="chart"></div><div id="${c}" class="chart short"></div>`;
    const e = +f.entry;
    plot(a, [{ x: r.dates, y: r.norm_y, name: r.y, line: { color: '#fb8b1e' } }, { x: r.dates, y: r.norm_x, name: r.x, line: { color: '#4da3ff' } }], { title: { text: 'Rebased prices' } });
    plot(b, [{ x: r.dates, y: r.z, name: 'Spread z-score', line: { color: '#ffd23f', width: 1 } }, { x: r.dates, y: r.position, name: 'Position', yaxis: 'y2', line: { color: '#2bd47d', width: 1, shape: 'hv' } }],
      { title: { text: 'Z-score & position (+1 = long Y / short βX)' }, yaxis2: { overlaying: 'y', side: 'right', range: [-3, 3], showgrid: false },
        shapes: [e, -e, 0].map(y => ({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, line: { color: y ? '#ff5252' : '#555', dash: 'dot' } })) });
    plot(c, [{ x: r.dates, y: r.equity, name: 'Pair strategy equity', line: { color: '#fb8b1e' } }], { title: { text: `Backtest (${r.n_trades} position changes, 5bps/leg costs, in-sample hedge ratio)` } });
  };
  form.onsubmit = e => { e.preventDefault(); run().catch(err => out.innerHTML = `<div class="err">${esc(err.message)}</div>`); };
  await run();
});

// ================================================================== PCA
reg('PCA', 'Quant Lab', 'Principal component factor analysis of a universe', async (p, _s, args) => {
  const syms = args.length >= 3 ? args : ['XLK', 'XLF', 'XLV', 'XLE', 'XLI', 'XLY', 'XLP', 'XLU', 'XLB', 'TLT', 'GLD'];
  const r = await api(`/api/pca?symbols=${encodeURIComponent(syms.join(','))}&period=2y&k=4`); p.setSource(r.source);
  const a = uid(), b = uid(), c = uid();
  p.body.innerHTML = `<div class="cards">${card('PC1 explains', pct(r.explained[0], 1))}${card('Top 3 explain', pct(r.cumulative[2], 1))}${card('Absorption ratio', fmt(r.absorption_ratio, 3))}${card('Assets', r.symbols.length)}</div>
    <div class="row"><div id="${a}" class="chart"></div><div id="${b}" class="chart"></div></div><div id="${c}" class="chart"></div>
    <p class="dim">PC1 is usually the “market” factor (all loadings same sign). A rising absorption ratio means risk is concentrated in fewer factors — a classic fragility signal.</p>`;
  plot(a, [{ x: r.explained.map((_, i) => 'PC' + (i + 1)), y: r.explained.map(v => v * 100), type: 'bar', name: 'Explained', marker: { color: '#fb8b1e' } },
    { x: r.cumulative.map((_, i) => 'PC' + (i + 1)), y: r.cumulative.map(v => v * 100), name: 'Cumulative', line: { color: '#4da3ff' } }], { title: { text: 'Explained variance %' }, yaxis: pctAxis });
  plot(b, Object.entries(r.loadings).map(([k, v], i) => ({ x: r.symbols, y: v, type: 'bar', name: k, marker: { color: COLORS[i] } })), { title: { text: 'Factor loadings' }, barmode: 'group' });
  plot(c, Object.entries(r.factor_index).map(([k, v], i) => ({ x: r.dates, y: v, name: k, line: { color: COLORS[i], width: 1.2 } })), { title: { text: 'Factor return indices (vol-normalised)' } });
});

// ================================================================== GC / ECO
reg('GC', 'Macro', 'US Treasury yield curve (FRED): today vs 1W/1M/1Y ago', async p => {
  const r = await api('/api/curve'); p.setSource(r.source);
  const a = uid(), cur = r.curves.Current, t = r.tenors, ix = n => cur[t.indexOf(n)];
  p.body.innerHTML = `<div class="cards">${card('2Y', fmt(ix('2Y')) + '%')}${card('10Y', fmt(ix('10Y')) + '%')}${card('30Y', fmt(ix('30Y')) + '%')}
    ${card('2s10s', fmt((ix('10Y') - ix('2Y')) * 100, 0) + 'bp', cls(ix('10Y') - ix('2Y')))}${card('3m10y', fmt((ix('10Y') - ix('3M')) * 100, 0) + 'bp', cls(ix('10Y') - ix('3M')))}${card('5s30s', fmt((ix('30Y') - ix('5Y')) * 100, 0) + 'bp', cls(ix('30Y') - ix('5Y')))}${card('As of', r.asof)}</div>
    <div id="${a}" class="chart tall"></div>` +
    table([{ h: 'Tenor', l: 1, f: i => t[i] }, ...Object.keys(r.curves).map(k => ({ h: k, f: i => fmt(r.curves[k][i], 3) })), { h: 'Δ 1M (bp)', f: i => signed((cur[i] - r.curves['1M ago'][i]) * 100, 0) }, { h: 'Δ 1Y (bp)', f: i => signed((cur[i] - r.curves['1Y ago'][i]) * 100, 0) }], t.map((_, i) => i));
  plot(a, Object.entries(r.curves).map(([k, v], i) => ({ x: t, y: v, name: k, mode: 'lines+markers', line: { color: COLORS[i], width: k === 'Current' ? 2.5 : 1.2, dash: k === 'Current' ? 'solid' : 'dot' } })),
    { title: { text: 'US Treasury par yield curve' }, yaxis: pctAxis, xaxis: { type: 'category' } });
});
reg('ECO', 'Macro', 'Macro dashboard (FRED): rates, inflation, labour, credit spreads', async (p, _s, args) => {
  const ids = args.length ? args : ['FEDFUNDS', 'CPIAUCSL', 'CPILFESL', 'UNRATE', 'PAYEMS', 'T10Y2Y', 'BAMLH0A0HYM2', 'MORTGAGE30US'];
  const r = await api(`/api/eco?ids=${ids.join(',')}&start=2005-01-01`); p.setSource(r.source);
  const divs = ids.map(() => uid());
  p.body.innerHTML = `<p class="dim">Add any FRED series id: <span class="code">ECO DGS10 SOFR VIXCLS</span> · catalogue: ${Object.keys(r.catalog).map(k => `<span class="code">${k}</span>`).join(' ')}</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:6px">${divs.map(d => `<div id="${d}" class="chart short"></div>`).join('')}</div>`;
  ids.forEach((sid, i) => {
    const s = r.series[sid]; if (!s) return;
    plot(divs[i], [{ x: s.dates, y: s.values, name: sid, line: { color: COLORS[i % COLORS.length], width: 1.3 }, fill: 'tozeroy', fillcolor: 'rgba(251,139,30,.05)' }],
      { title: { text: `${s.name}: <b>${fmt(s.last)}</b> (${s.last_date})` }, showlegend: false, margin: { t: 30, l: 40, r: 10 } });
  });
});

// ================================================================== TRADE (paper)
reg('TRADE', 'Portfolio & Trading', 'Paper trading: order ticket, positions, P&L, blotter', async (p, _s, args) => {
  const sym0 = args[0] || '';
  p.body.innerHTML = `<form class="form"><label>Symbol<input name="symbol" value="${esc(sym0)}" required></label><label>Side<select name="side">${opts(['BUY', 'SELL'], 'BUY')}</select></label>
    <label>Quantity<input name="qty" type="number" step="any" value="100" required></label><label>Order type<select name="type">${opts([['market', 'Market'], ['limit', 'Limit'], ['stop', 'Stop']], 'market')}</select></label>
    <label>Limit price<input name="limit_px" type="number" step="any"></label><label>Stop price<input name="stop_px" type="number" step="any"></label>
    <div class="actions"><button class="primary" type="submit">SEND ORDER</button><button type="button" class="danger" data-reset>RESET ACCOUNT</button>
      <span class="dim brokernote">Simulated fills at last price ± 2bps slippage, $1/order. Paper only — no real orders are sent.</span></div></form>
    <div class="msg"></div><div class="acct"></div>`;
  const form = $('form', p.body), acct = $('.acct', p.body), msg = $('.msg', p.body);
  const load = async () => {
    const r = await api('/api/paper'), a = uid();
    p.setSource(r.broker === 'paper' ? null : (r.live ? 'LIVE BROKER' : r.broker + ' paper'));
    const note = $('.brokernote', p.body);
    if (r.broker !== 'paper') note.innerHTML = r.live ? '<b class="neg">LIVE ACCOUNT — orders are sent to the market with real money.</b>' : `Orders go to your <b class="amb">${esc(r.broker)}</b> paper account.`;
    acct.innerHTML = `${r.broker_error ? `<div class="banner">Broker "${esc(r.broker_error)}" — using the local paper broker instead.</div>` : ''}<div class="cards">${card('Equity', '$' + fmt(r.equity, 0))}${card('Cash', '$' + fmt(r.cash, 0))}${card('Total P&L', signed(r.pnl_total, 0))}${card('Return', signed(r.pnl_pct, 2, '%'))}
      ${card('Unrealized', signed(r.unrealized, 0))}${card('Realized', signed(r.realized, 0))}${card('Gross exp.', fmt(r.gross_exposure, 1) + '%')}${card('Net exp.', fmt(r.net_exposure, 1) + '%')}</div>
      <h3>Positions</h3>${r.positions.length ? table([{ h: 'Symbol', l: 1, f: x => `<b class="amb">${x.symbol}</b>` }, { h: 'Qty', f: x => fmt(x.qty, 0) }, { h: 'Avg px', f: x => fmtPx(x.avg_px) }, { h: 'Last', f: x => fmtPx(x.last) },
        { h: 'Mkt value', f: x => fmt(x.mkt_value, 0) }, { h: 'Weight', f: x => fmt(x.weight, 1) + '%' }, { h: 'Unreal. P&L', f: x => signed(x.unrealized, 0) }, { h: '%', f: x => signed(x.unrealized_pct, 2, '%') }, { h: 'Realized', f: x => signed(x.realized, 0) },
        { h: '', f: x => `<button data-close="${x.symbol}" data-q="${x.qty}">CLOSE</button>` }], r.positions) : '<div class="dim">No positions</div>'}
      <h3>Orders</h3>${table([{ h: 'ID', k: 'id' }, { h: 'Time', f: x => x.ts.replace('T', ' ') }, { h: 'Symbol', l: 1, k: 'symbol' }, { h: 'Side', f: x => `<span class="${x.side === 'BUY' ? 'pos' : 'neg'}">${x.side}</span>` }, { h: 'Qty', f: x => fmt(x.qty, 0) },
        { h: 'Type', k: 'type' }, { h: 'Limit/Stop', f: x => fmtPx(x.limit_px ?? x.stop_px) }, { h: 'Status', f: x => `<span class="${x.status === 'FILLED' ? 'pos' : x.status === 'OPEN' ? 'yel' : 'dim'}">${x.status}</span>` }, { h: 'Fill', f: x => fmtPx(x.fill_px) },
        { h: '', f: x => x.status === 'OPEN' ? `<button data-cancel="${x.id}">CANCEL</button>` : '' }], r.orders.slice(0, 30))}
      <div id="${a}" class="chart short"></div>`;
    if (r.equity_curve.length > 1) plot(a, [{ x: r.equity_curve.map(e => e.ts), y: r.equity_curve.map(e => e.equity), line: { color: '#fb8b1e' }, name: 'Equity' }], { title: { text: 'Account equity (snapshots)' }, showlegend: false });
    $$('[data-cancel]', acct).forEach(b => b.onclick = async () => { await fetch('/api/paper/order/' + b.dataset.cancel, { method: 'DELETE' }); load(); });
    $$('[data-close]', acct).forEach(b => b.onclick = async () => { const q = +b.dataset.q; await api('/api/paper/order', { symbol: b.dataset.close, side: q > 0 ? 'SELL' : 'BUY', qty: Math.abs(q) }); load(); });
  };
  form.onsubmit = async e => {
    e.preventDefault(); const f = formData(form);
    if ((await api('/api/broker')).live && !confirm(`LIVE ORDER with real money:\n${f.side} ${f.qty} ${f.symbol.toUpperCase()} (${f.type})\n\nSend it?`)) return;
    try { const r = await api('/api/paper/order', f); msg.innerHTML = `<div class="${r.order.status === 'FILLED' ? 'pos' : 'yel'}">Order #${r.order.id} ${r.order.side} ${r.order.qty} ${r.order.symbol}: ${r.order.status}${r.order.fill_px ? ' @ ' + fmtPx(r.order.fill_px) : ''}</div>`; load(); }
    catch (err) { msg.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  };
  $('[data-reset]', p.body).onclick = async () => { if (confirm('Reset paper account to starting cash? All positions and orders are deleted.')) { await api('/api/paper/reset', {}); load(); } };
  await load(); p.every(20, load);
});
