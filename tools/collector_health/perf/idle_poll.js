// node idle_poll.js <url> <секунд> — вкладка «Арбитраж» в простое: когда уходят запросы к серверу и когда меняется метка «данные по ЧЧ:ММ»
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], sec = +(process.argv[3] || 600);
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: 1180, height: 800 } });
  const t0 = Date.now(), ts = () => ((Date.now() - t0) / 1000).toFixed(0).padStart(4);
  pg.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api')) console.log(ts(), 's', u.pathname, r.status()) });
  await pg.goto(url); await pg.waitForSelector('#tab tr.r');
  let last = '';
  for (let i = 0; i < sec; i += 5) {
    const t = await pg.evaluate(() => (document.querySelector('#ctl .upd') || {}).textContent || '');
    if (t !== last) { console.log(ts(), 's метка:', t); last = t }
    await pg.waitForTimeout(5000);
  }
  await b.close();
})();
