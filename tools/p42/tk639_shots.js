// П-42 TK-639: клик-тест блока «Число сделок за 6 и 12 ч и средний размер сделки» (Эквити → Где мы, «Признак: успешные пробои монеты») на VPS София (node18, playwright-core 1.48.2), НЕ на ПК.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk639_shots.js <url> <каталог выхода> [ширина]
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  await page.goto(base + '#equity/short/where', {waitUntil: 'load'});
  await page.waitForSelector('#feature [data-fround]');
  const R = {url: base, width: W, errors: errs};
  R.round = await page.evaluate(() => {
    const rs = [...document.querySelectorAll('#feature [data-fround]')];
    const i = rs.findIndex(b => b.querySelector('h3').textContent.includes('TK-639'));
    return {rounds: rs.map(b => b.querySelector('h3').textContent.slice(0, 60)), i};
  });
  const ri = R.round.i;
  if (ri < 0) { R.fail = 'нет раунда TK-639'; console.log(JSON.stringify(R)); process.exit(1); }
  const secs = await page.evaluate(ri => [...document.querySelectorAll(`#feature [data-rsec^="${ri}."]`)].map(s => ({k: s.dataset.rsec, fold: s.tagName === 'DETAILS', title: (s.querySelector('summary') || s.querySelector('.wh-tt')).textContent.slice(0, 80), tables: s.querySelectorAll('table').length, shaded: s.querySelectorAll('td[style*="background"]').length, cells: s.querySelectorAll('td').length})), ri);
  R.sections = secs;
  R.overflowClosed = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  await page.locator(`#feature [data-fround="${ri}"]`).scrollIntoViewIfNeeded();
  await page.waitForTimeout(250);
  await page.locator(`#feature [data-fround="${ri}"]`).screenshot({path: `${out}/round639-head.png`});
  await page.locator(`#feature [data-rsec="${ri}.0"]`).screenshot({path: `${out}/round639-h775.png`});
  // свёрнутый раздел o566: закрыт → клик по summary → открыт → таблицы видны → клик → закрыт
  const sel = `#feature details[data-rsec="${ri}.1"]`;
  R.fold = {closed: !(await page.$eval(sel, d => d.open))};
  await page.click(sel + ' > summary'); await page.waitForTimeout(250);
  R.fold.opened = await page.$eval(sel, d => d.open);
  R.fold.visibleTables = await page.$$eval(sel + ' table', ts => ts.filter(t => t.offsetParent !== null).length);
  R.fold.overflowOpen = await page.evaluate(() => ({page: document.documentElement.scrollWidth - innerWidth, tables: Math.max(0, ...[...document.querySelectorAll('#feature table')].map(tb => { const w = tb.parentElement; return w.scrollWidth - w.clientWidth; }))}));
  await page.locator(sel).screenshot({path: `${out}/round639-o566.png`});
  await page.click(sel + ' > summary'); await page.waitForTimeout(150);
  R.fold.closedAgain = !(await page.$eval(sel, d => d.open));
  // клетки «мало» (n < 70) в таблицах h775
  R.few = await page.evaluate(ri => [...document.querySelectorAll(`#feature [data-rsec="${ri}.0"] td`)].filter(td => td.textContent.includes('(мало)')).length, ri);
  // этап TK-639 на «Ход работ»
  await page.click('#toptabs button[data-tab="progress"]'); await page.waitForSelector('#progress .stage'); await page.waitForTimeout(250);
  R.stage = await page.evaluate(() => { const c = document.querySelector('#progress .stage[data-stage="TK-639"]'); return c ? {status: c.dataset.status, badge: c.querySelector('.badge').textContent} : null; });
  R.overflowProgress = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  fs.writeFileSync(`${out}/report639.json`, JSON.stringify(R, null, 1));
  console.log(JSON.stringify(R));
  await browser.close();
  process.exit(errs.length ? 1 : 0);
})();
