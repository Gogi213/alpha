// node gate_filters_test.js <url> [ширина окна] [id биржи, по умолчанию gate]
// Фильтры пар страницы (page-dense.html): у биржи есть кнопки «… → <Биржа>» — ровно по числу таких пар в данных (/api/arb); клик по каждой оставляет в таблице только её пары (число строк
// и подпись пары совпадают с данными), «Все» возвращает всё, ряд кнопок не даёт горизонтальной прокрутки и не выходит за карточку; ошибок страницы нет. Код выхода 1 — есть сбой.
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
const url = process.argv[2], W = +(process.argv[3] || 1280), EX = process.argv[4] || 'gate';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: W, height: 900 }, deviceScaleFactor: 1 });
  const errs = []; pg.on('pageerror', e => errs.push(String(e))); pg.on('console', m => { if (m.type() === 'error') errs.push(m.text()) });
  await pg.goto(url); await pg.waitForSelector('#tab tr.r');
  await pg.evaluate(() => localStorage.clear()); await pg.reload(); await pg.waitForSelector('#tab tr.r');
  const fails = []; const check = (name, ok, info) => { if (!ok) fails.push(name + (info ? ' — ' + info : '')) };
  const data = await pg.evaluate(() => { const A = window._arb, r = A.rows; return { n: r.length, pairs: [...new Set(r.map(x => x.pc))], cnt: Object.fromEntries([...new Set(r.map(x => x.pc))].map(p => [p, r.filter(x => x.pc === p).length])), pl: Object.fromEntries(r.map(x => [x.pc, x.pl])) } });
  const want = data.pairs.filter(p => p.split('|').includes(EX));
  check('есть пары биржи ' + EX, want.length > 0, 'пар биржи: 0');
  const btns = await pg.$$eval('#flt button', bs => bs.map(x => ({ p: x.dataset.p, t: x.textContent })));
  const got = btns.filter(x => x.p !== 'all' && x.p.split('|').includes(EX)).map(x => x.p).sort();
  check('кнопки биржи = пары данных', JSON.stringify(got) === JSON.stringify([...want].sort()), got.join(',') + ' против ' + want.join(','));
  check('все кнопки пар', btns.length === data.pairs.length + 1, btns.length + ' кнопок, пар ' + data.pairs.length);
  const geo = () => pg.evaluate(() => { const f = document.getElementById('flt').getBoundingClientRect(), c = document.getElementById('p-a').getBoundingClientRect(), d = document.documentElement; return { fR: f.right, cR: c.right, sw: d.scrollWidth, cw: d.clientWidth, fh: f.height } });
  const g0 = await geo(); check('ряд фильтров не шире карточки', g0.fR <= g0.cR + 0.5, JSON.stringify(g0)); check('нет горизонтальной прокрутки', g0.sw <= g0.cw, JSON.stringify(g0));
  const info = [];
  for (const p of want) {
    await pg.click('#flt [data-p="' + p + '"]'); await pg.waitForTimeout(60);
    const s = await pg.evaluate(() => ({ rows: [...document.querySelectorAll('#tab tbody tr.r')].map(r => r.querySelector('td.p').textContent), cnt: document.querySelector('#ctl .cnt').textContent, on: document.querySelector('#flt button.on').dataset.p, view: window._arb.view.length }));
    check('«' + data.pl[p] + '»: открыта кнопка', s.on === p, s.on);
    check('«' + data.pl[p] + '»: строк в представлении', s.view === data.cnt[p], s.view + ' против ' + data.cnt[p]);
    check('«' + data.pl[p] + '»: в таблице только эта пара', s.rows.length > 0 && s.rows.every(t => t === data.pl[p]), s.rows.slice(0, 3).join(' | '));
    check('«' + data.pl[p] + '»: счётчик из ' + data.cnt[p], new RegExp('из ' + data.cnt[p] + '$').test(s.cnt), s.cnt);
    info.push(data.pl[p] + ' ' + data.cnt[p]);
    const g = await geo(); check('«' + data.pl[p] + '»: ряд фильтров стоит', Math.abs(g.fh - g0.fh) < 0.5 && g.sw <= g.cw, JSON.stringify(g));
  }
  await pg.click('#flt [data-p="all"]'); await pg.waitForTimeout(60);
  check('«Все» возвращает все строки', (await pg.evaluate(() => window._arb.view.length)) === data.n);
  check('ошибок страницы нет', errs.length === 0, errs.join(' | '));
  console.log(JSON.stringify({ width: W, exchange: EX, rows: data.n, pairs: data.pairs.length, exchange_pairs: info, fails }, null, 1));
  await b.close(); process.exit(fails.length ? 1 : 0);
})();
