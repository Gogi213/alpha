// node req_count.js <url> <секунд на вкладку> — запросы к серверу в простое: вкладка «Арбитраж», потом «Здоровье» (после клика), и «пустая» (вкладка браузера скрыта)
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], sec = +(process.argv[3] || 120);
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: 1180, height: 800 } });
  let log = [];
  pg.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api')) log.push(u.pathname) });
  await pg.goto(url); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(1500);
  const count = () => { const c = {}; log.forEach(p => c[p] = (c[p] || 0) + 1); const t = log.length; log = []; return t + ' ' + JSON.stringify(c) };
  log = [];
  await pg.waitForTimeout(sec * 1000);
  console.log('вкладка «Арбитраж»,', sec, 'с в простое:', count());
  // действия: переключатели, сортировки, фильтры, открыть/закрыть график одной и той же строки дважды
  await pg.click('#ctl button[data-hide]'); await pg.click('#ctl button[data-m="out"]'); await pg.click('#ex button:nth-child(1)'); await pg.click('#tab th[data-k="n0"]'); await pg.click('#ex button:nth-child(1)');
  await pg.waitForTimeout(500);
  console.log('10 переключений/сортировок/фильтров:', count());
  await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForTimeout(600); await pg.click('#tab tr.r:nth-child(3)'); await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForTimeout(600);
  console.log('график: открыть, закрыть, открыть ту же строку:', count());
  await pg.click('#tab-h'); log = [];
  await pg.waitForTimeout(sec * 1000);
  console.log('вкладка «Здоровье»,', sec, 'с в простое:', count());
  await pg.click('#tab-a'); await pg.waitForTimeout(500); log = [];
  await b.close();
})();
