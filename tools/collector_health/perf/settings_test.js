// node settings_test.js <url> [ширина окна] [префикс PNG]   (PW=<путь к playwright>, CHROME=<браузер> — на Софии)
// Клик-тест «Настройки» (page-dense.html): кнопка и поповер без пояснительного текста, порог θ (0,1 / 0,15 / 0,2 / 0,3 / 0,5 %, по умолчанию 0,2), фильтры строк (оборот, расхождение, выбросы ×3, схождение,
// «после комиссий», круг до нуля, круг из − в +, без BingX) — число строк и содержимое против данных, localStorage, значок, перезагрузка, мусор и прежний формат в ключе, «по умолчанию»;
// столбцы кругов (подписи с θ, значения выбранного θ, сортировка, оба режима); столбца «через 250 мс» нет; подсказки заголовков; метка «снимки»; ничего не обрезано; сдвиг 0.
const { chromium } = require(process.env.PW || 'C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], W = +(process.argv[3] || 1180), outp = process.argv[4] || '';
const FK = 'alpha.arb.filters', CK = 'alpha.arb.cols.hidden', TK = 'alpha.arb.theta';
const THS = [10, 15, 20, 30, 50], TL = { 10: '0,1', 15: '0,15', 20: '0,2', 30: '0,3', 50: '0,5' };
// правило фильтров — независимая запись (не код страницы): F — значения полей, null — выкл.; gi — 0 медиана / 1 p95; ti — индекс θ
const PASS_SRC = `(r, F, gi, ti, SN) => {
  const z = (w) => { const a = r[w]; const e = a ? a[ti] : null; return e ? e[gi] : null };
  if (F.v != null && !(r.v && r.v[0] >= F.v * 1e6 && r.v[1] >= F.v * 1e6)) return false;
  if (F.gap != null && !(r.pk && r.pk[gi] >= F.gap * 100)) return false;
  for (const i of [0, 1, 2]) if (F['b' + i] != null && !(r.big && r.big[i] >= F['b' + i])) return false;
  if (F.tau != null && !(r.tau && r.tau[gi] <= F.tau)) return false;
  if (F.net != null && !(r.net && r.net[gi] >= F.net * 100)) return false;
  if (F.z != null) { const v = z('z'); if (!(v != null && v >= F.z * 100)) return false }
  if (F.x != null) { const v = z('x'); if (!(v != null && v >= F.x * 100)) return false }
  if (F.snap && (SN.has(r.a) || SN.has(r.b))) return false;
  return true }`;
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
  const fresh = async (ls) => { await pg.goto(url); await pg.evaluate(([f, c, t, v]) => { localStorage.removeItem(f); localStorage.removeItem(c); localStorage.removeItem(t); if (v) for (const k in v) localStorage.setItem(k, v[k]) }, [FK, CK, TK, ls || null]); await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => document.fonts.ready); await pg.waitForTimeout(150) };
  const state = () => pg.evaluate(([f, t, src]) => {
    const A = window._arb, F = A.FL, XS = new Set(A.sel), gi = A.st.mode === 'out' ? 1 : 0, ti = A.thetas().indexOf(A.TH), pass = eval(src);
    let exp = 0; for (const r of A.rows) { if (!(XS.has(r.a) && XS.has(r.b))) continue; if (A.st.hide && r.cnt[3] === 0) continue; if (!pass(r, F, gi, ti, A.SNAP)) continue; exp++ }
    const bd = document.querySelector('#ctl .cols-btn .bd'), pop = document.getElementById('colpop');
    return { view: A.view.length, exp, total: A.rows.length, FL: { ...F }, n: A.flN(), ls: localStorage.getItem(f), lsTh: localStorage.getItem(t), TH: A.TH, badge: bd ? bd.textContent : null, open: !pop.hidden, sort: A.st.sort, dir: A.st.dir, mode: A.st.mode,
      rowcnt: document.getElementById('rowcnt') ? document.getElementById('rowcnt').textContent : null, colcnt: document.getElementById('colcnt') ? document.getElementById('colcnt').textContent : null,
      inputs: [...document.querySelectorAll('#colpop input[data-fk]')].map(i => i.value), snapChk: (document.querySelector('#colpop input[data-fs]') || {}).checked,
      segOn: [...document.querySelectorAll('#colpop .seg button.on')].map(e => e.textContent), resetOff: (document.getElementById('colreset') || {}).disabled };
  }, [FK, TK, PASS_SRC]);
  // показанные строки (первые 300) против правила
  const badRows = () => pg.evaluate(([src]) => { const A = window._arb, F = A.FL, gi = A.st.mode === 'out' ? 1 : 0, ti = A.thetas().indexOf(A.TH), pass = eval(src); return A.view.slice(0, 300).filter(i => !pass(A.rows[i], F, gi, ti, A.SNAP)).length }, [PASS_SRC]);
  const rendered = () => pg.evaluate(() => [...document.querySelectorAll('#tb tr.r')].map(tr => { const t = [...tr.children].map(c => c.textContent.trim()); return { coin: t[0], pair: tr.children[1].textContent.replace(/снимки/g, '').trim(), sn: tr.querySelectorAll('i.sn').length, k: tr.dataset.k } }));
  const openPop = async () => { if (!(await state()).open) await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(100) };
  const resetAll = async () => { await openPop(); if (!(await state()).resetOff) { await pg.click('#colpop [data-reset]'); await pg.waitForTimeout(120) } };
  const setIn = async (k, v) => { await openPop(); await pg.fill('#colpop [data-fk="' + k + '"]', v); await pg.waitForTimeout(150) };
  // порог для поля k так, чтобы фильтр оставил часть строк: квантиль значения по данным, в единицах поля (строкой с запятой)
  const thr = (k, q) => pg.evaluate(([k, q]) => {
    const A = window._arb, gi = A.st.mode === 'out' ? 1 : 0, ti = A.thetas().indexOf(A.TH), v = [];
    for (const r of A.rows) { let x = null;
      if (k === 'v') x = r.v ? Math.min(r.v[0], r.v[1]) / 1e6 : null; else if (k === 'gap') x = r.pk ? r.pk[gi] / 100 : null; else if (k[0] === 'b') x = r.big ? r.big[+k[1]] : null; else if (k === 'tau') x = r.tau ? r.tau[gi] : null;
      else if (k === 'net') x = r.net ? r.net[gi] / 100 : null; else if (k === 'z' || k === 'x') x = r[k] && r[k][ti] ? r[k][ti][gi] / 100 : null;
      if (x != null) v.push(x) }
    v.sort((a, b) => a - b); const x = v[Math.min(v.length - 1, Math.floor(v.length * q))];
    return (k[0] === 'b' ? String(Math.round(x)) : String(Math.round(x * 1000) / 1000)).replace('.', ',') }, [k, q]);

  await fresh();
  const HAS = await pg.evaluate(() => window._arb.hasC());   // круги и θ — только если arb.json их отдаёт
  // --- кнопка, поповер, порядок по умолчанию
  const lab = await pg.$eval('#ctl [data-cols]', e => e.textContent.trim());
  check('кнопка называется «Настройки»', lab === 'Настройки', lab);
  check('«Столбцы» нигде в панели нет', !(await pg.$eval('#ctl', e => /Столбцы/.test(e.textContent))));
  let s = await state();
  check('по умолчанию сортировка — эпизодов за сутки по убыванию', s.sort === 'n3' && s.dir === -1, s.sort + ' ' + s.dir);
  check('по умолчанию фильтры выключены, значка нет, θ = 0,2', s.n === 0 && s.badge === null && s.ls === null && s.view === s.exp && s.TH === 20 && s.lsTh === null, JSON.stringify(s.FL) + ' ' + s.badge + ' ' + s.TH);
  const ord = await pg.evaluate(() => { const A = window._arb, v = A.view, R = A.rows; let bad = 0; for (let i = 1; i < Math.min(v.length, 400); i++) if (R[v[i - 1]].cnt[3] < R[v[i]].cnt[3]) bad++; return { bad, top: R[v[0]].cnt[3], max: Math.max(...R.map(r => r.cnt[3])) } });
  check('первая строка — максимум эпизодов за сутки, порядок по убыванию', ord.bad === 0 && ord.top === ord.max, JSON.stringify(ord));
  await openPop(); s = await state();
  const pop = await pg.$eval('#colpop', e => ({ h: [...e.querySelectorAll('h5')].map(x => x.textContent), p: e.querySelectorAll('p').length, txt: e.textContent, seg: [...e.querySelectorAll('.seg button')].map(x => x.textContent), fk: [...e.querySelectorAll('input[data-fk]')].map(x => x.dataset.fk), lab: [...e.querySelectorAll('label.fr')].map(x => x.textContent.trim()) }));
  check('поповер: θ — пять кнопок 0,1 / 0,15 / 0,2 / 0,3 / 0,5, выбрана 0,2 (нет кругов в данных — секции нет)', JSON.stringify(pop.seg) === JSON.stringify(HAS ? ['0,1', '0,15', '0,2', '0,3', '0,5'] : []) && JSON.stringify(s.segOn) === (HAS ? '["0,2"]' : '[]'), JSON.stringify([pop.seg, s.segOn]));
  check('поповер: поля фильтров (девять; без кругов — семь), флажок BingX', JSON.stringify(pop.fk) === JSON.stringify(['v', 'gap', 'b0', 'b1', 'b2', 'tau', 'net'].concat(HAS ? ['z', 'x'] : [])) && await pg.$('#colpop input[data-fs]') != null, JSON.stringify(pop.fk));
  check('поповер: «эпизодов за сутки» нет, пояснений нет (ни абзацев, ни «Сохраняется…», ни «Расхождение — по тому…»)', pop.p === 0 && !/эпизодов за сутки ≥|Сохраняется|по тому, что показано/.test(pop.txt), JSON.stringify([pop.p, pop.lab]));
  check('поповер: подписи полей короткие (≤ 36 знаков)', pop.lab.every(x => x.length <= 36), JSON.stringify(pop.lab.filter(x => x.length > 36)));
  check('поповер: столбцы — 17 флажков (в режиме «медиана»; без кругов — 14)', await pg.$$eval('#colpop input[data-ck]', e => e.length) === (HAS ? 17 : 14));
  check('поповер: «по умолчанию» выключена', s.resetOff === true);
  check('поповер: заголовок «Настройки»', await pg.$eval('#colpop h4', e => e.textContent) === 'Настройки');
  check('поповер: счётчик строк', /^Строк: [\d  ]+ из [\d  ]+$/.test(s.rowcnt), s.rowcnt);
  const pr = await pg.$eval('#colpop', e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight, Math.round(r.width)] });
  check('поповер: в окне по горизонтали, вертикаль — с прокруткой', pr[0] >= 0 && pr[1] <= pr[4] && pr[2] >= 0, JSON.stringify(pr));
  const base = await pg.evaluate(snap);

  // --- фильтры: каждый и все вместе, оба режима; пороги — квантили по данным (часть строк остаётся)
  for (const mode of ['med', 'out']) {
    await resetAll(); await pg.click('#ctl [data-m="' + mode + '"]'); await pg.waitForTimeout(150);
    const cases = [['оборот', { v: await thr('v', 0.5) }], ['расхождение', { gap: await thr('gap', 0.5) }], ['выбросов 0,3', { b0: await thr('b0', 0.8) }], ['выбросов 0,5', { b1: await thr('b1', 0.9) }], ['выбросов 1', { b2: await thr('b2', 0.95) }],
      ['схождение ≤', { tau: await thr('tau', 0.5) }], ['после комиссий', { net: await thr('net', 0.5) }], ...(HAS ? [['круг до нуля', { z: await thr('z', 0.5) }], ['круг из − в +', { x: await thr('x', 0.5) }]] : [])];
    cases.push(['все вместе', { v: await thr('v', 0.3), gap: await thr('gap', 0.3), b0: '1', tau: await thr('tau', 0.8), net: await thr('net', 0.3), ...(HAS ? { z: await thr('z', 0.3), x: await thr('x', 0.3) } : {}) }]);
    for (const [name, f] of cases) {
      await openPop();
      for (const [k, v] of Object.entries(f)) await setIn(k, v);
      s = await state();
      const want = {}; for (const [k, v] of Object.entries(f)) want[k] = Number(v.replace(',', '.'));
      check(mode + ' ' + name + ': FL = введённому', Object.entries(want).every(([k, v]) => s.FL[k] === v) && Object.keys(s.FL).filter(k => k !== 'snap' && !(k in want)).every(k => s.FL[k] === null), JSON.stringify(s.FL) + ' ' + JSON.stringify(f));
      check(mode + ' ' + name + ': строк = по данным, часть строк осталась', s.view === s.exp && s.view > 0 && s.view < s.total, s.view + ' / ' + s.exp + ' / ' + s.total + ' ' + JSON.stringify(f));
      check(mode + ' ' + name + ': значок', s.badge === String(Object.keys(f).length) && s.n === Object.keys(f).length, s.badge);
      check(mode + ' ' + name + ': localStorage', s.ls && Object.entries(want).every(([k, v]) => JSON.parse(s.ls)[k] === v), s.ls);
      check(mode + ' ' + name + ': каждая показанная строка проходит правило', (await badRows()) === 0);
      check(mode + ' ' + name + ': счётчик строк в поповере', new RegExp('Строк: .+ из ').test(s.rowcnt) && s.resetOff === false, s.rowcnt);
      const sh = shiftOf(base, await pg.evaluate(snap)); check(mode + ' ' + name + ': сдвиг шапки и панелей 0', sh[0] === 0, sh.join(' '));
      await pg.click('#colpop [data-reset]'); await pg.waitForTimeout(120);
      s = await state(); check(mode + ' ' + name + ': «по умолчанию» → всё выключено', s.n === 0 && s.ls === null && s.badge === null && s.view === s.exp && s.inputs.every(x => x === '') && s.snapChk === false, JSON.stringify(s));
    }
  }
  await resetAll(); await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(120);

  // --- фильтр «после комиссий ≥ 0» (ноль и отрицательное — настоящие значения, не «выключено»)
  await setIn('net', '0'); s = await state();
  check('«после комиссий ≥ 0»: ноль включает фильтр', s.FL.net === 0 && s.n === 1 && s.badge === '1' && s.view === s.exp && s.view > 0 && s.view < s.total && (await badRows()) === 0, JSON.stringify(s.FL) + ' ' + s.view);
  await setIn('net', '−0,5'); s = await state(); check('минус (знак «−» и «-») в «после комиссий» принимается', s.FL.net === -0.5 && !(await pg.$eval('#colpop [data-fk="net"]', e => e.classList.contains('bad'))) && s.view === s.exp, JSON.stringify(s.FL));
  await setIn('net', '-0.5'); s = await state(); check('«-0.5» тоже', s.FL.net === -0.5 && s.view === s.exp);
  await setIn('net', ''); await setIn('net', '-'); s = await state(); check('одинокий «−»: не включает, поле не красное', s.FL.net === null && !(await pg.$eval('#colpop [data-fk="net"]', e => e.classList.contains('bad'))));
  await setIn('net', ''); await setIn('v', '0'); s = await state(); check('«оборот ≥ 0»: фильтр включён, строк столько же, значок 1', s.FL.v === 0 && s.n === 1 && s.badge === '1');
  await resetAll();

  if (HAS) {
  // --- порог θ: каждая кнопка — заголовки, значения, сортировка, localStorage, перезагрузка
  var cellsOf = (k) => pg.evaluate((k) => { const A = window._arb, ti = A.thetas().indexOf(A.TH), gi = A.st.mode === 'out' ? 1 : 0, idx = [...document.querySelectorAll('#th th')].findIndex(t => t.dataset.k === k);
    const rows = [...document.querySelectorAll('#tb tr.r')].slice(0, 150), fmt = v => v == null ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + (Math.abs(v) / 100).toFixed(3).replace('.', ',');
    let bad = 0, ex = ''; for (const tr of rows) { const r = A.rows.find(x => x.k === tr.dataset.k), e = k === 'xn' ? (r.x && r.x[ti] ? r.x[ti][2] : null) : (r[k] && r[k][ti] ? r[k][ti][gi] : null);
      const want = k === 'xn' ? (e == null ? '—' : String(e)) : fmt(e), got = tr.children[idx].textContent.trim().replace(/\u00a0/g, ' ').replace(/ /g, ''); if (got !== want.replace(/ /g, '')) { bad++; ex = got + ' ≠ ' + want } }
    return { n: rows.length, bad, ex, head: document.querySelectorAll('#th th')[idx].textContent.replace(/\s+/g, ' ').trim() } }, k);
  for (const mode of ['med', 'out']) {
    await resetAll(); await pg.click('#ctl [data-m="' + mode + '"]'); await pg.waitForTimeout(120);
    for (const t of THS) {
      await openPop(); await pg.click('#colpop .seg button[data-th="' + t + '"]'); await pg.waitForTimeout(150); s = await state();
      check(mode + ' θ ' + TL[t] + ': кнопка нажата, A.TH, localStorage', s.TH === t && JSON.stringify(s.segOn) === JSON.stringify([TL[t]]) && (t === 20 ? s.lsTh === null : s.lsTh === String(t)), JSON.stringify([s.TH, s.segOn, s.lsTh]));
      check(mode + ' θ ' + TL[t] + ': поповер остался открытым, строки те же, значка нет', s.open && s.view === s.total && s.badge === null);
      for (const k of ['z', 'x', 'xn']) { const c = await cellsOf(k); check(mode + ' θ ' + TL[t] + ' ' + k + ': подпись с θ, ' + c.n + ' ячеек = данным', c.n > 0 && c.bad === 0 && c.head.includes('θ ' + TL[t] + ' %'), JSON.stringify(c)) }
      await pg.keyboard.press('Escape'); await pg.waitForTimeout(60);                              // поповер закрывает шапку
      await pg.click('#th th[data-k="x"]'); await pg.waitForTimeout(100);
      const ordx = await pg.evaluate(() => { const A = window._arb, ti = A.thetas().indexOf(A.TH), gi = A.st.mode === 'out' ? 1 : 0, v = A.view; let bad = 0, prev = Infinity; for (let i = 0; i < Math.min(v.length, 300); i++) { const e = A.rows[v[i]].x && A.rows[v[i]].x[ti] ? A.rows[v[i]].x[ti][gi] : -1e9; if (e > prev) bad++; prev = e } return { bad, sort: A.st.sort, dir: A.st.dir } });
      check(mode + ' θ ' + TL[t] + ': сортировка по кругу из − в + убывает', ordx.bad === 0 && ordx.sort === 'x' && ordx.dir === -1, JSON.stringify(ordx));
      await pg.click('#th th[data-k="xn"]'); await pg.waitForTimeout(100);
      const ordn = await pg.evaluate(() => { const A = window._arb, ti = A.thetas().indexOf(A.TH), v = A.view; let bad = 0, prev = Infinity; for (let i = 0; i < Math.min(v.length, 300); i++) { const e = A.rows[v[i]].x && A.rows[v[i]].x[ti] ? A.rows[v[i]].x[ti][2] : -1; if (e > prev) bad++; prev = e } return bad });
      check(mode + ' θ ' + TL[t] + ': сортировка по числу кругов убывает', ordn === 0);
      await pg.click('#th th[data-k="n3"]'); await pg.waitForTimeout(80);
      const sh = shiftOf(base, await pg.evaluate(snap)); check(mode + ' θ ' + TL[t] + ': сдвиг шапки и панелей 0', sh[0] === 0, sh.join(' '));
    }
  }
  await resetAll(); await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(100);
  await openPop(); await pg.click('#colpop .seg button[data-th="30"]'); await pg.waitForTimeout(120);
  await setIn('x', '0,1'); await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(150); s = await state();
  check('после перезагрузки θ 0,3 и фильтр круга те же', s.TH === 30 && s.FL.x === 0.1 && s.view === s.exp && s.n === 1, JSON.stringify([s.TH, s.FL.x, s.view, s.exp]));
  await openPop(); s = await state(); check('после перезагрузки кнопка θ 0,3 нажата', JSON.stringify(s.segOn) === '["0,3"]' && s.inputs[8] === '0,1', JSON.stringify([s.segOn, s.inputs]));
  check('«по умолчанию» включена при θ ≠ 0,2', s.resetOff === false);
  await pg.click('#colpop [data-reset]'); await pg.waitForTimeout(120); s = await state();
  check('«по умолчанию» возвращает θ 0,2, фильтры выкл., ключи удалены', s.TH === 20 && s.lsTh === null && s.ls === null && JSON.stringify(s.segOn) === '["0,2"]' && s.resetOff === true && s.n === 0, JSON.stringify(s));
  await pg.click('#colpop .seg button[data-th="10"]'); await pg.waitForTimeout(100); s = await state(); check('только θ ≠ 0,2 (без фильтров): значка нет, «по умолчанию» включена', s.badge === null && s.resetOff === false && s.TH === 10);
  await pg.click('#colpop [data-reset]'); await pg.waitForTimeout(100);
  for (const [name, v] of [['не число', 'abc'], ['нет в списке', '17'], ['строка', '"x"'], ['null', 'null'], ['массив', '[1]']]) {
    await fresh({ [TK]: v }); s = await state(); check('мусор в ключе θ (' + name + '): θ = 0,2', s.TH === 20, s.TH);
  }
  await fresh({ [TK]: '15' }); s = await state(); check('ключ θ = 15 → 0,15', s.TH === 15);

  }

  // --- BingX: метка «снимки», фильтр
  await fresh();
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

  // --- сохранение, мусор, прежний формат, неверный ввод
  await setIn('b0', '2'); await setIn('gap', '0,18');
  await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(150); s = await state();
  check('после перезагрузки фильтры те же', s.FL.b0 === 2 && s.FL.gap === 0.18 && s.FL.snap === true && s.FL.v === null && s.n === 3 && s.badge === '3' && s.view === s.exp, JSON.stringify(s.FL));
  await openPop(); s = await state(); check('после перезагрузки поля заполнены', JSON.stringify(s.inputs) === JSON.stringify((() => { const a = Array(HAS ? 9 : 7).fill(''); a[1] = '0,18'; a[2] = '2'; return a })()) && s.snapChk === true, JSON.stringify(s.inputs));
  await pg.fill('#colpop [data-fk="v"]', 'abc'); await pg.waitForTimeout(100);
  check('неверный ввод: поле красное, фильтр не менялся', await pg.$eval('#colpop [data-fk="v"]', e => e.classList.contains('bad')) && (await state()).FL.v === null);
  await pg.fill('#colpop [data-fk="v"]', '-3'); await pg.waitForTimeout(100); check('минус в обороте: поле красное, фильтр не включён', await pg.$eval('#colpop [data-fk="v"]', e => e.classList.contains('bad')) && (await state()).FL.v === null);
  await pg.fill('#colpop [data-fk="v"]', ''); await pg.waitForTimeout(100); check('пустое поле: не красное, выкл.', !(await pg.$eval('#colpop [data-fk="v"]', e => e.classList.contains('bad'))) && (await state()).FL.v === null);
  await pg.keyboard.press('Escape'); await pg.waitForTimeout(80); check('Esc закрывает', !(await state()).open);
  for (const [name, v] of [['не JSON', 'xx{'], ['не те типы', JSON.stringify({ b0: 'x', v: -1, gap: null, tau: -2, snap: 'yes' })], ['массив', '[1,2]'], ['бесконечность', '{"b1":1e999,"snap":false}']]) {
    await fresh({ [FK]: v }); s = await state(); check('мусор в ключе (' + name + '): все выкл.', Object.entries(s.FL).every(([k, x]) => k === 'snap' ? x === false : x === null) && s.view === s.exp, JSON.stringify(s.FL));
  }
  await fresh({ [FK]: JSON.stringify({ ep: 20, v: 1, gap: 0.2, snap: true }) }); s = await state();
  check('прежний формат ключа (ep, v, gap, snap): ep отброшен, остальное прочитано', s.FL.v === 1 && s.FL.gap === 0.2 && s.FL.snap === true && !('ep' in s.FL) && s.n === 3 && s.view === s.exp, JSON.stringify(s.FL));
  await fresh({ [FK]: JSON.stringify(HAS ? { net: -0.3, z: 0, x: 0.05, tau: 4, b2: 1 } : { net: -0.3, tau: 4, b2: 1 }) }); s = await state();
  check('ключ с новыми полями (минус, ноль) читается', s.FL.net === -0.3 && (!HAS || (s.FL.z === 0 && s.FL.x === 0.05)) && s.FL.tau === 4 && s.FL.b2 === 1 && s.n === (HAS ? 5 : 3) && s.view === s.exp && (await badRows()) === 0, JSON.stringify(s.FL));
  await fresh({ [CK]: JSON.stringify(['net2', 'z', 'abc']) }); s = await pg.evaluate(() => [...window._arb.hid]); check('скрытые столбцы: net2 (его больше нет) и мусор отброшены, z скрыт', JSON.stringify(s) === '["z"]', JSON.stringify(s));

  // --- подсказки заголовков
  await fresh();
  const ths = await pg.$$eval('#th th', e => e.map(t => ({ k: t.dataset.k, h: t.dataset.h })));
  check('у каждого заголовка есть подсказка', ths.length >= 14 && ths.every(t => t.h && t.h.length > 20), JSON.stringify(ths.filter(t => !t.h)));
  check('столбца «через 250 мс» нет: ни net2, ни подсказки, ни подписи', !ths.some(t => t.k === 'net2' || /250 мс/.test(t.h)) && !(await pg.$eval('#th', e => /250 мс/.test(e.textContent))));
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

  // --- порядок и состав столбцов
  const ord2 = await pg.$$eval('#th th', e => e.map(t => t.dataset.k)); check('порядок столбцов: … tau, net (, z, x, xn)', ord2.slice(HAS ? -5 : -2).join() === (HAS ? 'tau,net,z,x,xn' : 'tau,net') && !ord2.includes('net2'), ord2.join());
  await pg.click('#ctl [data-m="out"]'); await pg.waitForTimeout(120);
  const ord3 = await pg.$$eval('#th th', e => e.map(t => t.dataset.k)); check('режим «выбросы»: те же столбцы в конце', ord3.slice(HAS ? -5 : -2).join() === (HAS ? 'tau,net,z,x,xn' : 'tau,net'), ord3.join());
  if (HAS) {
  const heads = await pg.$$eval('#th th', e => e.filter(t => ['z', 'x', 'xn'].includes(t.dataset.k)).map(t => t.textContent.replace(/\s+/g, ' ').trim()));
  check('режим «выбросы»: подписи кругов с p95 и θ 0,2 %', /круг до нуля, p95/.test(heads[0]) && /круг из − в \+, p95/.test(heads[1]) && heads.every(h => /θ 0,2 %/.test(h)), JSON.stringify(heads));
  for (const k of ['z', 'x']) { const c = await cellsOf(k); check('режим «выбросы» ' + k + ': ячейки = p95 по данным', c.n > 0 && c.bad === 0, JSON.stringify(c)) }
  }
  await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(120);
  if (HAS) {
  const heads2 = await pg.$$eval('#th th', e => e.filter(t => ['z', 'x', 'xn'].includes(t.dataset.k)).map(t => t.textContent.replace(/\s+/g, ' ').trim()));
  check('режим «медиана»: подписи', /^после комиссий, % круг до нуля θ 0,2 %$/.test(heads2[0]) && /^после комиссий, % круг из − в \+ θ 0,2 %$/.test(heads2[1]) && /^кругов из − в \+ за сутки θ 0,2 %$/.test(heads2[2]), JSON.stringify(heads2));
  }
  const pos = await pg.evaluate(() => { const rows = [...document.querySelectorAll('#tb tr.r')].slice(0, 200); const idx = k => [...document.querySelectorAll('#th th')].findIndex(t => t.dataset.k === k); let bad = 0; for (const tr of rows) for (const k of ['net', 'z', 'x']) { if (idx(k) < 0) continue; const c = tr.children[idx(k)], t = c.textContent.trim(); if ((t[0] === '+') !== c.classList.contains('pos')) bad++ } return bad });
  check('зелёным («pos») подсвечены только положительные net, z, x', pos === 0, pos);

  // --- ничего не обрезано: заголовки и ячейки, оба режима, при θ 0,2 и 0,5
  for (const t of HAS ? [20, 50] : [20]) for (const mode of ['med', 'out']) {
    await pg.evaluate((t) => window._arb.setTh(t), t); await pg.click('#ctl [data-m="' + mode + '"]'); await pg.waitForTimeout(150);
    const cl = await pg.evaluate(() => {
      const th_ = [], tall = [], td_ = []; const tr = document.querySelector('#th tr').children;
      for (const th of tr) { const t = th.getBoundingClientRect(), h = th.querySelector('.hl').getBoundingClientRect(); if (h.left < t.left - 1 || h.right > t.right + 1) th_.push(th.dataset.k + ' ' + Math.round(h.left - t.left) + '/' + Math.round(t.right - h.right)); if (t.height > 60.5) tall.push(th.dataset.k + ' h' + Math.round(t.height)) }
      const rows = [...document.querySelectorAll('#tb tr.r')].slice(0, 120);
      for (const row of rows) for (const td of [...row.children].slice(1)) if (td.scrollWidth > td.clientWidth + 0.5) td_.push(td.textContent.trim().slice(0, 22) + ' ' + td.scrollWidth + '>' + td.clientWidth);
      return { th: th_, tall, td: [...new Set(td_)].slice(0, 6), ntd: td_.length, docW: [document.documentElement.clientWidth, document.documentElement.scrollWidth] };
    });
    check(mode + ' θ ' + TL[t] + ': заголовки и ячейки (кроме монеты) не обрезаны, горизонтальной прокрутки нет, шапка ≤ 60 px', cl.th.length === 0 && cl.tall.length === 0 && cl.ntd === 0 && cl.docW[0] === cl.docW[1], JSON.stringify(cl));
  }
  await pg.evaluate(() => window._arb.setTh(20)); await pg.click('#ctl [data-m="med"]'); await pg.waitForTimeout(100);
  if (outp) { await pg.screenshot({ path: outp + '-table.png' }); await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(150); await pg.screenshot({ path: outp + '-pop.png' }) }
  check('ошибок страницы нет', errs.length === 0, errs.slice(0, 3).join(' | '));
  console.log(JSON.stringify({ W, steps, fails: fails.length, errs: errs.length }));
  fails.slice(0, 25).forEach(f => console.log('FAIL ' + f.slice(0, 400)));
  await b.close(); process.exit(fails.length ? 1 : 0);
})();
