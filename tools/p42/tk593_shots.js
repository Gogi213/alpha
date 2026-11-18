// П-42 TK-593/TK-601: клик-тест страницы CatBoost на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk593_shots.js <url> <каталог выхода> [ширина]
// Делает: ошибки консоли, верхние вкладки и хеш-маршруты, снимки «Ход работ» / «Цели TK-598» / «Эквити» (шорт, лонг, шорт+лонг; наведение на оба графика),
// затем прежний поток вкладки «Модель TK-592»; выгружает DOM-метки (dom_progress.json, dom_equity.json, dom_<side>.json) для tk593_check.py.
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
  await page.goto(base, {waitUntil: 'load'});
  const report = {url: base, width: W, errors: errs, sides: {}, tabs: {}, hash: {}, equity: {}};
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const state = () => page.evaluate(() => ({
    hash: location.hash, selected: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab),
    visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id)
  }));
  const top = async name => { await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(350); await page.screenshot({path: `${out}/${name}.png`}); };

  // ── верхние вкладки: по умолчанию «Ход работ», клик по каждой, хеш
  report.tabs.default = await state();
  report.tabs.labels = await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.dataset.tab + '|' + b.innerText));
  await top('progress-top');
  await page.locator('#progress').screenshot({path: `${out}/progress-block.png`});
  const pdom = await page.evaluate(() => {
    const t = (r, s) => { const n = r.querySelector(s); return n ? n.textContent : null; };
    const stages = [...document.querySelectorAll('#progress .stage')].map(c => ({
      id: c.dataset.stage, status: c.dataset.status, badge: t(c, '.badge'), answers: t(c, '.stage-q'), next: t(c, '.stage-next'),
      steps: [...c.querySelectorAll('.steps li')].map(li => li.dataset.status), numbers: [...c.querySelectorAll('.nums li')].map(li => li.textContent)
    }));
    return {stages, headline: t(document, '#progress .prog-head')};
  });
  report.tabs.progress = {overflowX: await overflow()};

  await page.click('#toptabs button[data-tab="targets"]');
  await page.waitForSelector('#targets-box .tg');
  report.tabs.targets = {state: await state(), overflowX: await overflow()};
  await top('targets-top');
  await page.locator('#targets-box').screenshot({path: `${out}/targets-block.png`});
  const tdom = await page.evaluate(() => {
    const t = (r, s) => { const n = r.querySelector(s); return n ? n.textContent : null; };
    const targets = [...document.querySelectorAll('#targets-box .tg tbody tr')].map(r => ({id: r.dataset.target, long: r.dataset.long, short: r.dataset.short, what: r.querySelector('td.what').textContent, res: [...r.querySelectorAll('td.res .res-line')].map(d => d.dataset.side + '|' + d.querySelector('.res-t').textContent), none: !!r.querySelector('td.res .res-none')}));
    const w = document.querySelector('#targets-box .tg-wrap');
    return {targets, total_long: t(document, '#targets-box [data-f="total_long"]'), total_short: t(document, '#targets-box [data-f="total_short"]'), tableOverflow: w.scrollWidth - w.clientWidth};
  });
  fs.writeFileSync(`${out}/dom_progress.json`, JSON.stringify({...pdom, ...tdom, overflowX: report.tabs.targets.overflowX}));
  report.progress = {stages: pdom.stages.length, targets: tdom.targets.length, tableOverflow: tdom.tableOverflow};

  // ── «Эквити»: три режима, оба графика, наведение, таблицы
  await page.click('#toptabs button[data-tab="equity"]');
  await page.waitForSelector('#eq-view svg');
  report.tabs.equity = {state: await state()};
  const eqdom = {};
  for (const side of ['short', 'long', 'both']) {
    await page.click(`#eq-tabs button[data-side="${side}"]`);
    await page.waitForSelector('#eq-view table.eqt');
    const info = {hash: (await state()).hash, selected: await page.getAttribute(`#eq-tabs button[data-side="${side}"]`, 'aria-selected'), overflowX: await overflow(), desc: await page.innerText('#eq-desc')};
    await top(`equity-${side}-top`);
    await page.locator('#equity').screenshot({path: `${out}/equity-${side}.png`});
    for (const ch of ['ja', 'fit']) await page.locator(`.eq-card[data-eqchart="${ch}"]`).screenshot({path: `${out}/equity-${side}-${ch}.png`});
    // наведение: три точки по ширине графика, текст подсказки
    info.hover = {};
    for (const ch of ['ja', 'fit']) {
      const loc = page.locator(`rect[data-chart="eq.${ch}.hover"]`);
      await loc.scrollIntoViewIfNeeded();
      const b = await loc.boundingBox();
      info.hover[ch] = [];
      for (const f of [0.2, 0.55, 0.9]) {
        await page.mouse.move(b.x + b.width * f, b.y + b.height / 2);
        await page.waitForTimeout(150);
        const r = await page.evaluate(() => { const t = document.getElementById('tip'); return {shown: t.style.display === 'block', text: t.innerText.replace(/\n/g, ' | '), dots: [...document.querySelectorAll('#eq-view circle')].filter(c => c.getAttribute('visibility') === 'visible').length}; });
        info.hover[ch].push(r);
        if (f === 0.55) await page.screenshot({path: `${out}/equity-${side}-${ch}-hover.png`});
      }
      await page.mouse.move(2, 2);
      await page.waitForTimeout(100);
      info.hover[ch].push({after_leave_shown: await page.evaluate(() => document.getElementById('tip').style.display === 'block')});
    }
    eqdom[side] = await page.evaluate(() => {
      const r = {};
      document.querySelectorAll('table.eqt').forEach(tb => {
        const k = tb.dataset.eqchart + '|' + tb.dataset.eqside;
        r[k] = {};
        tb.querySelectorAll('tbody tr[data-line]').forEach(tr => { r[k][tr.dataset.line] = Object.fromEntries([...tr.querySelectorAll('td[data-f]')].map(td => [td.dataset.f, td.innerText.trim()])); });
      });
      const fin = {};
      document.querySelectorAll('path[data-final]').forEach(p => { fin[p.dataset.chart] = +p.dataset.final; });
      const paths = [...document.querySelectorAll('#eq-view path[data-chart]')].map(p => ({chart: p.dataset.chart, len: p.getAttribute('d').length, stroke: p.getAttribute('stroke'), w: p.getAttribute('stroke-width')}));
      const charts = [...document.querySelectorAll('#eq-view .eq-card')].map(c => c.dataset.eqchart);
      return {tables: r, finals: fin, paths, charts};
    });
    report.equity[side] = info;
  }
  fs.writeFileSync(`${out}/dom_equity.json`, JSON.stringify(eqdom));
  // хеш-маршруты
  for (const h of ['#equity/long', '#equity/both', '#targets', '#short', '#model/long', '#progress']) {
    await page.goto(base + h, {waitUntil: 'load'});
    await page.waitForTimeout(200);
    const st = await state();
    report.hash[h] = {...st, eq: await page.evaluate(() => { const b = document.querySelector('#eq-tabs button[aria-selected="true"]'); return b ? b.dataset.side : null; }), model: await page.evaluate(() => { const b = document.querySelector('#tabs button[aria-selected="true"]'); return b ? b.dataset.side : null; })};
  }
  await page.goto(base, {waitUntil: 'load'});

  // ── «Модель TK-592» (прежний поток)
  await page.click('#toptabs button[data-tab="model"]');
  report.tabs.model = await state();
  for (const side of ['long', 'short']) {
    await page.click(`#tabs button[data-side="${side}"]`);
    await page.waitForSelector('#sec-sum');
    const sel = await page.getAttribute(`#tabs button[data-side="${side}"]`, 'aria-selected');
    await top(`${side}-top`);
    const info = {selected: sel, overflowX: await overflow()};
    const navN = await page.locator('#nav button').count();
    info.nav = [];
    for (let i = 0; i < navN; i++) {
      const b = page.locator('#nav button').nth(i);
      const t = await b.innerText();
      await b.click();
      await page.waitForTimeout(350);
      const sid = ['sum', 'roc', 'lift', 'calib', 'imp', 'lc', 'over', 'strata-day', 'u1r'][i];
      const tp = await page.evaluate(id => document.getElementById('sec-' + id).getBoundingClientRect().top, sid);
      info.nav.push({t, top: Math.round(tp)});
    }
    const ids = await page.evaluate(() => [...document.querySelectorAll('section')].map(s => s.id));
    for (const id of ids) await page.locator('#' + id).screenshot({path: `${out}/${side}-${id}.png`});
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
  await page.click('#tabs button[data-side="long"]');
  report.back_to_long = await page.getAttribute('#tabs button[data-side="long"]', 'aria-selected');
  fs.writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 1));
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
})();
