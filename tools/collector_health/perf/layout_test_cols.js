// node layout_test_cols.js <url> [ширина окна] [префикс PNG]
// Клик-тест «Столбцы» (page-dense.html): каждый флажок в обоих режимах счёта — столбец исчез/появился, ячейки строк сходятся с шапкой,
// ширина таблицы и все элементы над ней стоят на месте (сдвиг 0), localStorage обновлён; сохранение после перезагрузки; «по умолчанию»; Esc / клик снаружи.
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], W = +(process.argv[3] || 1180), outp = process.argv[4] || '';
const KEY = 'alpha.arb.cols.hidden';
const snap = () => document.fonts.ready.then(() => {
  const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom].map(x => Math.round(x * 100) / 100) };
  const o = {}; const q = (sel, f) => document.querySelectorAll(sel).forEach(f);
  const h1 = document.querySelector('.top h1'); if (h1) o.h1 = R(h1);
  q('.tabs button', (e, i) => o['tab' + i] = R(e));
  o.clock = R(document.getElementById('clock'));
  q('#flt button', (e, i) => o['flt' + i] = R(e));
  q('#ctl button', e => o['ctl:' + e.dataset.t] = R(e));
  const cnt = document.querySelector('#ctl .cnt'); if (cnt) o.cnt = R(cnt);
  const upd = document.querySelector('#ctl .upd'); if (upd) o.upd = R(upd);
  const tw = R(document.getElementById('tw')); o['tw.left/top'] = [tw[0], tw[2], 0, 0];
  const tb = R(document.getElementById('tab')); o['table.left/top'] = [tb[0], tb[2], 0, 0];
  o.docW = [document.documentElement.clientWidth, document.documentElement.scrollWidth, 0, 0];
  return o;
});
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: W, height: 900 }, deviceScaleFactor: 1.5 });
  const errs = []; pg.on('pageerror', e => errs.push(String(e))); pg.on('console', m => { if (m.type() === 'error') errs.push(m.text()) });
  await pg.goto(url); await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => localStorage.removeItem('alpha.arb.cols.hidden')); await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => document.fonts.ready);
  const fails = [], res = []; let boxes = 0;
  const check = (name, ok, info) => { if (!ok) fails.push(name + (info ? ' — ' + info : '')) };
  const shiftOf = (a, c) => { let mx = 0, worst = ''; for (const k of Object.keys(a)) { if (!c[k]) continue; const d = Math.max(...a[k].map((x, i) => Math.abs(x - c[k][i]))); if (d > mx) { mx = d; worst = k } } return [mx, worst] };
  const state = () => pg.evaluate(KEY => {
    const A = window._arb, vis = A.vis(A.cols()), ths = [...document.querySelectorAll('#tab thead th')].map(t => t.dataset.k);
    const row = document.querySelector('#tab tbody tr.r'); const cells = row ? [...row.children].reduce((s, t) => s + (t.colSpan || 1), 0) : 0;
    const grid = vis.reduce((s, c) => s + (c.span || 1), 0);
    const cw = [...document.querySelectorAll('#cg col')].reduce((s, c) => s + c.getBoundingClientRect().width, 0);
    const tbw = document.getElementById('tab').getBoundingClientRect().width;
    const thw = {}; document.querySelectorAll('#tab thead th').forEach(t => thw[t.dataset.k] = Math.round(t.getBoundingClientRect().width * 100) / 100);
    const twr = document.getElementById('tw').getBoundingClientRect(), box = document.getElementById('p-a').getBoundingClientRect();
    return { thw, twW: twr.width, boxW: box.width - 32, ths, vis: vis.map(c => c.k), cells, grid, cw, tbw, ls: localStorage.getItem(KEY), sort: A.st.sort, mode: A.st.mode, hid: [...A.hid], n: A.view.length, open: !document.getElementById('colpop').hidden };
  }, KEY);
  const openPop = async () => { if (!(await state()).open) await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(80) };

  // 0. кнопка и поповер
  check('button exists', await pg.$('#ctl [data-cols]') != null);
  const base = await pg.evaluate(snap);
  await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(100);
  let s = await state(); check('popover opens', s.open);
  check('aria-expanded true', await pg.$eval('#ctl [data-cols]', e => e.getAttribute('aria-expanded')) === 'true');
  const pr = await pg.$eval('#colpop', e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight, getComputedStyle(e).position] });
  check('popover inside viewport', pr[0] >= 0 && pr[1] <= pr[4] && pr[3] <= pr[5], JSON.stringify(pr));
  const fixed = await pg.$$eval('#colpop input[data-ck]', e => e.filter(i => i.disabled).map(i => i.dataset.ck + ':' + i.checked));
  check('c and p fixed', JSON.stringify(fixed) === '["c:true","p:true"]', JSON.stringify(fixed));
  res.push(['popover open (шапка/панели)', ...shiftOf(base, await pg.evaluate(snap))]);
  if (outp) await pg.screenshot({ path: outp + '-popover.png' });

  // 1. каждый флажок, оба режима
  for (const mode of ['med', 'out']) {
    await pg.click('#ctl button[data-m="' + mode + '"]'); await pg.waitForTimeout(150); await openPop();
    const keys = await pg.$$eval('#colpop input[data-ck]:not([disabled])', e => e.map(i => i.dataset.ck));
    for (const k of keys) {
      boxes++;
      const before = await pg.evaluate(snap), sb = await state();
      await pg.click('#colpop input[data-ck="' + k + '"]'); await pg.waitForTimeout(120);
      const sa = await state(), after = await pg.evaluate(snap);
      check(mode + ' hide ' + k + ': th gone', !sa.ths.includes(k) && sa.ths.length === sb.ths.length - 1, JSON.stringify(sa.ths));
      check(mode + ' hide ' + k + ': cells == header', sa.cells === sa.grid && sa.cw > 0, sa.cells + '/' + sa.grid);
      check(mode + ' hide ' + k + ': columns fill table', Math.abs(sa.cw - sa.tbw) < 1, sa.cw + ' vs ' + sa.tbw);
      const kept = Object.keys(sa.thw).filter(x => x !== k && sb.thw[x] != null);
      check(mode + ' hide ' + k + ': other columns keep width', kept.length === sa.ths.length && kept.every(x => Math.abs(sa.thw[x] - sb.thw[x]) < .6), JSON.stringify(kept.filter(x => Math.abs(sa.thw[x] - sb.thw[x]) >= .6).map(x => x + ' ' + sb.thw[x] + '→' + sa.thw[x])));
      check(mode + ' hide ' + k + ': card shrinks, left kept', sa.twW < sb.twW - 20 && sa.twW <= sa.boxW + .5, sb.twW + ' → ' + sa.twW);
      check(mode + ' hide ' + k + ': stored', JSON.parse(sa.ls || '[]').includes(k), String(sa.ls));
      check(mode + ' hide ' + k + ': sort column visible', sa.vis.includes(sa.sort), sa.sort + ' in ' + sa.vis);
      check(mode + ' hide ' + k + ': rows kept', sa.n === sb.n);
      const hs = await pg.$eval('#colcnt', e => e.textContent); check(mode + ' hide ' + k + ': counter', /Показано: \d+ из \d+/.test(hs), hs);
      check(mode + ' hide ' + k + ': reset enabled', await pg.$eval('#colreset', e => !e.disabled));
      check(mode + ' hide ' + k + ': popover stays open', sa.open);
      res.push([mode + ' hide ' + k, ...shiftOf(before, after)]);
      await pg.click('#colpop input[data-ck="' + k + '"]'); await pg.waitForTimeout(120);
      const sr = await state(), back = await pg.evaluate(snap);
      check(mode + ' show ' + k + ': th back', sr.ths.includes(k) && sr.ths.length === sb.ths.length, JSON.stringify(sr.ths));
      check(mode + ' show ' + k + ': stored', !(JSON.parse(sr.ls || '[]').includes(k)), String(sr.ls));
      check(mode + ' show ' + k + ': table width', Math.abs(sr.tbw - sb.tbw) < .01, sr.tbw + ' vs ' + sb.tbw);
      check(mode + ' show ' + k + ': widths restored', Object.keys(sb.thw).every(x => sr.thw[x] != null && Math.abs(sr.thw[x] - sb.thw[x]) < .01));
      res.push([mode + ' show ' + k, ...shiftOf(before, back)]);
    }
  }
  await pg.click('#ctl button[data-m="med"]'); await pg.waitForTimeout(150);

  // 2. скрыть столбец сортировки; график при скрытых столбцах
  await openPop();
  await pg.click('#colpop input[data-ck="size"]'); await pg.waitForTimeout(150);
  s = await state(); check('hide sorted column → new sort', s.sort !== 'size' && s.vis.includes(s.sort), s.sort);
  await pg.click('#colpop input[data-ck="v0"]'); await pg.click('#colpop input[data-ck="n1"]');
  await pg.mouse.click(5, 5); await pg.waitForTimeout(100); check('outside click closes', !(await state()).open);
  await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForSelector('#cv'); await pg.waitForTimeout(500);
  const chk = await pg.evaluate(() => { const c = document.getElementById('cv'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; const td = document.querySelector('#tab tr.ch td'); const g = window._arb.vis(window._arb.cols()).reduce((s, x) => s + (x.span || 1), 0); return { n, span: td.colSpan, g, w: td.getBoundingClientRect().width, t: document.getElementById('tab').getBoundingClientRect().width } });
  check('chart drawn with hidden columns', chk.n > 2000 && chk.span === chk.g && Math.abs(chk.w - chk.t) < 1, JSON.stringify(chk));
  if (outp) { await pg.evaluate(() => window.scrollTo(0, 0)); await pg.screenshot({ path: outp + '-hidden-chart.png' }) }
  await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForTimeout(150);
  await pg.click('#ctl [data-cols]'); await pg.waitForTimeout(100); check('button reopens', (await state()).open);
  await pg.keyboard.press('Escape'); await pg.waitForTimeout(80); check('Escape closes', !(await state()).open);
  check('focus back on button', await pg.evaluate(() => document.activeElement && document.activeElement.dataset.cols === '1'));

  // 3. сохранение после перезагрузки
  const hidBefore = (await state()).hid.slice().sort();
  await pg.reload(); await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => document.fonts.ready);
  s = await state(); check('persist after reload', JSON.stringify(s.hid.slice().sort()) === JSON.stringify(hidBefore) && hidBefore.length === 3 && !s.ths.includes('size') && !s.ths.includes('v0') && !s.ths.includes('n1'), JSON.stringify(s.hid) + ' ' + JSON.stringify(s.ths));
  check('persist: sort column visible', s.vis.includes(s.sort), s.sort);
  await openPop();
  const unchecked = await pg.$$eval('#colpop input[data-ck]:not(:checked)', e => e.map(i => i.dataset.ck).sort());
  check('persist: checkboxes show state', JSON.stringify(unchecked) === JSON.stringify(hidBefore), JSON.stringify(unchecked));
  const b2 = await pg.evaluate(snap);
  // 4. «по умолчанию»
  await pg.click('#colreset'); await pg.waitForTimeout(150);
  s = await state(); const all = await pg.evaluate(() => window._arb.cols().length);
  check('reset: all columns', s.ths.length === all && s.hid.length === 0 && s.ls === null, JSON.stringify(s.ths) + ' ls=' + s.ls);
  check('reset: card back to full width', Math.abs(s.twW - s.boxW) < .6, s.twW + ' vs ' + s.boxW);
  check('reset: boxes checked', (await pg.$$eval('#colpop input[data-ck]:not(:checked)', e => e.length)) === 0);
  check('reset: button disabled', await pg.$eval('#colreset', e => e.disabled));
  res.push(['reset', ...shiftOf(b2, await pg.evaluate(snap))]);
  await pg.reload(); await pg.waitForSelector('#tab tr.r'); s = await state(); check('default after reload', s.ths.length === all && s.ls === null);
  // 5. всё скрыто (кроме монеты и пары)
  await openPop();
  const keys = await pg.$$eval('#colpop input[data-ck]:not([disabled])', e => e.map(i => i.dataset.ck));
  for (const k of keys) await pg.click('#colpop input[data-ck="' + k + '"]');
  await pg.waitForTimeout(150); s = await state();
  check('all hidden: only c,p', JSON.stringify(s.ths) === '["c","p"]' && s.sort === 'c' && s.cells === 2, JSON.stringify(s.ths) + ' ' + s.sort);
  if (outp) await pg.screenshot({ path: outp + '-minimal.png' });
  await pg.click('#colreset'); await pg.waitForTimeout(150); s = await state(); check('all hidden → reset', s.ths.length === all);
  await pg.keyboard.press('Escape');
  // 6. переход на «Здоровье» с открытым поповером
  await pg.click('#ctl [data-cols]'); await pg.click('#tab-h'); await pg.waitForTimeout(200); check('tab health closes popover', !(await state()).open);
  await pg.click('#tab-a'); await pg.waitForTimeout(200);

  // 7. изменение размера окна при скрытых столбцах: ширины пересчитываются от новой ширины, горизонтальной прокрутки нет
  await pg.click('#ctl [data-cols]');
  for (const k of ['n1', 'n2', 'b1', 'b2', 'tau']) await pg.click('#colpop input[data-ck="' + k + '"]');
  await pg.keyboard.press('Escape');
  const w0 = await state();
  await pg.setViewportSize({ width: W === 1180 ? 1440 : 1180, height: 900 }); await pg.waitForTimeout(250);
  const w1 = await state(); const dw = await pg.evaluate(() => [document.documentElement.clientWidth, document.documentElement.scrollWidth]);
  check('resize: no horizontal scroll', dw[0] === dw[1], JSON.stringify(dw));
  check('resize: card follows page width', w1.twW <= w1.boxW + .5 && (W === 1180 ? w1.twW > w0.twW : w1.twW < w0.twW), w0.twW + ' → ' + w1.twW);
  check('resize: table fills card', Math.abs(w1.cw - w1.tbw) < 1 && Math.abs(w1.tbw - (w1.twW - 2)) < 1.5, w1.cw + ' ' + w1.tbw + ' ' + w1.twW);
  await pg.setViewportSize({ width: W, height: 900 }); await pg.waitForTimeout(250);
  const w2 = await state(); check('resize back: same widths', JSON.stringify(w2.thw) === JSON.stringify(w0.thw), JSON.stringify(w2.thw));
  await pg.click('#ctl [data-cols]'); await pg.click('#colreset'); await pg.keyboard.press('Escape');

  let bad = 0;
  for (const [n, m, w] of res) if (m > 0.01) { bad++; console.log('SHIFT ' + n.padEnd(34) + 'max|Δ| = ' + m.toFixed(2) + ' px (' + w + ')') }
  console.log('width', W, '| checkbox toggles', boxes * 2, '| steps', res.length, '| with shift', bad, '| failures', fails.length, '| errors', JSON.stringify(errs));
  for (const f of fails) console.log('FAIL ' + f);
  await b.close();
})();
