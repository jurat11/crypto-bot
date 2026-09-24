// $15 live demo forward test. FROZEN on 2026-09-24. Do not edit the rules.
// Paste into a browser tab opened at https://data-api.binance.vision/api/v3/ping, then run:
//   await loadAll(); JSON.stringify(FWD(START))
const START = Date.UTC(2026, 8, 24, 16); // 2026-09-24 16:00 UTC, first bar of the demo
const FEE = 0.004;                        // 0.4% per trade (realistic small-account fee), slippage 0.05%
window.DATA = window.DATA || {};
async function load(sym, startMs) {
  let out = [], t = startMs;
  while (t < Date.now()) {
    const j = await (await fetch(`/api/v3/klines?symbol=${sym}&interval=1h&startTime=${t}&limit=1000`)).json();
    if (!j.length) break;
    for (const k of j) out.push([k[0], +k[1], +k[2], +k[3], +k[4], +k[5]]);
    t = j[j.length - 1][0] + 3600000;
  }
  return out;
}
async function loadAll() { for (const s of ['BTCUSDT', 'ETHUSDT']) DATA[s] = await load(s, START - 110 * 864e5); return Object.keys(DATA); }

const E = {};
E.ema = (a, n) => { const o = new Array(a.length); const k = 2 / (n + 1); let e = a[0]; for (let i = 0; i < a.length; i++) { e = i ? a[i] * k + e * (1 - k) : a[i]; o[i] = e; } return o; };
E.rsi = (c, n) => { const o = new Array(c.length).fill(50); let g = 0, l = 0; for (let i = 1; i < c.length; i++) { const d = c[i] - c[i - 1]; const up = Math.max(d, 0), dn = Math.max(-d, 0); if (i <= n) { g += up / n; l += dn / n; } else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; } if (i >= n) o[i] = 100 - 100 / (1 + g / (l || 1e-12)); } return o; };
E.sim = function (bars, entryFn, exitFn, { cash = 15, fee = 0.001, slip = 0.0005, t0 = 0, t1 = Infinity } = {}) {
  let inPos = false, qty = 0, entryPx = 0, entryIdx = -1, stake = 0, eq = cash, peak = cash, mdd = 0; const trades = [];
  for (let i = 1; i < bars.length; i++) {
    const t = bars[i][0]; if (t < t0 || t >= t1) continue; const c = bars[i][4];
    if (inPos) { if (exitFn(i, entryIdx, entryPx)) { const px = c * (1 - slip); cash = qty * px * (1 - fee); trades.push({ in: bars[entryIdx][0], out: t, entryPx, exitPx: px, pnl: cash - stake, hour: new Date(bars[entryIdx][0]).getUTCHours(), r: (px / entryPx) * (1 - fee) ** 2 - 1 }); inPos = false; qty = 0; } }
    else if (entryFn(i)) { const px = c * (1 + slip); stake = cash; qty = cash * (1 - fee) / px; cash = 0; entryPx = px; entryIdx = i; inPos = true; }
    eq = inPos ? qty * c : cash; peak = Math.max(peak, eq); mdd = Math.max(mdd, 1 - eq / peak);
  }
  const last = bars.filter(b => b[0] < t1).at(-1); const final = inPos ? qty * last[4] * (1 - slip) * (1 - fee) : cash;
  return { final, trades, mdd, openAtEnd: inPos };
};
// Bot A (article #20): EMA9/21 cross on BTC 1h, entries only at 06 or 20 UTC
E.emaCross = (bars, hours) => { const c = bars.map(b => b[4]); const f = E.ema(c, 9), s = E.ema(c, 21);
  return [i => f[i] > s[i] && f[i - 1] <= s[i - 1] && (!hours || hours.includes(new Date(bars[i][0]).getUTCHours())), i => f[i] < s[i] && f[i - 1] >= s[i - 1]]; };
// Bot B (article #36 "strategy from the internet"): RSI14 buy <30, sell >70, 5% stop, BTC 1h
E.rsiBot = bars => { const c = bars.map(b => b[4]); const r = E.rsi(c, 14); return [i => r[i] < 30, (i, ei, ep) => r[i] > 70 || c[i] < ep * 0.95]; };

function FWD(START) {
  const res = {}; const cut = s => DATA[s].filter(b => b[0] >= START - 40 * 864e5);
  { const d = cut('BTCUSDT'); const [en, ex] = E.emaCross(d, [6, 20]); const r = E.sim(d, en, ex, { fee: FEE, t0: START }); res.A_bestHour = { final: +r.final.toFixed(2), trades: r.trades.length, open: r.openAtEnd, mdd: +r.mdd.toFixed(3) }; }
  { const d = cut('BTCUSDT'); const [en, ex] = E.rsiBot(d); const r = E.sim(d, en, ex, { fee: FEE, t0: START }); res.B_rsi = { final: +r.final.toFixed(2), trades: r.trades.length, open: r.openAtEnd, mdd: +r.mdd.toFixed(3) }; }
  // Bot C (this repo): SMA50 daily trend + 40% vol dial, $7.50 BTC sleeve + $7.50 ETH sleeve
  { let total = 0; const trades = []; for (const s of ['BTCUSDT', 'ETHUSDT']) {
      const h = DATA[s].filter(b => b[0] >= START - 100 * 864e5); const m = new Map();
      for (const k of h) { const dd = Math.floor(k[0] / 864e5); let x = m.get(dd); if (!x) { x = [dd * 864e5, 0, 0, 0, k[4]]; m.set(dd, x); } else x[4] = k[4]; }
      const days = [...m.values()]; const closes = days.map(x => x[4]);
      const expAt = i => { const sm = closes.slice(i - 49, i + 1).reduce((a, b) => a + b, 0) / 50; const lr = []; for (let j = i - 29; j <= i; j++) lr.push(Math.log(closes[j] / closes[j - 1])); const mu = lr.reduce((a, b) => a + b, 0) / 30; const vol = Math.sqrt(lr.reduce((a, b) => a + (b - mu) ** 2, 0) / 29 * 365); return closes[i] > sm ? Math.round(Math.min(1, 0.4 / vol) * 10) / 10 : 0; };
      const startBar = h.find(b => b[0] >= START); const idx = days.findIndex(x => x[0] + 864e5 > START) - 1; let cash = 7.5, qty = 0;
      const trade = (target, px, when) => { const want = target * (cash + qty * px) - qty * px; if (Math.abs(want) < 0.5) return; if (want > 0) { cash -= want; qty += want * (1 - FEE) / px; } else { const q = -want / px; qty -= q; cash += q * px * (1 - FEE); } trades.push({ s, when: new Date(when).toISOString().slice(0, 16), target, px: +px.toFixed(2) }); };
      trade(expAt(idx), startBar[1], startBar[0]);
      for (let i = idx + 1; i < days.length - 1; i++) trade(expAt(i), closes[i], days[i][0] + 864e5);
      total += cash + qty * h.at(-1)[4]; }
    res.C_sma = { final: +total.toFixed(2), trades };
  }
  const b0 = DATA.BTCUSDT.find(b => b[0] >= START), e0 = DATA.ETHUSDT.find(b => b[0] >= START);
  res.hold5050 = { final: +(7.5 * (1 - FEE) * DATA.BTCUSDT.at(-1)[4] / b0[1] + 7.5 * (1 - FEE) * DATA.ETHUSDT.at(-1)[4] / e0[1]).toFixed(2) };
  res.window = { from: new Date(START).toISOString(), to: new Date(DATA.BTCUSDT.at(-1)[0] + 36e5).toISOString(), btc: [b0[1], DATA.BTCUSDT.at(-1)[4]], eth: [e0[1], DATA.ETHUSDT.at(-1)[4]] };
  return res;
}
