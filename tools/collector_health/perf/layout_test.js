// node layout_test.js <url> <outprefix>
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], outp = process.argv[3] || '';
const snap = () => document.fonts.ready.then(() => {
  const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom].map(x => Math.round(x * 100) / 100) };
  const o = {};
  document.querySelectorAll('.tabs button').forEach((e, i) => o['tab' + i] = R(e));
  document.querySelectorAll('#flt button').forEach((e, i) => o['flt' + i] = R(e));
  document.querySelectorAll('#ctl button').forEach((e, i) => o['ctl:' + e.dataset.t] = R(e));
  const cnt = document.querySelector('#ctl .cnt'); if (cnt) o['cnt'] = R(cnt);
  const upd = document.querySelector('#ctl .upd'); if (upd) o['upd'] = R(upd);
  document.querySelectorAll('#tab th').forEach(e => o['th:' + e.dataset.k] = R(e));
  const t = document.querySelector('#tab'); o['table'] = R(t);
  const thead = document.querySelector('#tab thead'); if (thead) o['thead'] = R(thead);
  const tr = document.querySelector('#tab tbody tr.r'); if (tr) { o['row0'] = R(tr); tr.querySelectorAll('td').forEach((e, i) => o['row0td' + i] = R(e)) }
  o['docW'] = [document.documentElement.clientWidth, 0, 0, 0];
  return o;
});
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: 1180, height: 800 } });
  const errs = []; pg.on('pageerror', e => errs.push(String(e))); pg.on('response', r => { if (r.status() >= 400) errs.push(r.status() + ' ' + r.url()) });
  await pg.goto(url); await pg.waitForSelector('#tab tr.r');
  const res = [];
  const act = async (name, fn) => {
    await pg.evaluate(() => window.scrollTo(0, 0));
    const a = await pg.evaluate(snap); await fn(); await pg.waitForTimeout(150);
    await pg.evaluate(() => window.scrollTo(0, 0));
    const c = await pg.evaluate(snap);
    let mx = 0, worst = '';
    for (const k of Object.keys(a)) { if (!c[k]) continue;
      // при смене счёта сравниваем только то, что есть в обоих состояниях; колонка «расхождение» меняет число ячеек, ячейки первой строки — только по позиции строки
      if (k.startsWith('row0td')) continue;
      const skip = k === 'table' ? [3] : k === 'th:size' ? [1] : [];   // высота таблицы и правый край «расхождения» (две колонки ↔ одна) меняются по смыслу
      const d = Math.max(...a[k].map((x, i) => skip.includes(i) ? 0 : Math.abs(x - c[k][i])));
      if (d > mx) { mx = d; worst = k } }
    res.push([name, mx, worst]);
  };
  const click = sel => pg.click(sel);
  await act('hide on', () => click('#ctl button[data-hide]'));
  await act('hide off', () => click('#ctl button[data-hide]'));
  await act('mode out', () => click('#ctl button[data-m="out"]'));
  await act('hide on (out)', () => click('#ctl button[data-hide]'));
  await act('mode zero', () => click('#ctl button[data-m="zero"]'));
  await act('hide on (zero)', () => click('#ctl button[data-hide]'));
  await act('hide off (zero)', () => click('#ctl button[data-hide]'));
  await act('mode med', () => click('#ctl button[data-m="med"]'));
  await act('hide off', () => click('#ctl button[data-hide]'));
  const pairs = await pg.$$eval('#flt button', e => e.length);
  for (let i = 1; i < Math.min(pairs, 4); i++) await act('pair #' + i, () => pg.click('#flt button:nth-child(' + (i + 1) + ')'));
  await act('pair all', () => click('#flt button:nth-child(1)'));
  for (const mode of ['med', 'out', 'zero']) {
    await click('#ctl button[data-m="' + mode + '"]');
    const ks = await pg.$$eval('#tab th', e => e.map(x => x.dataset.k));
    for (const k of ks) { await act('sort ' + mode + ' ' + k, () => click('#tab th[data-k="' + k + '"]')); await act('sort ' + mode + ' ' + k + ' (reverse)', () => click('#tab th[data-k="' + k + '"]')); }
  }
  await click('#ctl button[data-m="med"]');
  await act('open row chart', async () => { await pg.click('#tab tr.r:nth-child(3)'); await pg.waitForSelector('#cv'); await pg.waitForTimeout(400) });
  await act('close row chart', () => pg.click('#tab tr.r:nth-child(3)'));
  await click('#tab-h');
  const h = await pg.evaluate(() => ({ rows: document.querySelectorAll('#rows .row').length, hid: document.getElementById('p-a').hidden, ph: document.getElementById('p-h').hidden }));
  await click('#tab-a');
  console.log('health tab:', JSON.stringify(h));
  let bad = 0;
  for (const [n, m, w] of res) { if (m > 0.01) bad++; console.log((m > 0.01 ? 'SHIFT ' : 'ok    ') + n.padEnd(34) + 'max|Δ| = ' + m.toFixed(2) + ' px' + (m > 0.01 ? '  (' + w + ')' : '')) }
  console.log('actions', res.length, 'with shift', bad, 'errors', JSON.stringify(errs));
  if (outp) {
    await pg.evaluate(() => window.scrollTo(0, 0));
    await pg.screenshot({ path: outp + '-med.png' });
    await click('#ctl button[data-m="out"]'); await click('#ctl button[data-hide]');
    await pg.screenshot({ path: outp + '-out.png' });
  }
  await b.close();
})();
