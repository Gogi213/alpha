// node shot_chart.js <url> <ключ c|a|b> <файл.png> — открыть график пары на живой странице и снять его
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: 1180, height: 900 } });
  await pg.goto(process.argv[2]); await pg.waitForSelector('#tab tr.r');
  await pg.evaluate(k => { const a = window._arb; a.st.open = k; a.refresh(); }, process.argv[3]);
  await pg.waitForFunction(() => { const l = document.getElementById('ld'); return l && l.hidden }, null, { timeout: 8000 });
  await pg.evaluate(() => document.querySelector('#cv').scrollIntoView({ block: 'center' }));
  await pg.waitForTimeout(400);
  const box = await pg.evaluate(() => { const r = document.querySelector('tr.ch').getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height } });
  await pg.screenshot({ path: process.argv[4], clip: { x: box.x, y: Math.max(0, box.y - 50), width: box.width, height: box.height + 50 } });
  await b.close();
})();
