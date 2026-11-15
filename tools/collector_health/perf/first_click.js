// node first_click.js <url> [cpu=1] [прогонов=5] [пауза после загрузки, мс=1500]
// Для каждого действия — свежая страница: загрузка, пауза, ОДИН клик. «JS» — синхронная часть обработчика, «кадр» — до отрисованного кадра.
// Так видна цена «первого раза» (раскладка, шрифты, JIT), которую медиана по подряд идущим действиям скрывает.
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], thr = +(process.argv[3] || 1), runs = +(process.argv[4] || 5), wait = +(process.argv[5] || 1500);
const ACTIONS = [
  ['счёт: выбросы', '#ctl button[data-m="out"]'],
  ['скрыть пустые', '#ctl button[data-hide]'],
  ['пара: Binance→BingX', '#flt button:nth-child(2)'],
  ['сорт: за 1 ч', '#tab th[data-k="n0"]'],
  ['сорт: монета', '#tab th[data-k="c"]'],
  ['график: открыть строку', '#tab tr.r:nth-child(5)'],
  ['вкладка Здоровье', '#tab-h'],
];
const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)] };
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  console.log('— ' + url + ' · CPU×' + thr + ' · пауза ' + wait + ' мс · прогонов ' + runs + ' —');
  console.log('действие (первый клик после загрузки)'.padEnd(40), 'JS мин/мед/макс'.padEnd(20), 'до кадра мин/мед/макс');
  for (const [name, sel] of ACTIONS) {
    const js = [], fr = [];
    for (let r = 0; r < runs; r++) {
      const ctx = await b.newContext({ viewport: { width: 1180, height: 800 } });
      const pg = await ctx.newPage();
      if (thr > 1) { const cdp = await ctx.newCDPSession(pg); await cdp.send('Emulation.setCPUThrottlingRate', { rate: thr }) }
      await pg.goto(url); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(wait);
      const [j, f] = await pg.evaluate(async sel => {
        const el = document.querySelector(sel);
        const t0 = performance.now(); el.click(); const j = performance.now() - t0;
        await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
        return [j, performance.now() - t0];
      }, sel);
      js.push(j); fr.push(f);
      await ctx.close();
    }
    const f1 = x => x.toFixed(0).padStart(4);
    console.log(name.padEnd(40), `${f1(Math.min(...js))} /${f1(med(js))} /${f1(Math.max(...js))}`.padEnd(20), `${f1(Math.min(...fr))} /${f1(med(fr))} /${f1(Math.max(...fr))}`);
  }
  await b.close();
})();
