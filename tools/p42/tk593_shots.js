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
