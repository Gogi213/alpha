// П-42 TK-624: счёт упоминаний «бп» на всех вкладках и видах страницы :8090 (видимый текст, title/aria-label, подсказки при наведении).
// Запуск на Софии: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk624_bpscan.js <url> <файл-отчёт.json> [ширина]
// Подсказки: MutationObserver на #tip + синтетические mousemove по каждому svg-элементу (rect/path/circle/g). Выход: число совпадений; код 1, если > 0.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const base = url.split('#')[0];
const RE = /(?:^|[^\wа-яё])(?:бп|bps?|базисн\w*)(?![\wа-яё])|\w+_bp(?![a-z])/gi;
const ROUTES = ['#progress', '#targets', '#features', '#model/long', '#model/short'];
for (const s of ['short', 'long', 'both']) for (const v of ['', '/shape', '/bases', '/where', '/comp']) ROUTES.push('#equity/' + s + v);

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: +(widthArg || 1280), height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push(e.message));
  const hits = [], seen = new Set();
  const grab = (route, kind, text) => { for (const m of (text || '').matchAll(RE)) { const ctx = text.slice(Math.max(0, m.index - 40), m.index + m[0].length + 40).replace(/\s+/g, ' '); const key = kind + '|' + ctx; if (!seen.has(key)) { seen.add(key); hits.push({route, kind, ctx}); } } };
  await page.goto(base, {waitUntil: 'load'});
  await page.evaluate(() => { window.__tips = []; new MutationObserver(() => window.__tips.push(document.getElementById('tip').innerText)).observe(document.getElementById('tip'), {childList: true, subtree: true, characterData: true}); });
  const nav = [...ROUTES];
  let n = 0;
  for (const r of nav) {
    await page.goto(base + r, {waitUntil: 'load'});
    await page.waitForTimeout(400);
    // «Признаки» и «Модель»: пройти по всем вложенным ссылкам/наборам нельзя перебором — берём весь отрисованный DOM маршрута
    const info = await page.evaluate(async () => {
      const c = document.body.cloneNode(true);
      c.querySelectorAll('script,style').forEach(x => x.remove());
      const attrs = [...document.querySelectorAll('[title],[aria-label],[alt]')].map(e => (e.getAttribute('title') || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('alt') || ''));
      const svgs = [...document.querySelectorAll('svg rect, svg path, svg circle, svg g')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; });
      let fired = 0;
      for (const e of svgs) { const b = e.getBoundingClientRect(); for (const f of [0.25, 0.75]) { e.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: b.x + b.width * f, clientY: b.y + b.height / 2})); fired++; } }
      const tips = window.__tips.splice(0);
      return {text: c.textContent, attrs, tips, svgs: svgs.length, fired, tab: location.hash};
    });
    grab(r, 'text', info.text); info.attrs.forEach(a => grab(r, 'attr', a)); info.tips.forEach(t => grab(r, 'tip', t));
    n++;
    console.log(r.padEnd(24), 'svg', String(info.svgs).padStart(5), 'tips', String(info.tips.length).padStart(5), 'hits so far', hits.length);
  }
  // «Признаки»: раскрыть каждый набор по хешу
  const sets = await page.evaluate(() => [...document.querySelectorAll('section.ft-set')].map(s => s.id.replace(/^ft-/, '')));
  for (const k of sets) { await page.goto(base + '#features/' + k, {waitUntil: 'load'}); await page.waitForTimeout(250); grab('#features/' + k, 'text', await page.evaluate(() => { const c = document.body.cloneNode(true); c.querySelectorAll('script,style').forEach(x => x.remove()); return c.textContent; })); }
  fs.writeFileSync(out, JSON.stringify({url: base, width: +(widthArg || 1280), routes: nav.length, errors: errs, hits}, null, 1));
  console.log('routes', nav.length, 'sets', sets.length, 'errors', errs.length, 'bp mentions', hits.length);
  hits.slice(0, 80).forEach(h => console.log(' ', h.route, h.kind, '…' + h.ctx + '…'));
  await browser.close();
  process.exit(hits.length ? 1 : 0);
})();
