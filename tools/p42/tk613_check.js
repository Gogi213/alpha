// П-42 TK-613: клик-тест вкладки «Прогресс» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk613_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; вкладка по умолчанию и по хешу; строки DOM против data/tasks.json (состояние, %, счётчик, скорость, конец и «обновлено» по GMT+4 —
// время считается независимо через Intl); ширина полосы; нагрузка и диск; автообновление раз в 60 с (часы Playwright) и остановка на другой вкладке;
// предупреждения «устарели» и «нет связи»; остальные вкладки живы; горизонтальный перелив; снимки. Код возврата 1, если есть провалы.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
const ru = (v, d) => v.toFixed(d).replace('.', ',');
const fmt = new Intl.DateTimeFormat('ru-RU', {timeZone: 'Etc/GMT-4', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
function g4(ts, nowMs) {
  const P = ms => Object.fromEntries(fmt.formatToParts(new Date(ms)).map(p => [p.type, p.value]));
  const d = P(ts * 1000), n = P(nowMs);
  const same = d.day === n.day && d.month === n.month && d.year === n.year;
  return (same ? '' : `${d.day}.${d.month} `) + `${d.hour}:${d.minute}`;
}
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  let ignoreNet = false;   // обрыв tasks.json устраивает сам тест
  page.on('console', m => { if (m.type() === 'error' && !(ignoreNet && m.text().includes('ERR_FAILED'))) errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => { if (!r.url().includes('tasks.json')) errs.push('requestfailed: ' + r.url()); });
  let reqs = 0;
  page.on('request', r => { if (r.url().includes('data/tasks.json')) reqs++; });
  const rep = {url: base, width: W, errors: errs, checks: {}, fails: []};
  const C = rep.checks;
  const fail = m => rep.fails.push(m);
  const eq = (name, got, want) => { C[name] = got; if (JSON.stringify(got) !== JSON.stringify(want)) fail(`${name}: ${JSON.stringify(got)} ≠ ${JSON.stringify(want)}`); };
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const state = () => page.evaluate(() => ({hash: location.hash, selected: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab), visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id)}));
  const rows = () => page.evaluate(() => [...document.querySelectorAll('#live .lv-row')].map(r => {
    const t = s => { const n = r.querySelector(s); return n ? n.innerText.replace(/\s+/g, ' ').trim() : null; };
    return {id: r.dataset.task, state: t('[data-f=state]'), pct: t('[data-f=pct]'), text: t('[data-f=text]'), rate: t('[data-f=rate]'), eta: t('[data-f=eta]'), upd: t('[data-f=upd]'),
      bar: (r.querySelector('.lv-bar i') || {style: {}}).style.width || null, unk: !!r.querySelector('.lv-bar.unk'), chips: [...r.querySelectorAll('.lv-chip')].map(c => c.innerText)};
  }));
  let J = null;   // первый ответ tasks.json, который получила сама страница (сверка именно с ним)
  page.on('response', async r => { if (!J && r.url().includes('data/tasks.json')) J = await r.json(); });
  await page.clock.install();
  await page.goto(base, {waitUntil: 'load'});
  await page.waitForSelector('#live .lv-row', {timeout: 15000});
  const nowMs = await page.evaluate(() => Date.now());
  for (let k = 0; k < 50 && !J; k++) await page.waitForTimeout(100);

  // ── вкладка по умолчанию, порядок вкладок
  const labels = await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.dataset.tab + '|' + b.innerText));
  eq('первая вкладка', labels[0], 'live|Прогресс');
  const st0 = await state();
  eq('по умолчанию: выбрана и видна', [st0.hash, st0.selected, st0.visible], ['#live', ['live'], ['pane-live']]);

  // ── строки против JSON
  const R = await rows();
  eq('задачи и порядок', R.map(r => r.id), J.tasks.map(t => t.id));
  J.tasks.forEach((t, i) => {
    const r = R[i], k = t.id;
    eq(`${k} состояние`, r.state, t.state_text);
    eq(`${k} процент`, r.pct, t.pct == null ? 'всего: неизвестно' : ru(t.pct, 1) + ' %');
    eq(`${k} полоса`, t.pct == null ? r.unk : r.bar, t.pct == null ? true : t.pct + '%');
    eq(`${k} счётчик`, r.text, t.text.replace(/\s+/g, ' ').trim());
    eq(`${k} скорость`, r.rate, t.rate ? ru(t.rate, t.rate < 10 ? 1 : 0) + ' ' + t.rate_unit : '—');
    const etaHead = t.eta_ts ? g4(t.eta_ts, nowMs) + (t.eta_hi_ts ? '–' + g4(t.eta_hi_ts, nowMs) : '') : '—';
    eq(`${k} конец (GMT+4)`, r.eta.startsWith(etaHead), true);
    eq(`${k} обновлено (GMT+4)`, t.updated_ts ? r.upd.startsWith(g4(t.updated_ts, nowMs)) : r.upd, t.updated_ts ? true : '—');
    eq(`${k} этапы`, r.chips.length, t.stages ? t.stages.length : 0);
  });
  const meta = await page.evaluate(() => document.getElementById('lv-meta').innerText.replace(/\s+/g, ' '));
  eq('нагрузка в строке srv', meta.includes('загрузка ' + J.host.load.map(x => ru(x, 1)).join(' / ')), true);
  eq('диск в строке srv', meta.includes('свободно ' + ru(J.host.disk.free_gb, 1) + ' ГБ'), true);
  eq('подзаголовок: время сборки GMT+4', (await page.locator('#lv-sub').innerText()).includes(g4(J.generated_ts, nowMs)), true);
  eq('нет предупреждений при свежих данных', await page.locator('.lv-warn').count(), (nowMs / 1000 - J.generated_ts > 180) ? 1 : 0);
  C.overflowX = await overflow();
  if (C.overflowX > 0) fail('горизонтальный перелив ' + C.overflowX);
  await page.screenshot({path: `${out}/live-${W}.png`, fullPage: true});

  // ── предупреждения: данные устарели; нет связи
  await page.route('**/data/tasks.json*', async route => { const r = await route.fetch(); const j = await r.json(); j.generated_ts -= 900; await route.fulfill({json: j}); });
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('предупреждение «устарели»', (await page.locator('.lv-warn').allInnerTexts()).some(x => x.includes('устарели')), true);
  await page.screenshot({path: `${out}/live-stale-${W}.png`});
  await page.unroute('**/data/tasks.json*');
  ignoreNet = true;
  await page.route('**/data/tasks.json*', route => route.abort());
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('предупреждение «нет связи», строки остались', [(await page.locator('.lv-warn').allInnerTexts()).some(x => x.includes('Нет связи')), (await rows()).length], [true, J.tasks.length]);
  await page.unroute('**/data/tasks.json*'); ignoreNet = false;
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('связь вернулась — предупреждения нет', await page.locator('.lv-warn').count(), 0);

  // ── автообновление: ещё один запрос за 61 с, на другой вкладке — нет запросов
  const r0 = reqs;
  await page.clock.runFor(61000); await page.waitForTimeout(400);
  eq('автообновление: +1 запрос за 61 с', reqs - r0, 1);
  await page.click('#toptabs button[data-tab="progress"]');
  const r1 = reqs;
  await page.clock.runFor(130000); await page.waitForTimeout(300);
  eq('на другой вкладке запросов нет', reqs - r1, 0);
  await page.click('#toptabs button[data-tab="live"]'); await page.waitForTimeout(600);
  eq('возврат на «Прогресс»: сразу перечитывает', reqs - r1, 1);
  eq('после возврата строки на месте', (await rows()).length, J.tasks.length);

  // ── хеши и остальные вкладки
  for (const k of ['progress', 'targets', 'equity', 'features', 'model', 'live']) {
    await page.click(`#toptabs button[data-tab="${k}"]`); await page.waitForTimeout(350);
    const s = await state();
    eq(`вкладка ${k}`, [s.selected, s.visible], [[k], ['pane-' + k]]);
  }
  await page.goto(base + '#progress', {waitUntil: 'load'}); await page.waitForTimeout(300);
  eq('хеш #progress → «Ход работ»', (await state()).selected, ['progress']);
  await page.goto(base + '#live', {waitUntil: 'load'}); await page.waitForSelector('#live .lv-row');
  eq('хеш #live → «Прогресс»', (await state()).selected, ['live']);
  eq('ошибок консоли', errs, []);
  rep.ok = rep.fails.length === 0 && errs.length === 0;
  fs.writeFileSync(`${out}/tk613-check-${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({width: W, ok: rep.ok, checks: Object.keys(C).length, fails: rep.fails, errors: errs}));
  await browser.close();
  process.exit(rep.ok ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(2); });
