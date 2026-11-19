// П-42 TK-624: клик-тест блока «Где мы» (вкладка «Ход работ») и вида «Базы» (вкладка «Эквити») страницы :8090 на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk624_shots.js <url> <каталог выхода> [ширина]
// Выгружает DOM-метки (dom_where.json, dom_bases.json, report624.json) для tk624_check.py и снимки: where-top, where-block, progress-groups, bases-<сторона>, bases-<сторона>-hover.
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
  const R = {url: base, width: W, errors: errs, where: {}, bases: {}, hash: {}};
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const state = () => page.evaluate(() => ({
    hash: location.hash, selected: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab),
    visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id),
    view: (() => { const b = document.querySelector('#eq-views button[aria-selected="true"]'); return b ? b.dataset.view : null; })(),
    side: (() => { const b = document.querySelector('#eq-tabs button[aria-selected="true"]'); return b ? b.dataset.side : null; })()
  }));
  const top = async name => { await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(350); await page.screenshot({path: `${out}/${name}.png`}); };
  const tipAt = async (sel, f) => {
    await page.locator(sel).first().scrollIntoViewIfNeeded(); await page.waitForTimeout(100);
    const b = await page.locator(sel).first().boundingBox();
    await page.mouse.move(b.x + b.width * f, b.y + b.height / 2); await page.waitForTimeout(150);
    return page.evaluate(() => { const t = document.getElementById('tip'); return {shown: t.style.display === 'block', text: t.innerText.replace(/\n/g, ' | ')}; });
  };

  // ── «Ход работ»: только этапы и ссылка на «Эквити»
  await page.goto(base, {waitUntil: 'load'});
  R.default = await state();
  await page.click('#toptabs button[data-tab="progress"]');
  await page.waitForSelector('#progress .stage');
  await page.waitForTimeout(300);
  R.where.progressState = await state();
  R.where.progressOverflowX = await overflow();
  R.where.progressMoney = await page.evaluate(() => ({where: !!document.querySelector('#progress #where'), tables: document.querySelectorAll('#progress table').length, svg: document.querySelectorAll('#progress svg').length}));
  await top('progress-top');
  await page.locator('#progress').screenshot({path: `${out}/progress-groups.png`});
  const pdom = await page.evaluate(() => {
    const t = (r, s) => { const n = r.querySelector(s); return n ? n.textContent : null; };
    const groups = [...document.querySelectorAll('#progress .stage-group, #progress .stages')].map(n => n.classList.contains('stage-group') ? {g: n.textContent} : {ids: [...n.querySelectorAll('.stage')].map(s => s.dataset.stage)});
    return {groups, link: document.querySelector('#progress a[data-wlink]').getAttribute('href'), linkText: document.querySelector('#progress [data-wlink-box]').textContent,
      stages: [...document.querySelectorAll('#progress .stage')].map(c => ({id: c.dataset.stage, status: c.dataset.status, badge: t(c, '.badge'), answers: t(c, '.stage-q'), next: t(c, '.stage-next'),
        steps: [...c.querySelectorAll('.steps li')].map(li => li.dataset.status), numbers: [...c.querySelectorAll('.nums li')].map(li => li.textContent)})),
      headline: t(document, '#progress .prog-head')};
  });
  // ── ссылка → «Эквити» · «Где мы»
  await page.click('#progress a[data-wlink]');
  await page.waitForSelector('#where .wh-box');
  await page.waitForTimeout(300);
  R.where.link = await state();
  R.where.tabsHidden = await page.getAttribute('#eq-tabs', 'hidden') !== null;
  R.where.overflowX = await overflow();
  R.where.desc = await page.innerText('#eq-desc');
  await top('where-top');
  await page.locator('#where').screenshot({path: `${out}/where-block.png`});
  await page.locator('#feature').screenshot({path: `${out}/feature-block.png`});
  const wdom = await page.evaluate(() => {
    const t = (r, s) => { const n = r.querySelector(s); return n ? n.textContent : null; };
    const w = document.getElementById('where');
    const row = r => ({id: r.dataset.wline || r.dataset.uni, name: r.querySelector('td').textContent, cells: Object.fromEntries([...r.querySelectorAll('td[data-f]')].map(td => [td.dataset.f, td.textContent]))});
    const boxes = [...w.querySelectorAll('.wh-box')].map(b => ({k: b.dataset.wbox, title: t(b, 'h3'), items: [...b.querySelectorAll('li')].map(li => li.textContent)}));
    const paths = [...w.querySelectorAll('svg path[data-chart]')].map(p => ({chart: p.dataset.chart, final: +p.dataset.final, stroke: p.getAttribute('stroke')}));
    const wraps = [...w.querySelectorAll('table')].map(tb => tb.parentElement.scrollWidth - tb.parentElement.clientWidth);
    const f = document.getElementById('feature');
    return {title: t(w, '.card-h b'), lead: t(w, '.wh-lead'), table_title: t(w, '.wh-tt'), table_note: t(w, '.wh-tn'), lines: [...w.querySelectorAll('table[data-wtable] tbody tr')].map(row), boxes, paths, tableOverflow: Math.max(...wraps),
      universe: {title: [...w.querySelectorAll('.wh-tt')][1] ? [...w.querySelectorAll('.wh-tt')][1].textContent : null, note: [...w.querySelectorAll('.wh-tn')][1] ? [...w.querySelectorAll('.wh-tn')][1].textContent : null,
        tables: [...w.querySelectorAll('table[data-utable]')].map(tb => ({side: tb.dataset.utable, rows: [...tb.querySelectorAll('tbody tr')].map(row)}))},
      legend: [...w.querySelectorAll('.legend span')].map(s => s.textContent), link: w.querySelector('a[data-wlink]').getAttribute('href'),
      feature: {title: t(f, '.card-h b'), badge: t(f, '.badge'), lead: t(f, '.wh-lead'), box: t(f, '[data-fbox="current"] h3'), text: t(f, '[data-fbox="current"] .ft-st'), tables: f.querySelectorAll('table').length}};
  });
  R.where.hover = [];
  for (const f of [0.2, 0.55, 0.9]) R.where.hover.push(await tipAt('#where svg rect[data-chart="wh.hover"]', f));
  await page.screenshot({path: `${out}/where-hover.png`});
  await page.mouse.move(2, 2);
  fs.writeFileSync(`${out}/dom_where.json`, JSON.stringify({...wdom, progress: pdom}));

  // ── «Признаки» · «Пробиваемость монеты» (TK-604 простыми словами)
  await page.goto(base + '#features/breakability', {waitUntil: 'load'});
  await page.waitForSelector('#ft-breakability');
  await page.waitForTimeout(400);
  R.features = await page.evaluate(() => { const s = document.getElementById('ft-breakability'); return {text: s.innerText, overflow: document.documentElement.scrollWidth - innerWidth}; });
  await page.locator('#ft-breakability').screenshot({path: `${out}/features-breakability.png`});
  await page.goto(base + '#progress', {waitUntil: 'load'});
  await page.click('#progress a[data-wlink]');
  await page.waitForSelector('#where .wh-box');

  // ── вид «Базы»: три стороны
  const bdom = {};
  await page.click('#eq-views button[data-view="bases"]');
  await page.waitForSelector('.eq-card[data-bases]');
  for (const side of ['short', 'long', 'both']) {
    await page.click(`#eq-tabs button[data-side="${side}"]`);
    await page.waitForSelector(`.eq-card[data-bases="${side}"]`);
    await page.waitForTimeout(200);
    const info = {state: await state(), overflowX: await overflow(), desc: await page.innerText('#eq-desc'), sub: await page.innerText('#eq-sub'), cmpHidden: await page.getAttribute('#eq-cmp', 'hidden') !== null};
    await top(`bases-${side}-top`);
    await page.locator('#equity').screenshot({path: `${out}/bases-${side}.png`});
    info.hover = [];
    for (const f of [0.2, 0.55, 0.9]) info.hover.push(await tipAt('.eq-card[data-bases] svg rect[data-chart="bs.hover"]', f));
    await page.screenshot({path: `${out}/bases-${side}-hover.png`});
    await page.mouse.move(2, 2);
    info.dom = await page.evaluate(() => {
      const c = document.querySelector('.eq-card[data-bases]');
      const tables = [...c.querySelectorAll('table[data-bsline]')].map(tb => ({line: tb.dataset.bsline, rows: [...tb.querySelectorAll('tbody tr')].map(r => ({row: r.dataset.bsrow, name: r.querySelector('td').textContent, cells: Object.fromEntries([...r.querySelectorAll('td[data-f]')].map(td => [td.dataset.f, td.textContent]))}))}));
      const paths = [...c.querySelectorAll('svg path[data-chart]')].map(p => ({chart: p.dataset.chart, final: +p.dataset.final, stroke: p.getAttribute('stroke')}));
      const wraps = [...c.querySelectorAll('table[data-bsline]')].map(tb => tb.parentElement.scrollWidth - tb.parentElement.clientWidth);
      return {tables, paths, tableOverflow: Math.max(...wraps), legend: [...c.querySelectorAll('.legend span')].map(s => s.textContent), note: c.querySelector('.eq-note').textContent, head: c.querySelector('.card-h').innerText};
    });
    bdom[side] = info;
  }
  // старые виды не сломаны
  await page.click('#eq-views button[data-view="curve"]');
  await page.waitForSelector('#eq-view table.eqt[data-eqchart]');
  R.bases.backCurve = {...(await state()), cards: await page.evaluate(() => [...document.querySelectorAll('#eq-view .eq-card')].map(c => c.dataset.eqchart)), cmpHidden: await page.getAttribute('#eq-cmp', 'hidden') !== null, sub: await page.innerText('#eq-sub')};
  await page.click('#eq-views button[data-view="shape"]');
  await page.waitForSelector('#eq-view table.eqt[data-shchart]');
  R.bases.backShape = {...(await state()), cards: await page.evaluate(() => [...document.querySelectorAll('#eq-view .eq-card')].map(c => c.dataset.shchart))};
  await page.click('#eq-views button[data-view="bases"]');
  await page.waitForSelector('.eq-card[data-bases]');
  R.bases.toBases = await state();
  fs.writeFileSync(`${out}/dom_bases.json`, JSON.stringify(bdom));

  // ── хеши
  for (const h of ['#equity/both/bases', '#equity/long/bases', '#equity/short', '#equity/short/where', '#progress']) {
    await page.goto(base + h, {waitUntil: 'load'});
    if (h.endsWith('bases')) await page.waitForSelector('.eq-card[data-bases]');
    if (h.endsWith('where')) await page.waitForSelector('#where .wh-box');
    if (h === '#progress') await page.waitForSelector('#progress .stage');
    R.hash[h] = {...(await state()), bases: await page.evaluate(() => { const c = document.querySelector('.eq-card[data-bases]'); return c ? c.dataset.bases : null; }), where: await page.evaluate(() => !!document.querySelector('#progress #where'))};
  }
  R.errorsFinal = errs.slice();
  fs.writeFileSync(`${out}/report624.json`, JSON.stringify(R, null, 1));
  console.log(JSON.stringify({errors: errs, where: {overflowX: R.where.overflowX, tableOverflow: wdom.tableOverflow}, bases: Object.fromEntries(Object.entries(bdom).map(([k, v]) => [k, {overflowX: v.overflowX, tableOverflow: v.dom.tableOverflow}])), hash: R.hash}, null, 1));
  await browser.close();
})();
