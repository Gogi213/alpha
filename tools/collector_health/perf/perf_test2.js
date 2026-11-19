// node perf_test2.js <url> [cpu=1] [прогонов=3] [задержка сети, мс=0]
// Для каждого действия: «JS» — синхронная работа обработчика, «кадр» — от клика до отрисованного кадра (≥ ~16 мс из-за кадра 60 Гц).
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], thr = +(process.argv[3] || 1), runs = +(process.argv[4] || 3), lat = +(process.argv[5] || 0);
const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)] };
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const first = [], js = {}, fr = {}, longs = [];
  for (let run = 0; run < runs; run++) {
    const ctx = await b.newContext({ viewport: { width: 1180, height: 800 } });
    const pg = await ctx.newPage();
    const cdp = await ctx.newCDPSession(pg);
    if (thr > 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: thr });
    if (lat) { await cdp.send('Network.enable'); await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: lat, downloadThroughput: -1, uploadThroughput: -1 }) }
    await pg.addInitScript(() => {
      window.__lt = [];
      try { new PerformanceObserver(l => l.getEntries().forEach(e => window.__lt.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] }) } catch (e) {}
      window.__first = null;
      new MutationObserver(() => { if (window.__first == null && document.querySelector('#tab tr.r')) window.__first = performance.now() }).observe(document, { childList: true, subtree: true });
    });
    await pg.goto(url); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(400);
    first.push(await pg.evaluate(() => {
      const r = performance.getEntriesByType('resource').find(e => e.name.includes('/api/arb') && !e.name.includes('chart'));
      return { firstRow: window.__first, arbEnd: r.responseEnd, size: r.encodedBodySize, dec: r.decodedBodySize, rows: document.querySelectorAll('#tab tr.r').length, dom: document.getElementsByTagName('*').length };
    }));
    const t = async (name, sel) => {
      const [j, f] = await pg.evaluate(async sel => {
        const el = document.querySelector(sel);
        const t0 = performance.now(); el.click(); const j = performance.now() - t0;
        await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
        return [j, performance.now() - t0];
      }, sel);
      (js[name] = js[name] || []).push(j); (fr[name] = fr[name] || []).push(f);
    };
    await t('скрыть пустые (вкл)', '#ctl button[data-hide]'); await t('скрыть пустые (выкл)', '#ctl button[data-hide]');
    await t('счёт: выбросы', '#ctl button[data-m="out"]'); await t('счёт: медиана', '#ctl button[data-m="med"]');
    await t('биржи: без Binance', '#ex button:nth-child(1)'); await t('биржи: все', '#ex button:nth-child(1)'); await t('биржи: без Bybit', '#ex button:nth-child(2)'); await t('биржи: все (2)', '#ex button:nth-child(2)');
    for (const [n, k] of [['монета', 'c'], ['пара', 'p'], ['расхождение', 'size'], ['за 1 ч', 'n0'], ['за 4 ч', 'n1'], ['за 1 сут', 'n3'], ['схождение', 'tau'], ['после комиссий', 'net']]) await t('сорт: ' + n, '#tab th[data-k="' + k + '"]');
    // график: холодный (без наведения) и после наведения 250 мс; конец — холст нарисован (#ld скрыт)
    const chart = async (name, rowSel, hover) => {
      if (hover) { await pg.hover(rowSel); await pg.waitForTimeout(hover) }
      const ms = await pg.evaluate(async sel => {
        const t0 = performance.now(); document.querySelector(sel).click();
        for (;;) { await new Promise(r => requestAnimationFrame(r)); const l = document.getElementById('ld'); if (l && l.hidden) break; if (performance.now() - t0 > 5000) break }
        return performance.now() - t0;
      }, rowSel);
      (fr[name] = fr[name] || []).push(ms); (js[name] = js[name] || []).push(ms);
      await pg.evaluate(sel => document.querySelector(sel).click(), rowSel);   // закрыть
    };
    await chart('график: открыть (холодный, с запросом)', '#tab tr.r:nth-child(5)', 0);
    await chart('график: открыть (после наведения 250 мс)', '#tab tr.r:nth-child(7)', 250);
    await chart('график: открыть ту же строку повторно', '#tab tr.r:nth-child(5)', 0);
    await t('вкладка Здоровье', '#tab-h'); await t('вкладка Арбитраж', '#tab-a');
    await pg.evaluate(() => window.scrollTo(0, 3000)); await pg.waitForTimeout(100);
    const sc = await pg.evaluate(async () => { const t0 = performance.now(); window.scrollTo(0, 9000); await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0))); return performance.now() - t0 });
    (fr['прокрутка на 6000 px'] = fr['прокрутка на 6000 px'] || []).push(sc); (js['прокрутка на 6000 px'] = js['прокрутка на 6000 px'] || []).push(sc);
    longs.push(await pg.evaluate(() => window.__lt));
    await ctx.close();
  }
  console.log('— ' + url + ' · CPU×' + thr + (lat ? ' · задержка сети ' + lat + ' мс' : '') + ' · прогонов ' + runs + ' —');
  const n = first[0];
  console.log('строк в DOM таблицы:', n.rows, '· элементов в DOM:', n.dom, '· /api/arb:', Math.round(n.dec / 1024) + ' КБ распаковано,', Math.round(n.size / 1024) + ' КБ по сети');
  console.log('первая строка: от начала загрузки', Math.round(med(first.map(x => x.firstRow))), 'мс; от конца ответа /api/arb', Math.round(med(first.map(x => x.firstRow - x.arbEnd))), 'мс');
  console.log('действие'.padEnd(46), 'JS, мс   до кадра, мс');
  for (const k of Object.keys(fr)) console.log(k.padEnd(46), med(js[k]).toFixed(1).padStart(6), '  ', Math.round(med(fr[k])).toString().padStart(8));
  const lt = longs.flat();
  console.log('длинных задач (>50 мс):', lt.length / runs, 'на прогон; самая длинная', Math.max(0, ...lt), 'мс');
  await b.close();
})();
