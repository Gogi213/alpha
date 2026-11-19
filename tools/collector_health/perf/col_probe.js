// node col_probe.js <url> <ширина окна> — ширины столбцов и минимальные ширины содержимого (заголовок по самой длинной строке, ячейки по самому широкому значению), оба режима
const { chromium } = require(process.env.PW || 'playwright');
const url = process.argv[2], W = +(process.argv[3] || 1180);
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true, args: ['--no-sandbox'] });
  const pg = await b.newPage({ viewport: { width: W, height: 900 }, deviceScaleFactor: 1 });
  await pg.goto(url); await pg.waitForSelector('#tab tr.r'); await pg.evaluate(() => document.fonts.ready); await pg.waitForTimeout(200);
  for (const mode of ['med', 'out']) {
    await pg.click('#ctl [data-m="' + mode + '"]'); await pg.waitForTimeout(200);
    const r = await pg.evaluate(() => {
      const ths = [...document.querySelectorAll('#th th')], rows = [...document.querySelectorAll('#tb tr.r')].slice(0, 200), out = [];
      ths.forEach((th, j) => {
        const t = th.getBoundingClientRect(), h = th.querySelector('.hl').getBoundingClientRect(); let over = 0, ex = '';
        if (j > 0) for (const tr of rows) { const td = tr.children[j]; const d = td.scrollWidth - td.clientWidth; if (d > over) { over = d; ex = td.textContent.trim() } }
        const hl_ = th.querySelector('.hl'); out.push({ lines: Math.round(hl_.getBoundingClientRect().height / 13), k: th.dataset.k, w: Math.round(t.width * 10) / 10, H: Math.round(t.height), hov: Math.round(Math.max(t.left - h.left, h.right - t.right) * 10) / 10, over, ex });
      });
      return { tw: document.getElementById('tw').getBoundingClientRect().width, doc: document.documentElement.clientWidth, cols: out };
    });
    console.log(mode, 'tw', r.tw, 'viewport', r.doc, 'sum', Math.round(r.cols.reduce((s, c) => s + c.w, 0)));
    console.log(r.cols.map(c => c.k + ':' + c.w + 'L' + c.lines + (c.H > 60 ? ' TALL' + c.H : '') + (c.hov > 0.5 ? ' HOV' + c.hov : '') + (c.over > 0.5 ? ' CELL+' + c.over + '(' + c.ex + ')' : '')).join('  '));
  }
  await b.close();
})();
