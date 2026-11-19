// П-42 TK-606: клик-тест вкладки «Признаки» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk606_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; вкладка «Признаки» по клику и по хешу (#features, #features/<набор>); сверка DOM с data/features.json (наборы, статусы,
// цифры, отчёты, строки признаков: имя, «что измеряет», источники, пометка); кнопки навигации (секция встаёт под липкие панели, хеш #features/<id>);
// остальные вкладки живы; повторный заход не дублирует содержимое; горизонтальный перелив; снимки.
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
  const rep = {url: base, width: W, errors: errs, checks: {}, fails: []};
  const C = rep.checks;
  const fail = m => rep.fails.push(m);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const state = () => page.evaluate(() => ({hash: location.hash, selected: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab), visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id)}));
  const settle = async () => { let last = -1, same = 0; for (let k = 0; k < 80 && same < 4; k++) { await page.waitForTimeout(150); const y = await page.evaluate(() => scrollY); same = y === last ? same + 1 : 0; last = y; } };
  await page.goto(base, {waitUntil: 'load'});
  const J = await page.evaluate(async () => (await fetch('data/features.json')).json());
  C.tabs = await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.dataset.tab + '|' + b.innerText));
  C.default = await state();
  if (!C.tabs.includes('features|Признаки')) fail('нет вкладки «Признаки»');
  await page.click('#toptabs button[data-tab="features"]');
  C.afterClick = await state();
  if (C.afterClick.hash !== '#features' || C.afterClick.visible.join() !== 'pane-features' || C.afterClick.selected.join() !== 'features') fail('клик по вкладке: ' + JSON.stringify(C.afterClick));
  const dom = await page.evaluate(() => ({
    title: document.querySelector('#features .prog-h h2').innerText, meta: document.querySelector('#features .prog-h .muted').innerText,
    intro: document.querySelector('#features .prog-head').innerText, foot: document.querySelector('[data-f=foot]').innerText,
    navs: [...document.querySelectorAll('.ft-bar button')].map(b => b.dataset.go + '|' + b.innerText),
    sets: [...document.querySelectorAll('section.ft-set')].map(s => ({
      id: s.dataset.set, status: s.dataset.status, badge: s.querySelector('[data-f=status]').innerText, title: s.querySelector('.card-h b').innerText, meta: s.querySelector('.ft-meta span').innerText,
      about: s.querySelector('.ft-about').innerText, nums: [...s.querySelectorAll('.nums li')].map(l => l.innerText), nonum: s.querySelector('.card-b > .muted.small') ? s.querySelector('.card-b > .muted.small').innerText : null,
      reports: [...s.querySelectorAll('.ft-rep code')].map(c => c.innerText), groups: [...s.querySelectorAll('tr.grp')].map(g => g.innerText),
      rows: [...s.querySelectorAll('tr[data-feature]')].map(r => ({name: r.dataset.feature, code: r.querySelector('[data-f=name] code').innerText, mark: r.querySelector('[data-f=mark]') ? r.querySelector('[data-f=mark]').innerText : null, what: r.querySelector('[data-f=what]').innerText, src: [...r.querySelectorAll('[data-f=src] div')].map(d => d.innerText)}))}))}));
  const total = J.sets.reduce((s, x) => s + x.groups.reduce((a, g) => a + g.features.length, 0), 0);
  let nChecks = 0;
  const eq = (a, b, m) => { nChecks++; if (JSON.stringify(a) !== JSON.stringify(b)) fail(m + ': ' + JSON.stringify(a).slice(0, 160) + ' ≠ ' + JSON.stringify(b).slice(0, 160)); };
  eq(dom.title, 'Признаки', 'заголовок'); eq(dom.meta, `${total} признаков в ${J.sets.length} наборах · обновлено ${J.updated}`, 'мета'); eq(dom.intro, J.intro, 'intro'); eq(dom.foot, J.foot, 'foot');
  eq(dom.navs, J.sets.map((s, i) => `${s.id}|${i + 1} ${s.short}`), 'навигация');
  eq(dom.sets.map(s => s.id), J.sets.map(s => s.id), 'порядок наборов');
  J.sets.forEach((s, i) => {
    const d = dom.sets[i]; if (!d) return;
    const n = s.groups.reduce((a, g) => a + g.features.length, 0), feats = s.groups.flatMap(g => g.features);
    eq(d.status, s.status, s.id + ' статус'); eq(d.badge, s.status_text, s.id + ' плашка'); eq(d.title, `${i + 1} · ${s.title}`, s.id + ' заголовок'); eq(d.meta, `${s.task} · ${n} признаков`, s.id + ' мета');
    eq(d.about, s.about, s.id + ' about'); eq(d.nums, s.numbers, s.id + ' цифры'); eq(d.nonum, s.numbers.length ? null : 'Цифр пока нет.', s.id + ' «цифр нет»');
    eq(d.reports, [...s.reports, s.protocol], s.id + ' отчёты/протокол'); eq(d.groups, s.groups.filter(g => g.title).map(g => g.title), s.id + ' группы');
    eq(d.rows.map(r => r.name), feats.map(f => f.name), s.id + ' имена'); eq(d.rows.map(r => r.code), feats.map(f => f.name.replace(/_bp$/, '')), s.id + ' код');
    eq(d.rows.map(r => r.what), feats.map(f => f.what), s.id + ' что измеряет'); eq(d.rows.map(r => r.src), feats.map(f => f.src.map(k => J.sources[k])), s.id + ' источники');
    eq(d.rows.map(r => r.mark), feats.map(f => f.mark || null), s.id + ' пометки');
    if (s.status === 'running' && s.numbers.length) fail(s.id + ': идёт, но цифры есть');
  });
  C.nChecks = nChecks; C.total = total; C.rowCounts = dom.sets.map(s => s.id + ':' + s.rows.length);
  C.overflow = await overflow();
  C.cellOverflow = await page.evaluate(() => [...document.querySelectorAll('.ft td, .ft-rep code')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).display !== 'inline').length);
  await page.screenshot({path: `${out}/features_top_${W}.png`});
  C.nav = [];
  for (const s of J.sets) {
    await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(250);
    await page.click(`button[data-go="${s.id}"]`); await settle();
    const r = await page.evaluate(i => { const e = document.getElementById('ft-' + i).getBoundingClientRect(); const bar = document.querySelector('.ft-bar').getBoundingClientRect(); return {top: Math.round(e.top), barBottom: Math.round(bar.bottom), hash: location.hash}; }, s.id);
    r.id = s.id; r.ok = r.top >= r.barBottom - 2 && r.top < 260 && r.hash === '#features/' + s.id;
    if (!r.ok) fail('навигация ' + JSON.stringify(r));
    C.nav.push(r);
    await page.screenshot({path: `${out}/features_nav_${s.id}_${W}.png`});
  }
  C.others = {};
  for (const k of ['progress', 'targets', 'equity', 'model', 'features']) {
    await page.click(`#toptabs button[data-tab="${k}"]`); await page.waitForTimeout(400);
    const st = await state();
    C.others[k] = {hash: st.hash, visible: st.visible.join(), overflow: await overflow()};
    if (st.visible.join() !== 'pane-' + k) fail('вкладка ' + k + ': ' + JSON.stringify(st));
  }
  C.rowsAfterReturn = await page.evaluate(() => document.querySelectorAll('tr[data-feature]').length);
  if (C.rowsAfterReturn !== total) fail(`после возврата строк ${C.rowsAfterReturn} ≠ ${total}`);
  for (const h of ['#features', '#features/toxic', '#features/breakability', '#features/нет']) {
    await page.goto(base + h, {waitUntil: 'load'}); await page.reload({waitUntil: 'load'}); await settle();
    const r = await page.evaluate(() => { const bar = document.querySelector('.ft-bar'); const ids = [...document.querySelectorAll('section.ft-set')].map(x => ({id: x.dataset.set, top: Math.round(x.getBoundingClientRect().top)})); return {hash: location.hash, visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join(), barBottom: bar ? Math.round(bar.getBoundingClientRect().bottom) : null, ids}; });
    C['hash ' + h] = r;
    if (r.visible !== 'pane-features') fail(h + ': не вкладка Признаки ' + r.visible);
    const want = h.startsWith('#features/') && h !== '#features/нет' ? h.slice(10) : null;
    if (want) { const e = r.ids.find(x => x.id === want); if (!e || e.top < r.barBottom - 2 || e.top > 260) fail(h + ': секция не наверху ' + JSON.stringify(e)); }
    else if (r.hash !== '#features') fail(h + ': хеш ' + r.hash);
  }
  await page.screenshot({path: `${out}/features_deeplink_${W}.png`});
  fs.writeFileSync(`${out}/features_report_${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({errors: errs, fails: rep.fails, nChecks, total, overflow: C.overflow, cellOverflow: C.cellOverflow, nav: C.nav.map(n => n.id + ':' + n.ok), others: C.others, rows: C.rowCounts, tabs: C.tabs}, null, 1));
  await browser.close();
})();
