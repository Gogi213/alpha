// П-42 TK-593: клик-тест страницы CatBoost на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk593_shots.js <url> <каталог выхода> [ширина]
// Делает: ошибки консоли, клик по вкладкам и разделам навигации, наведение на метки, снимок каждого раздела, выгрузку DOM-меток (dom_<side>.json) для сверки с источниками.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  await page.goto(url, {waitUntil: 'load'});
  const report = {url, width: W, errors: errs, sides: {}};
  // TK-601: блок «Ход работ» — снимок верха страницы и блока целиком, выгрузка текстов в dom_progress.json для сверки с progress.json
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await page.screenshot({path: `${out}/progress-top.png`});
  await page.locator('#progress').screenshot({path: `${out}/progress-block.png`});
  const pdom = await page.evaluate(() => {
    const t = (r, s) => { const n = r.querySelector(s); return n ? n.textContent : null; };
    const stages = [...document.querySelectorAll('#progress .stage')].map(c => ({
      id: c.dataset.stage, status: c.dataset.status, badge: t(c, '.badge'), answers: t(c, '.stage-q'), next: t(c, '.stage-next'),
      steps: [...c.querySelectorAll('.steps li')].map(li => li.dataset.status), numbers: [...c.querySelectorAll('.nums li')].map(li => li.textContent)
    }));
    const targets = [...document.querySelectorAll('#progress .tg tbody tr')].map(r => ({id: r.dataset.target, long: r.dataset.long, short: r.dataset.short, what: r.querySelector('td.what').textContent}));
    return {stages, targets, total_long: t(document, '#progress [data-f="total_long"]'), total_short: t(document, '#progress [data-f="total_short"]'), headline: t(document, '#progress .prog-head'),
      overflowX: document.documentElement.scrollWidth - innerWidth, tableOverflow: (() => { const w = document.querySelector('#progress .tg-wrap'); return w.scrollWidth - w.clientWidth; })()};
  });
  fs.writeFileSync(`${out}/dom_progress.json`, JSON.stringify(pdom));
  report.progress = {stages: pdom.stages.length, targets: pdom.targets.length, overflowX: pdom.overflowX, tableOverflow: pdom.tableOverflow};
  for (const side of ['long', 'short']) {
    await page.click(`#tabs button[data-side="${side}"]`);
    await page.waitForSelector('#sec-sum');
    const sel = await page.getAttribute(`#tabs button[data-side="${side}"]`, 'aria-selected');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(500);
    await page.screenshot({path: `${out}/${side}-top.png`});
    const info = {selected: sel, overflowX: await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)};
    // навигация по разделам
    const navN = await page.locator('#nav button').count();
    info.nav = [];
    for (let i = 0; i < navN; i++) {
      const b = page.locator('#nav button').nth(i);
      const t = await b.innerText();
      await b.click();
      await page.waitForTimeout(350);
      const sid = ['sum', 'roc', 'lift', 'calib', 'imp', 'lc', 'over', 'strata-day', 'u1r'][i];
      const top = await page.evaluate(id => document.getElementById('sec-' + id).getBoundingClientRect().top, sid);
      info.nav.push({t, top: Math.round(top)});
    }
    // снимки разделов
    const ids = await page.evaluate(() => [...document.querySelectorAll('section')].map(s => s.id));
    for (const id of ids) {
      await page.locator('#' + id).screenshot({path: `${out}/${side}-${id}.png`});
    }
    // наведение: ROC, дециль, страта суток
    const hov = async (css, name) => {
      const loc = page.locator(css).first();
      await loc.scrollIntoViewIfNeeded();
      const b = await loc.boundingBox();
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
      await page.waitForTimeout(150);
      const shown = await page.evaluate(() => { const t = document.getElementById('tip'); return t.style.display === 'block' ? t.innerText.replace(/\n/g, ' | ') : null; });
      info['hover_' + name] = shown;
      if (name === 'roc') await page.screenshot({path: `${out}/${side}-hover-roc.png`});
      await page.mouse.move(2, 2);
    };
    await hov('#sec-roc svg[data-chart="roc"] rect', 'roc');
    await hov('#sec-lift svg[data-chart="decile"] rect[fill="transparent"]', 'decile');
    await hov('#sec-strata-day svg[data-chart="strata.day"] rect[fill="transparent"]', 'day');
    // выгрузка меток
    const dom = await page.evaluate(() => {
      const r = {};
      document.querySelectorAll('.card[data-dir]').forEach(card => {
        const dir = card.dataset.dir;
        card.querySelectorAll('[data-chart]').forEach(n => {
          const k = dir + '|' + n.getAttribute('data-chart');
          (r[k] = r[k] || []).push(Object.fromEntries([...n.attributes].filter(a => a.name.startsWith('data-') && a.name !== 'data-chart').map(a => [a.name.slice(5), a.value])));
        });
      });
      const tbl = {};
      document.querySelectorAll('#sec-sum tbody tr, #sec-u1r tbody tr, #sec-over tbody tr').forEach(tr => {
        const sec = tr.closest('section').id, card = tr.closest('.card[data-dir]');
        const key = sec + '|' + (card ? card.dataset.dir + '|' : '') + (tr.dataset.dir || tr.dataset.model);
        tbl[key] = Object.fromEntries([...tr.querySelectorAll('[data-f]')].map(td => [td.dataset.f, td.innerText.trim()]));
      });
      const verdict = [...document.querySelectorAll('#sec-sum .verd')].map(v => v.innerText.trim());
      return {marks: r, tables: tbl, verdict};
    });
    fs.writeFileSync(`${out}/dom_${side}.json`, JSON.stringify(dom));
    report.sides[side] = info;
  }
  // возврат на лонг и проверка, что вкладка переключается обратно
  await page.click('#tabs button[data-side="long"]');
  report.back_to_long = await page.getAttribute('#tabs button[data-side="long"]', 'aria-selected');
  fs.writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 1));
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
})();
