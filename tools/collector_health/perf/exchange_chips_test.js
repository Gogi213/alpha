// node exchange_chips_test.js <url> [ширина окна] [префикс PNG]
// Чипы бирж в верхней панели (page-dense.html): все наборы из ≥ 2 бирж (5 бирж → 26), каждый — дважды («скрыть пустые» выкл/вкл). Для каждого набора: в таблице только пары, у которых выбраны ОБЕ биржи
// (число строк, подписи пар и счётчик «пустых: N из TOT» считаются заново по данным /api/arb), состояние чипов и localStorage, сдвиг шапки, панелей и столбцов 0, нет горизонтальной прокрутки.
// Отдельно: минимум 2 (клик по последним двум выбранным — ничего), сохранение после перезагрузки, мусор в localStorage → выбраны все, чипы в одной строке с «Арбитраж / Здоровье», старого ряда #flt нет,
// открытый график закрывается, вкладка «Здоровье» прячет чипы и возвращает на место, фокус остаётся на чипе. Код выхода 1 — есть сбой.
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], W = +(process.argv[3] || 1280), outp = process.argv[4] || '';
const KEY = 'alpha.arb.ex.hidden';
const snap = () => document.fonts.ready.then(() => {
  const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom].map(x => Math.round(x * 100) / 100) };
  const o = {}; const q = (sel, f) => document.querySelectorAll(sel).forEach(f);
  const top = document.querySelector('.top'); o.top = R(top);
  o.h1 = R(document.querySelector('.top h1'));
  q('.tabs button', (e, i) => o['tab' + i] = R(e));
  q('#ex button', (e, i) => o['ex' + i] = R(e));
  o.clock = R(document.getElementById('clock'));
  q('#ctl button', e => o['ctl:' + e.dataset.t] = R(e));
  const cnt = document.querySelector('#ctl .cnt'); if (cnt) o.cnt = R(cnt);
  const upd = document.querySelector('#ctl .upd'); if (upd) o.upd = R(upd);
  q('#tab th', e => o['th:' + e.dataset.k] = R(e));
  const tw = R(document.getElementById('tw')); o.tw = [tw[0], tw[1], tw[2], 0];
  const d = document.documentElement; o.docW = [d.clientWidth, d.scrollWidth, 0, 0];
  return o;
});
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: W, height: 900 }, deviceScaleFactor: outp ? 1.5 : 1 });
  const errs = []; pg.on('pageerror', e => errs.push(String(e))); pg.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) });
  pg.on('response', r => { if (r.status() >= 400 && !/\/favicon\.ico$/.test(r.url())) errs.push(r.status() + ' ' + r.url()) });   // на живой странице браузер просит /favicon.ico (404) — не ошибка страницы
  const load = async () => { await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => document.fonts.ready) };
  await pg.goto(url); await load(); await pg.evaluate(k => localStorage.removeItem(k), KEY); await pg.reload(); await load();
  const fails = []; const check = (name, ok, info) => { if (!ok) fails.push(name + (info ? ' — ' + info : '')) };
  const shiftOf = (a, c) => { let mx = 0, worst = ''; for (const k of Object.keys(a)) { if (!c[k]) continue; const d = Math.max(...a[k].map((x, i) => Math.abs(x - c[k][i]))); if (d > mx) { mx = d; worst = k } } return [mx, worst] };
  const data = await pg.evaluate(() => { const A = window._arb; return { ids: A.ids, rows: A.rows.map(r => ({ a: r.a, b: r.b, e: r.cnt[3] === 0 ? 1 : 0, pl: r.pl })) } });
  const ids = data.ids, n = ids.length;
  const state = () => pg.evaluate(KEY => {
    const A = window._arb, d = document.documentElement;
    const chips = [...document.querySelectorAll('#ex button')].map(x => ({ x: x.dataset.x, on: x.classList.contains('on'), pr: x.getAttribute('aria-pressed'), dis: x.getAttribute('aria-disabled') === 'true' }));
    return { sel: A.sel.slice().sort(), chips, view: A.view.length, labels: [...document.querySelectorAll('#tab tbody tr.r td.p')].map(t => t.textContent), cnt: document.querySelector('#ctl .cnt').textContent, ls: localStorage.getItem(KEY), hide: A.st.hide, sw: d.scrollWidth, cw: d.clientWidth };
  }, KEY);
  const expect = (S, hide) => { const rs = data.rows.filter(r => S.includes(r.a) && S.includes(r.b)); const tot = rs.length, ne = rs.reduce((s, r) => s + r.e, 0); return { view: hide ? rs.filter(r => !r.e).length : tot, tot, ne, labels: new Set(rs.map(r => r.pl)) } };
  const clickSet = async S => {   // сначала включаем недостающие, потом выключаем лишние — ниже 2 не опускаемся
    const cur = (await state()).sel;
    for (const x of S) if (!cur.includes(x)) await pg.click('#ex [data-x="' + x + '"]');
    for (const x of cur) if (!S.includes(x)) await pg.click('#ex [data-x="' + x + '"]');
    await pg.waitForTimeout(40);
  };

  // 0. расположение: чипы в верхней панели справа от вкладок, слева от часов, в одной строке; старого ряда нет; все выбраны
  let s = await state(), g = await pg.evaluate(snap);
  const last = 'ex' + (n - 1);
  check('чипы = биржи данных', JSON.stringify(s.chips.map(c => c.x)) === JSON.stringify(ids) && n >= 3, JSON.stringify(s.chips.map(c => c.x)));
  check('по умолчанию выбраны все, ключа нет', s.sel.length === n && s.ls === null && s.chips.every(c => c.on && c.pr === 'true' && !c.dis), JSON.stringify(s));
  check('старого ряда #flt нет', await pg.$('#flt') == null && await pg.$('.flt') == null);
  check('чипы внутри .top', await pg.evaluate(() => [...document.querySelectorAll('#ex button')].every(e => e.closest('.top'))));
  check('чипы правее вкладок, левее часов', g.ex0[0] >= g.tab1[1] && g[last][1] <= g.clock[0] - 8, JSON.stringify([g.tab1, g.ex0, g[last], g.clock]));
  check('чипы в панели по вертикали', ids.every((_, i) => g['ex' + i][2] >= g.top[2] && g['ex' + i][3] <= g.top[3] && g['ex' + i][3] - g['ex' + i][2] <= 28), JSON.stringify([g.top, g.ex0]));
  check('чипы в одну строку', new Set(ids.map((_, i) => g['ex' + i][2])).size === 1);
  check('нет горизонтальной прокрутки', s.sw <= s.cw, s.sw + '/' + s.cw);
  const exW = g[last][1] - g.ex0[0];
  if (outp) { await pg.screenshot({ path: outp + '-all.png', clip: { x: 0, y: 0, width: W, height: 140 } }) }

  // 1. все наборы ≥ 2, «скрыть пустые» выкл / вкл
  const sets = []; for (let m = 1; m < (1 << n); m++) { const S = ids.filter((_, i) => m >> i & 1); if (S.length >= 2) sets.push(S) }
  let steps = 0, shifts = 0; const info = [];
  for (const hide of [false, true]) {
    if (hide) { await clickSet(ids); await pg.click('#ctl button[data-hide]'); await pg.waitForTimeout(40) }
    for (const S of sets) {
      const before = await pg.evaluate(snap);
      await clickSet(S); await pg.waitForTimeout(60);
      const after = await pg.evaluate(snap), st = await state(), ex = expect(S, hide), tag = (hide ? 'скрыть пустые ' : '') + S.join('+');
      steps++;
      check(tag + ': выбраны', JSON.stringify(st.sel) === JSON.stringify(S.slice().sort()), JSON.stringify(st.sel));
      check(tag + ': чипы on/aria', st.chips.every(c => c.on === S.includes(c.x) && c.pr === String(S.includes(c.x))), JSON.stringify(st.chips));
      check(tag + ': минимум 2 — заперты только выбранные при 2', st.chips.every(c => c.dis === (S.length === 2 && S.includes(c.x))), JSON.stringify(st.chips.filter(c => c.dis)));
      check(tag + ': строк в представлении', st.view === ex.view && ex.view > 0, st.view + ' против ' + ex.view);
      check(tag + ': в таблице только пары выбранных', st.labels.length > 0 && st.labels.every(l => ex.labels.has(l)), st.labels.slice(0, 3).join(' | '));
      check(tag + ': счётчик', st.cnt === (hide ? 'скрыто ' : 'пустых: ') + ex.ne + ' из ' + ex.tot, st.cnt + ' против ' + ex.ne + ' из ' + ex.tot);
      const hidden = ids.filter(x => !S.includes(x));
      check(tag + ': localStorage', hidden.length ? JSON.stringify((JSON.parse(st.ls || '[]')).slice().sort()) === JSON.stringify(hidden.slice().sort()) : st.ls === null, String(st.ls));
      check(tag + ': нет горизонтальной прокрутки', st.sw <= st.cw);
      const [mx, worst] = shiftOf(before, after); if (mx > 0.01) { shifts++; console.log('SHIFT ' + tag + ' max|Δ| = ' + mx.toFixed(2) + ' (' + worst + ')') }
      if (!hide) info.push(S.length + ':' + ex.view);
    }
  }
  await clickSet(ids); await pg.click('#ctl button[data-hide]'); await pg.waitForTimeout(40);
  check('скрыть пустые снят', (await state()).hide === false);

  // 2. минимум 2: оставляем две, кликаем по ним — ничего не меняется; третью включаем — снова можно выключать
  await clickSet(ids.slice(0, 2)); const a = await state(), ge = await pg.evaluate(snap);
  for (const x of ids.slice(0, 2)) { await pg.click('#ex [data-x="' + x + '"]', { force: true }); await pg.waitForTimeout(40) }   // aria-disabled: playwright считает кнопку недоступной, клик — принудительный, как у человека
  let c = await state(); check('минимум 2: клик по выбранным ничего не меняет', JSON.stringify(c.sel) === JSON.stringify(a.sel) && c.view === a.view && c.ls === a.ls, JSON.stringify([a.sel, c.sel]));
  check('минимум 2: сдвиг 0', shiftOf(ge, await pg.evaluate(snap))[0] === 0);
  check('минимум 2: подсказка у запертых', await pg.$$eval('#ex button[aria-disabled=true]', e => e.length === 2 && e.every(x => /минимум 2/.test(x.title))));
  await pg.click('#ex [data-x="' + ids[2] + '"]'); await pg.click('#ex [data-x="' + ids[0] + '"]'); c = await state();
  check('минимум 2: после третьей выключается первая', JSON.stringify(c.sel) === JSON.stringify([ids[1], ids[2]].sort()), JSON.stringify(c.sel));

  // 3. сохранение после перезагрузки (набор не по умолчанию)
  const keep = n >= 4 ? [ids[1], ids[2], ids[n - 1]] : [ids[1], ids[2]];
  await clickSet(keep); const sb = await state();
  await pg.reload(); await load(); const sr = await state();
  check('перезагрузка: набор тот же', JSON.stringify(sr.sel) === JSON.stringify(keep.slice().sort()) && sr.view === sb.view && sr.cnt === sb.cnt, JSON.stringify(sr.sel));
  check('перезагрузка: чипы', sr.chips.every(x => x.on === keep.includes(x.x)));
  if (outp) { await pg.screenshot({ path: outp + '-three.png', clip: { x: 0, y: 0, width: W, height: 140 } }) }
  // открытый график закрывается при смене набора (как прежде при смене пары)
  await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForSelector('#cv'); await pg.waitForTimeout(300);
  await pg.click('#ex [data-x="' + ids.find(x => !keep.includes(x)) + '"]'); await pg.waitForTimeout(80);
  check('график закрылся при смене набора', await pg.evaluate(() => window._arb.st.open == null && !document.getElementById('cv')));
  await clickSet(ids); await pg.waitForTimeout(40);
  check('все выбраны → ключ удалён', (await state()).ls === null);

  // 4. мусор в localStorage → выбраны все
  for (const [name, val] of [['слом JSON', '{{'], ['скрыты все биржи', JSON.stringify(ids)], ['остаётся одна', JSON.stringify(ids.slice(1))], ['неизвестные ключи', '["nope",7,null]'], ['не массив', '{"a":1}']]) {
    await pg.evaluate(([k, v]) => localStorage.setItem(k, v), [KEY, val]); await pg.reload(); await load();
    const st = await state();
    check('мусор «' + name + '» → выбраны все', st.sel.length === n && st.view === data.rows.length && st.chips.every(x => x.on), JSON.stringify(st.sel));
  }
  await pg.evaluate(k => localStorage.removeItem(k), KEY); await pg.reload(); await load();

  // 5. вкладка «Здоровье» прячет чипы, возврат — на те же места; фокус не теряется при клике (клавиатура)
  const t0 = await pg.evaluate(snap);
  await pg.click('#tab-h'); await pg.waitForTimeout(300);
  check('Здоровье: чипы скрыты', await pg.evaluate(() => document.getElementById('ex').hidden && getComputedStyle(document.getElementById('ex')).display === 'none'));
  const th = await pg.evaluate(snap); let hs = 0; for (const k of ['top', 'h1', 'tab0', 'tab1', 'clock']) hs = Math.max(hs, ...t0[k].map((x, i) => Math.abs(x - th[k][i])));
  check('Здоровье: шапка не сдвинулась', hs === 0, String(hs));
  await pg.click('#tab-a'); await pg.waitForTimeout(300);
  check('Арбитраж: чипы на месте', shiftOf(t0, await pg.evaluate(snap))[0] === 0);
  await pg.focus('#ex [data-x="' + ids[0] + '"]'); await pg.keyboard.press('Space'); await pg.waitForTimeout(60);
  s = await state(); check('клавиатура: Space переключает, фокус остаётся', !s.sel.includes(ids[0]) && await pg.evaluate(x => document.activeElement && document.activeElement.dataset.x === x, ids[0]), JSON.stringify(s.sel));
  await pg.keyboard.press('Enter'); await pg.waitForTimeout(60);
  check('клавиатура: Enter возвращает', (await state()).sel.length === n);
  check('ошибок страницы нет', errs.length === 0, errs.join(' | '));
  if (outp) { await pg.evaluate(() => window.scrollTo(0, 0)); await pg.screenshot({ path: outp + '-end.png' }) }
  console.log(JSON.stringify({ width: W, exchanges: ids, rows: data.rows.length, combos: sets.length, steps, with_shift: shifts, chips_width_px: Math.round(exW), size_view: info.join(' '), failures: fails.length, errors: errs.length }));
  for (const f of fails) console.log('FAIL ' + f);
  await b.close(); process.exit(fails.length || shifts ? 1 : 0);
})();
