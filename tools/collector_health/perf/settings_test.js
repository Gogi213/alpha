// node settings_test.js <url> [ширина окна] [префикс PNG]   (PW=<путь к playwright>, CHROME=<браузер> — на Софии)
// Клик-тест «Настройки» (page-dense.html): кнопка и поповер, фильтры строк (эпизодов за сутки, оборот каждой биржи, расхождение, без BingX) — число строк и содержимое
// против данных, localStorage, значок на кнопке, перезагрузка, мусор в ключе, «по умолчанию»; порядок по умолчанию (эпизодов за сутки); подсказки заголовков;
// метка «снимки» у пар BingX; столбец «после комиссий через 250 мс»; ширина заголовков и ячеек (ничего не обрезано), сдвиг 0 при смене фильтров.
const { chromium } = require(process.env.PW || 'C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], W = +(process.argv[3] || 1180), outp = process.argv[4] || '';
const FK = 'alpha.arb.filters', CK = 'alpha.arb.cols.hidden';
const snap = () => document.fonts.ready.then(() => {
  const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom].map(x => Math.round(x * 100) / 100) };
  const o = {}; const q = (sel, f) => document.querySelectorAll(sel).forEach(f);
  const h1 = document.querySelector('.top h1'); if (h1) o.h1 = R(h1);
  q('.tabs button', (e, i) => o['tab' + i] = R(e)); o.clock = R(document.getElementById('clock'));
  q('#ex button', (e, i) => o['ex' + i] = R(e)); q('#ctl button', e => o['ctl:' + e.dataset.t] = R(e));
  const tw = R(document.getElementById('tw')); o['tw.left/top'] = [tw[0], tw[2], 0, 0];
  o.docW = [document.documentElement.clientWidth, document.documentElement.scrollWidth, 0, 0];
  return o;
});
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, args: process.env.CHROME ? ['--no-sandbox'] : [] });
  const pg = await b.newPage({ viewport: { width: W, height: 900 }, deviceScaleFactor: 1 });
  const errs = []; pg.on('pageerror', e => errs.push(String(e))); pg.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) });
  pg.on('response', r => { if (r.status() >= 400 && !/\/favicon\.ico$/.test(r.url())) errs.push(r.status() + ' ' + r.url()) });
  const fails = []; let steps = 0;
  const check = (name, ok, info) => { steps++; if (!ok) fails.push(name + (info === undefined ? '' : ' — ' + info)) };
  const shiftOf = (a, c) => { let mx = 0, worst = ''; for (const k of Object.keys(a)) { if (!c[k]) continue; const d = Math.max(...a[k].map((x, i) => Math.abs(x - c[k][i]))); if (d > mx) { mx = d; worst = k } } return [mx, worst] };
  const fresh = async (ls) => { await pg.goto(url); await pg.evaluate(([f, c, v]) => { localStorage.removeItem(f); localStorage.removeItem(c); if (v) for (const k in v) localStorage.setItem(k, v[k]) }, [FK, CK, ls || null]); await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => document.fonts.ready); await pg.waitForTimeout(150) };
  const state = () => pg.evaluate(([f]) => {
    const A = window._arb, F = A.FL, XS = new Set(A.sel), gi = A.st.mode === 'out' ? 1 : 0;
    let exp = 0; for (const r of A.rows) { if (!(XS.has(r.a) && XS.has(r.b))) continue; if (A.st.hide && r.cnt[3] === 0) continue; if (F.ep > 0 && !(r.cnt[3] >= F.ep)) continue; if (F.v > 0 && !(r.v && r.v[0] >= F.v * 1e6 && r.v[1] >= F.v * 1e6)) continue; if (F.gap > 0 && !(r.pk && r.pk[gi] >= F.gap * 100)) continue; if (F.snap && (A.SNAP.has(r.a) || A.SNAP.has(r.b))) continue; exp++ }
    const bd = document.querySelector('#ctl .cols-btn .bd'), pop = document.getElementById('colpop');
    return { view: A.view.length, exp, total: A.rows.length, FL: { ...F }, n: A.flN(), ls: localStorage.getItem(f), badge: bd ? bd.textContent : null, open: !pop.hidden, sort: A.st.sort, dir: A.st.dir, mode: A.st.mode,
      rowcnt: document.getElementById('rowcnt') ? document.getElementById('rowcnt').textContent : null, colcnt: document.getElementById('colcnt') ? document.getElementById('colcnt').textContent : null,
      inputs: [...document.querySelectorAll('#colpop input[data-fk]')].map(i => i.value), snapChk: (document.querySelector('#colpop input[data-fs]') || {}).checked,
      resetOff: (document.getElementById('colreset') || {}).disabled };
  }, [FK]);
  const rendered = () => pg.evaluate(() => [...document.querySelectorAll('#tb tr.r')].map(tr => { const t = [...tr.children].map(c => c.textContent.trim()); return { coin: t[0], pair: tr.children[1].textContent.replace(/снимки/g, '').trim(), sn: tr.querySelectorAll('i.sn').length, k: tr.dataset.k } }));
  const openPop = async () => { if (!(await state()).open) await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(100) };
  const resetAll = async () => { await openPop(); if (!(await state()).resetOff) { await pg.click('#colpop [data-reset]'); await pg.waitForTimeout(120) } };
  const setIn = async (k, v) => { await openPop(); await pg.fill('#colpop [data-fk="' + k + '"]', v); await pg.waitForTimeout(150) };

  await fresh();
  // --- кнопка, поповер, порядок по умолчанию
  const lab = await pg.$eval('#ctl [data-cols]', e => e.textContent.trim());
  check('кнопка называется «Настройки»', lab === 'Настройки', lab);
  check('«Столбцы» нигде в панели нет', !(await pg.$eval('#ctl', e => /Столбцы/.test(e.textContent))));
  let s = await state();
  check('по умолчанию сортировка — эпизодов за сутки по убыванию', s.sort === 'n3' && s.dir === -1, s.sort + ' ' + s.dir);
  check('по умолчанию фильтры выключены, значка нет', s.n === 0 && s.badge === null && s.ls === null && s.view === s.exp, JSON.stringify(s.FL) + ' ' + s.badge);
  const ord = await pg.evaluate(() => { const A = window._arb, v = A.view, R = A.rows; let bad = 0; for (let i = 1; i < Math.min(v.length, 400); i++) if (R[v[i - 1]].cnt[3] < R[v[i]].cnt[3]) bad++; return { bad, top: R[v[0]].cnt[3], max: Math.max(...R.map(r => r.cnt[3])) } });
  check('первая строка — максимум эпизодов за сутки, порядок по убыванию', ord.bad === 0 && ord.top === ord.max, JSON.stringify(ord));
  await openPop(); s = await state();
  check('поповер: три поля, флажок и столбцы', await pg.$$eval('#colpop input[data-fk]', e => e.length) === 3 && await pg.$('#colpop input[data-fs]') != null && await pg.$$eval('#colpop input[data-ck]', e => e.length) === 15);
  check('поповер: «по умолчанию» выключена', s.resetOff === true);
  check('поповер: заголовок «Настройки»', await pg.$eval('#colpop h4', e => e.textContent) === 'Настройки');
  check('поповер: счётчик строк', s.rowcnt === 'Строк: ' + s.view.toLocaleString('ru').replace(/\u00a0/g, ' ') + ' из ' + s.total.toLocaleString('ru').replace(/\u00a0/g, ' ') || /^Строк: [\d  ]+ из [\d  ]+$/.test(s.rowcnt), s.rowcnt);
  const pr = await pg.$eval('#colpop', e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight, Math.round(r.width)] });
  check('поповер: в окне', pr[0] >= 0 && pr[1] <= pr[4] && pr[2] >= 0 && pr[3] <= pr[5], JSON.stringify(pr));
  const base = await pg.evaluate(snap);

  // --- фильтры: каждый и все вместе, оба режима
  const cases = [['эпизодов за сутки ≥ 20', { ep: '20' }], ['оборот ≥ 1 млн', { v: '1' }], ['расхождение ≥ 0,2 % (запятая)', { gap: '0,2' }], ['эпизодов ≥ 5, оборот ≥ 0,5, расхождение ≥ 0.15', { ep: '5', v: '0,5', gap: '0.15' }]];
  for (const mode of ['med', 'out']) {
    if (mode === 'out') { await resetAll(); await pg.click('#ctl [data-m="out"]'); await pg.waitForTimeout(150) }
    for (const [name, f] of cases) {
      await openPop();
      for (const [k, v] of Object.entries(f)) await setIn(k, v);
      s = await state();
      const num = { ep: 0, v: 0, gap: 0 }; for (const [k, v] of Object.entries(f)) num[k] = Number(v.replace(',', '.'));
      check(mode + ' ' + name + ': FL', s.FL.ep === num.ep && s.FL.v === num.v && s.FL.gap === num.gap, JSON.stringify(s.FL));
      check(mode + ' ' + name + ': строк = по данным', s.view === s.exp && s.view > 0 && s.view < s.total, s.view + ' / ' + s.exp + ' / ' + s.total);
      check(mode + ' ' + name + ': значок', s.badge === String(Object.keys(f).length) && s.n === Object.keys(f).length, s.badge);
      check(mode + ' ' + name + ': localStorage', s.ls && JSON.parse(s.ls).ep === num.ep && JSON.parse(s.ls).gap === num.gap, s.ls);
      const rows = await pg.evaluate(() => { const A = window._arb; return A.view.slice(0, 300).map(i => { const r = A.rows[i]; return [r.cnt[3], r.v ? Math.min(r.v[0], r.v[1]) : -1, r.pk ? r.pk[A.st.mode === 'out' ? 1 : 0] : -1] }) });
      check(mode + ' ' + name + ': каждая строка проходит', rows.every(r => r[0] >= num.ep && r[1] >= num.v * 1e6 && r[2] >= num.gap * 100), JSON.stringify(rows.find(r => !(r[0] >= num.ep && r[1] >= num.v * 1e6 && r[2] >= num.gap * 100))));
      check(mode + ' ' + name + ': счётчик строк в поповере', new RegExp('Строк: .+ из ').test(s.rowcnt) && s.resetOff === false, s.rowcnt);
      const sh = shiftOf(base, await pg.evaluate(snap)); check(mode + ' ' + name + ': сдвиг шапки и панелей 0', sh[0] === 0, sh.join(' '));
      await pg.click('#colpop [data-reset]'); await pg.waitForTimeout(120);
      s = await state(); check(mode + ' ' + name + ': «по умолчанию» → всё выключено', s.n === 0 && s.ls === null && s.badge === null && s.view === s.exp && s.inputs.every(x => x === '') && s.snapChk === false, JSON.stringify(s));
    }
  }
  await resetAll(); await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(120);

  // --- BingX: метка «снимки», фильтр
  let rr = await rendered();
  const withB = rr.filter(r => /BingX/.test(r.pair));
  check('метка «снимки»: есть у каждой показанной пары с BingX, ровно одна', withB.length > 0 && withB.every(r => r.sn === 1) && rr.filter(r => !/BingX/.test(r.pair)).every(r => r.sn === 0), withB.length + ' пар с BingX');
  const sn0 = await pg.$('#tb i.sn'); await sn0.hover(); await pg.waitForTimeout(350);
  let tp = await pg.$eval('#htip', e => ({ o: getComputedStyle(e).opacity, t: e.textContent, r: (r => [r.left, r.right, r.top, r.bottom])(e.getBoundingClientRect()), w: innerWidth }));
  check('подсказка метки «снимки»', +tp.o > 0.9 && /BingX/.test(tp.t) && tp.r[0] >= 0 && tp.r[1] <= tp.w, JSON.stringify(tp));
  await pg.mouse.move(5, 5); await pg.waitForTimeout(150); check('подсказка метки уходит', await pg.$eval('#htip', e => getComputedStyle(e).opacity) === '0');
  await openPop(); await pg.click('#colpop input[data-fs]'); await pg.waitForTimeout(150);
  s = await state(); rr = await rendered();
  check('без BingX: строк по данным, пар с BingX нет', s.FL.snap === true && s.view === s.exp && s.view > 0 && rr.every(r => !/BingX/.test(r.pair)) && s.badge === '1', s.view + '/' + s.exp);
  check('без BingX: localStorage', JSON.parse(s.ls).snap === true);
  await pg.click('#ex [data-x="okx"]'); await pg.waitForTimeout(120); s = await state(); check('без BingX + чип OKX выкл.: строк по данным', s.view === s.exp, s.view + '/' + s.exp);
  await pg.click('#ex [data-x="okx"]'); await pg.waitForTimeout(100);

  // --- сохранение, мусор, неверный ввод
  await setIn('ep', '10'); await setIn('gap', '0,18');
  await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(150); s = await state();
  check('после перезагрузки фильтры те же', s.FL.ep === 10 && s.FL.gap === 0.18 && s.FL.snap === true && s.FL.v === 0 && s.n === 3 && s.badge === '3' && s.view === s.exp, JSON.stringify(s.FL));
  await openPop(); s = await state(); check('после перезагрузки поля заполнены', JSON.stringify(s.inputs) === JSON.stringify(['10', '', '0,18']) && s.snapChk === true, JSON.stringify(s.inputs));
  await pg.fill('#colpop [data-fk="v"]', 'abc'); await pg.waitForTimeout(100);
  check('неверный ввод: поле красное, фильтр не менялся', await pg.$eval('#colpop [data-fk="v"]', e => e.classList.contains('bad')) && (await state()).FL.v === 0);
  await pg.fill('#colpop [data-fk="v"]', '-3'); await pg.waitForTimeout(100); check('минус: поле красное', await pg.$eval('#colpop [data-fk="v"]', e => e.classList.contains('bad')));
  await pg.fill('#colpop [data-fk="v"]', ''); await pg.waitForTimeout(100); check('пустое поле: не красное, выкл.', !(await pg.$eval('#colpop [data-fk="v"]', e => e.classList.contains('bad'))) && (await state()).FL.v === 0);
  await pg.keyboard.press('Escape'); await pg.waitForTimeout(80); check('Esc закрывает', !(await state()).open);
  for (const [name, v] of [['не JSON', 'xx{'], ['не те типы', JSON.stringify({ ep: 'x', v: -1, gap: null, snap: 'yes' })], ['массив', '[1,2]'], ['бесконечность', '{"ep":1e999,"snap":false}']]) {
    await fresh({ [FK]: v }); s = await state(); check('мусор в ключе (' + name + '): все выкл.', s.FL.ep === 0 && s.FL.v === 0 && s.FL.gap === 0 && s.FL.snap === false && s.view === s.exp, JSON.stringify(s.FL));
  }

  // --- подсказки заголовков
  await fresh();
  const ths = await pg.$$eval('#th th', e => e.map(t => ({ k: t.dataset.k, h: t.dataset.h })));
  check('у каждого заголовка есть подсказка', ths.length >= 14 && ths.every(t => t.h && t.h.length > 20), JSON.stringify(ths.filter(t => !t.h)));
  const bs = await pg.evaluate(snap);
  for (const t of ths) {
    await pg.hover('#th th[data-k="' + t.k + '"]'); await pg.waitForTimeout(300);
    const r = await pg.$eval('#htip', (e, k) => { const th = document.querySelector('#th th[data-k="' + k + '"]').getBoundingClientRect(), r = e.getBoundingClientRect(); return { o: getComputedStyle(e).opacity, t: e.textContent, l: r.left, r: r.right, top: r.top, thb: th.bottom, w: innerWidth } }, t.k);
    check('подсказка «' + t.k + '»: видна, текст верный, в окне, под заголовком', +r.o > 0.9 && r.t === t.h && r.l >= 0 && r.r <= r.w && r.top >= r.thb, JSON.stringify(r));
    check('подсказка «' + t.k + '»: сдвиг 0', shiftOf(bs, await pg.evaluate(snap))[0] === 0);
    await pg.mouse.move(5, 5); await pg.waitForTimeout(120);
    if (await pg.$eval('#htip', e => getComputedStyle(e).opacity) !== '0') check('подсказка «' + t.k + '»: уходит', false);
  }
  await pg.hover('#th th[data-k="n2"]'); await pg.waitForTimeout(300); await pg.click('#th th[data-k="n2"]'); await pg.waitForTimeout(120);
  check('клик по заголовку: подсказка скрыта, сортировка по нему', (await pg.$eval('#htip', e => getComputedStyle(e).opacity)) === '0' && (await state()).sort === 'n2');
  await pg.click('#th th[data-k="n3"]'); await pg.waitForTimeout(100);

  // --- столбец «после комиссий через 250 мс»
  await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(100);
  const n2 = await pg.evaluate(() => { const A = window._arb, c = A.cols().find(c => c.k === 'net2'); const rows = [...document.querySelectorAll('#tb tr.r')]; const idx = [...document.querySelectorAll('#th th')].findIndex(t => t.dataset.k === 'net2'); return { has: !!c, head: c && c.t, idx, cells: rows.map(tr => tr.children[idx].textContent.trim()), data: rows.map(tr => A.rows.find(r => r.k === tr.dataset.k).net250 || null) } });
  check('столбец net2: есть, подпись с «250 мс», стоит сразу за «после комиссий»', n2.has && /250 мс/.test(n2.head) && n2.idx > 0, JSON.stringify([n2.has, n2.head, n2.idx]));
  const ord2 = await pg.$$eval('#th th', e => e.map(t => t.dataset.k)); check('net2 сразу после net', ord2.indexOf('net2') === ord2.indexOf('net') + 1, ord2.join());
  check('столбец net2: ячейка «—» только если данных нет, иначе число', n2.cells.every((c, i) => n2.data[i] ? /^[+−]?\d[\d ]*,\d{3}$/.test(c) : c === '—'), JSON.stringify(n2.cells.slice(0, 6)) + JSON.stringify(n2.data.slice(0, 3)));
  await pg.click('#th th[data-k="net2"]'); await pg.waitForTimeout(120); s = await state(); check('сортировка по net2', s.sort === 'net2' && s.dir === -1);
  const o2 = await pg.evaluate(() => { const A = window._arb, v = A.view, R = A.rows; let bad = 0, prev = Infinity; for (let i = 0; i < Math.min(v.length, 300); i++) { const x = R[v[i]].net250 ? R[v[i]].net250[0] : -1e9; if (x > prev) bad++; prev = x } return bad });
  check('порядок по net2 убывает', o2 === 0, o2);
  await pg.click('#th th[data-k="n3"]'); await pg.waitForTimeout(100);

  // --- ничего не обрезано: заголовки и ячейки, оба режима
  for (const mode of ['med', 'out']) {
    await pg.click('#ctl [data-m="' + mode + '"]'); await pg.waitForTimeout(150);
    const cl = await pg.evaluate(() => {
      const th_ = [], tall = [], td_ = []; const tr = document.querySelector('#th tr').children;
      for (const th of tr) { const t = th.getBoundingClientRect(), h = th.querySelector('.hl').getBoundingClientRect(); if (h.left < t.left - 1 || h.right > t.right + 1) th_.push(th.dataset.k + ' ' + Math.round(h.left - t.left) + '/' + Math.round(t.right - h.right)); if (t.height > 60.5) tall.push(th.dataset.k + ' h' + Math.round(t.height)) }
      const rows = [...document.querySelectorAll('#tb tr.r')].slice(0, 120);
      for (const row of rows) for (const td of [...row.children].slice(1)) if (td.scrollWidth > td.clientWidth + 0.5) td_.push(td.textContent.trim().slice(0, 22) + ' ' + td.scrollWidth + '>' + td.clientWidth);
      return { th: th_, tall, td: [...new Set(td_)].slice(0, 6), ntd: td_.length, docW: [document.documentElement.clientWidth, document.documentElement.scrollWidth] };
    });
    check(mode + ': заголовки и ячейки (кроме монеты) не обрезаны, горизонтальной прокрутки нет', cl.th.length === 0 && cl.tall.length === 0 && cl.ntd === 0 && cl.docW[0] === cl.docW[1], JSON.stringify(cl));
  }
  await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(100);
  if (outp) { await pg.screenshot({ path: outp + '-table.png' }); await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(150); await pg.screenshot({ path: outp + '-pop.png' }) }
  check('ошибок страницы нет', errs.length === 0, errs.slice(0, 3).join(' | '));
  console.log(JSON.stringify({ W, steps, fails: fails.length, errs: errs.length }));
  fails.slice(0, 25).forEach(f => console.log('FAIL ' + f.slice(0, 400)));
  await b.close(); process.exit(fails.length ? 1 : 0);
})();
