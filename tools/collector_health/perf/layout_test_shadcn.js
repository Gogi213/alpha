// node layout_test_shadcn.js <url> [ширина окна] [префикс PNG]
// Клик-тест вкладки «Арбитраж» (shadcn, тёмная): каждая кнопка нажимается; после действия — сдвиг элементов (px) и проверка эффекта (данные, а не только вид).
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], W = +(process.argv[3] || 1180), outp = process.argv[4] || '';
const snap = () => document.fonts.ready.then(() => {
  const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom].map(x => Math.round(x * 100) / 100) };
  const o = {};
  const q = (sel, f) => document.querySelectorAll(sel).forEach(f);
  const h1 = document.querySelector('.top h1'); if (h1) o['h1'] = R(h1);
  q('.tabs button', (e, i) => o['tab' + i] = R(e));
  const clk = document.getElementById('clock'); if (clk) o['clock'] = R(clk);
  if (!document.getElementById('p-a').hidden) {
    const ch = document.getElementById('cards-h'); if (ch) o['cards-h'] = R(ch);
    q('#cards .card', (e, i) => o['card' + i] = R(e));
    q('#ex button', (e, i) => o['ex' + i] = R(e));
    q('#ctl button', (e, i) => o['ctl:' + e.dataset.t] = R(e));
    const cnt = document.querySelector('#ctl .cnt'); if (cnt) o['cnt'] = R(cnt);
    const upd = document.querySelector('#ctl .upd'); if (upd) o['upd'] = R(upd);
    q('#tab th', e => o['th:' + (e.dataset.k || 'gh')] = R(e));
    o['tw'] = R(document.getElementById('tw'));
    o['table'] = R(document.getElementById('tab'));
    const thead = document.querySelector('#tab thead'); if (thead) o['thead'] = R(thead);
    const tr = document.querySelector('#tab tbody tr.r'); if (tr) o['row0'] = R(tr);
  }
  o['docW'] = [document.documentElement.clientWidth, 0, 0, 0];
  return o;
});
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: W, height: 800 } });
  const errs = []; pg.on('pageerror', e => errs.push(String(e))); pg.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) });
  pg.on('response', r => { if (r.status() >= 400 && !/\/favicon\.ico$/.test(r.url())) errs.push(r.status() + ' ' + r.url()) });   // на живой странице браузер просит /favicon.ico (404) — не ошибка страницы
  await pg.goto(url); await pg.waitForSelector('#tab tr.r');
  const res = [], fails = [];
  const check = (name, ok, info) => { if (!ok) fails.push(name + (info ? ' — ' + info : '')) };
  const act = async (name, fn, verify, arg) => {
    await pg.evaluate(() => window.scrollTo(0, 0));
    const a = await pg.evaluate(snap); await fn(); await pg.waitForTimeout(150);
    await pg.evaluate(() => window.scrollTo(0, 0));
    const c = await pg.evaluate(snap);
    let mx = 0, worst = '';
    for (const k of Object.keys(a)) {
      if (!c[k]) continue;
      if (/^mode /.test(name) && k.startsWith('th:')) continue;   // другой набор столбцов (режим «выбросы» добавляет «расхождение макс») — их места меняются по смыслу
      // меняют размер по смыслу: высота таблицы/обёртки, правый край «расхождения» (одна колонка ↔ две), строка графика
      const skip = (k === 'table' || k === 'tw') ? [3] : k === 'th:size' ? [1] : [];
      const d = Math.max(...a[k].map((x, i) => skip.includes(i) ? 0 : Math.abs(x - c[k][i])));
      if (d > mx) { mx = d; worst = k }
    }
    res.push([name, mx, worst]);
    if (verify) { const v = await pg.evaluate(verify, arg); check(name, v === true, String(v)) }
  };
  const click = sel => pg.click(sel);
  const state = () => pg.evaluate(() => { const A = window._arb; return { st: { ...A.st }, n: A.view.length } });

  // скрыть пустые
  await act('hide on', () => click('#ctl button[data-hide]'), () => { const A = window._arb; return A.st.hide && A.view.every(i => A.rows[i].cnt[3] > 0) });
  await act('hide off', () => click('#ctl button[data-hide]'), () => { const A = window._arb; return !A.st.hide && A.view.length === A.rows.length });
  // режимы счёта
  await act('mode out', () => click('#ctl button[data-m="out"]'), () => window._arb.st.mode === 'out' && document.querySelector('#tab th[data-k="max"]') != null);
  await act('hide on (out)', () => click('#ctl button[data-hide]'), () => window._arb.st.hide && window._arb.view.every(i => window._arb.rows[i].cnt[3] > 0));
  await act('hide off (out)', () => click('#ctl button[data-hide]'), () => !window._arb.st.hide);
  await act('mode med', () => click('#ctl button[data-m="med"]'), () => window._arb.st.mode === 'med' && document.querySelector('#tab th[data-k="max"]') == null);
  // чипы бирж: каждая выключается и включается (все комбинации — exchange_chips_test.js)
  const exs = await pg.$$eval('#ex button', e => e.map(x => x.dataset.x));
  check('есть чипы бирж', exs.length >= 3 && await pg.$('#flt') == null, exs.join(','));
  for (const x of exs) {
    await act('exchange off ' + x, () => click('#ex [data-x="' + x + '"]'), x => { const A = window._arb; return !A.sel.includes(x) && A.sel.length === A.ids.length - 1 && A.view.length > 0 && A.view.every(j => A.sel.includes(A.rows[j].a) && A.sel.includes(A.rows[j].b)) }, x);
    await act('exchange on ' + x, () => click('#ex [data-x="' + x + '"]'), x => { const A = window._arb; return A.sel.includes(x) && A.sel.length === A.ids.length && A.view.length === A.rows.length }, x);
  }
  // сортировки: каждая колонка, оба направления, оба режима
  for (const mode of ['med', 'out']) {
    await click('#ctl button[data-m="' + mode + '"]');
    const ks = await pg.$$eval('#tab th[data-k]', e => e.map(x => x.dataset.k));
    for (const k of ks) {
      for (const rev of [false, true]) {
        await act('sort ' + mode + ' ' + k + (rev ? ' (reverse)' : ''), () => click('#tab th[data-k="' + k + '"]'), () => {
          const A = window._arb, C = A.cols(), col = C.find(c => c.k === A.st.sort);
          if (A.st.sort !== '' + A.st.sort || !col) return 'no col';
          const d = A.st.dir, V = A.view.map(i => A.rows[i]);
          if (col.k === 'c' || col.k === 'p') { const lab = r => col.k === 'c' ? r.c : r.pl; for (let i = 1; i < V.length; i++) if ((lab(V[i]) < lab(V[i - 1]) ? -1 : 1) * d < 0 && lab(V[i]) !== lab(V[i - 1])) return 'order broken at ' + i; return true }
          // числовые: значения по порядку не нарушают направление (пустые — по краю)
          const vals = V.map(r => col.v(r)).filter(x => x != null);
          for (let i = 1; i < vals.length; i++) if ((vals[i] - vals[i - 1]) * d < -1e-9) return 'order broken at ' + i + ' ' + vals[i - 1] + ' ' + vals[i];
          return true;
        });
      }
    }
  }
  await click('#ctl button[data-m="med"]');
  // график: открыть, нарисован ли, переключить режим при открытом, закрыть
  await act('open row chart', async () => { await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForSelector('#cv'); await pg.waitForTimeout(500) },
    () => { const c = document.getElementById('cv'), x = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < x.length; i += 4) if (x[i]) n++; return n > 2000 || 'blank canvas ' + n });
  await act('mode out (chart open)', () => click('#ctl button[data-m="out"]'), () => window._arb.st.mode === 'out');
  await act('mode med (chart open)', () => click('#ctl button[data-m="med"]'), () => window._arb.st.mode === 'med');
  await act('close row chart', () => pg.click('#tab tr.r:nth-child(3)'), () => window._arb.st.open == null && !document.getElementById('cv'));
  // вкладки страницы
  const topBefore = await pg.evaluate(snap);
  await click('#tab-h'); await pg.waitForSelector('#rows .row', { timeout: 8000 }).catch(() => {}); await pg.waitForTimeout(100);   // /api/v3 на живой странице — до 0,4 с при остывшем кэше
  const h = await pg.evaluate(() => ({ rows: document.querySelectorAll('#rows .row').length, a: document.getElementById('p-a').hidden, ph: document.getElementById('p-h').hidden, on: document.querySelector('.tabs button.on').id }));
  check('tab health', h.rows > 0 && h.a && !h.ph && h.on === 'tab-h', JSON.stringify(h));
  const topH = await pg.evaluate(snap);
  let tabShift = 0; for (const k of ['h1', 'tab0', 'tab1', 'clock']) tabShift = Math.max(tabShift, ...topBefore[k].map((x, i) => Math.abs(x - topH[k][i])));
  res.push(['tab Арбитраж → Здоровье (шапка)', tabShift, 'h1/tabs/clock']);
  await click('#rows .row:nth-child(1)'); await pg.waitForTimeout(200);
  check('health row opens', await pg.evaluate(() => document.querySelector('#rows .row.open') != null));
  await click('#rows .row:nth-child(1)');
  await pg.hover('#rows .b >> nth=5'); await pg.waitForTimeout(200);
  check('health tooltip', await pg.evaluate(() => +getComputedStyle(document.getElementById('tip')).opacity === 1));
  await click('#tab-a'); await pg.waitForTimeout(400);
  const a2 = await pg.evaluate(snap);
  let back = 0; for (const k of ['h1', 'tab0', 'tab1', 'clock', 'card0', 'card3', 'ex0', 'ex4', 'th:c', 'table']) if (topBefore[k] && a2[k]) back = Math.max(back, ...topBefore[k].map((x, i) => (k === 'table' && i === 3) ? 0 : Math.abs(x - a2[k][i])));
  res.push(['tab Здоровье → Арбитраж (вернулись)', back, 'после возврата']);
  check('tab arb', await pg.evaluate(() => !document.getElementById('p-a').hidden && document.getElementById('tab-a').classList.contains('on')));
  let bad = 0;
  for (const [n, m, w] of res) { if (m > 0.01) bad++; console.log((m > 0.01 ? 'SHIFT ' : 'ok    ') + n.padEnd(40) + 'max|Δ| = ' + m.toFixed(2) + ' px' + (m > 0.01 ? '  (' + w + ')' : '')) }
  console.log('width', W, '| actions', res.length, '| with shift', bad, '| effect failures', fails.length, '| errors', JSON.stringify(errs));
  for (const f of fails) console.log('FAIL ' + f);
  if (outp) { await pg.evaluate(() => window.scrollTo(0, 0)); await pg.screenshot({ path: outp + '-end.png' }) }
  await b.close();
})();
