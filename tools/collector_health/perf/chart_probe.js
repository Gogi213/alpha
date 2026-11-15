// node chart_probe.js <url> [cpu=1] — из чего складывается открытие графика: запрос, первая раскладка, первая отрисовка холста
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], thr = +(process.argv[3] || 1), warm = process.argv[4] === 'warm';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  for (let run = 0; run < 4; run++) {
    const ctx = await b.newContext({ viewport: { width: 1180, height: 800 } });
    const pg = await ctx.newPage();
    if (thr > 1) { const cdp = await ctx.newCDPSession(pg); await cdp.send('Emulation.setCPUThrottlingRate', { rate: thr }) }
    await pg.goto(url); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(1500);
    if (warm) await pg.evaluate(() => window.__warm && window.__warm());
    const r = await pg.evaluate(async () => {
      const out = {}, rows = window._arb.rows, a = window._arb;
      const key = rows[4];
      let t = performance.now();
      const j = await fetch('/api/arb/chart?c=' + encodeURIComponent(key.c) + '&a=' + key.a + '&b=' + key.b).then(x => x.json());
      out.fetch1 = performance.now() - t;
      t = performance.now();
      const key2 = rows[9];
      await fetch('/api/arb/chart?c=' + encodeURIComponent(key2.c) + '&a=' + key2.a + '&b=' + key2.b).then(x => x.json());
      out.fetch2 = performance.now() - t;
      // клик по строке 5 (данные ещё не в кеше страницы): состав — вставка строки графика, раскладка, холст
      const tr = document.querySelector('#tab tr.r:nth-child(5)');
      t = performance.now(); tr.click(); out.clickJS = performance.now() - t;
      return out;
    });
    console.log(JSON.stringify(Object.fromEntries(Object.entries(r).map(([k, v]) => [k, Math.round(v)]))));
    await ctx.close();
  }
  await b.close();
})();
