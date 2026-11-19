// П-42 TK-613 / TK-631: клик-тест вкладки «Прогресс» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk613_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; вкладка по умолчанию и по хешу; строки «Сейчас идёт» и «Расчёты на сервере» против data/tasks.json (состояние, имя, роль, время GMT+4 — считается
// независимо через Intl); пустые списки; нагрузка, диск, сборки; автообновление раз в 60 с (часы Playwright) и остановка на другой вкладке; предупреждения «устарели»,
// «нет связи», «ПК не передаёт»; синтетические данные со всеми состояниями; остальные вкладки живы; горизонтальный перелив; снимки. Код возврата 1, если есть провалы.
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
  const nowRows = () => page.evaluate(() => [...document.querySelectorAll('#lv-now .lv-row')].map(r => {
    const t = f => { const n = r.querySelector(`[data-f=${f}]`); return n ? n.innerText.replace(/\s+/g, ' ').trim() : null; };
    return {tk: r.dataset.task, state: r.dataset.state, badge: t('state'), name: r.querySelector('.lv-name span').innerText, role: t('role'), start: t('start'), upd: t('upd')};
  }));
  const unitRows = () => page.evaluate(() => [...document.querySelectorAll('#lv-units .lv-row')].map(r => {
    const t = f => { const n = r.querySelector(`[data-f=${f}]`); return n ? n.innerText.replace(/\s+/g, ' ').trim() : null; };
    return {name: r.dataset.unit, state: r.dataset.state, badge: t('state'), since: t('since'), end: t('end'), rc: t('rc')};
  }));
  const empties = () => page.evaluate(() => [...document.querySelectorAll('#live [data-f=empty]')].map(n => n.innerText));
  let J = null;   // первый ответ tasks.json, который получила сама страница (сверка именно с ним)
  page.on('response', async r => { if (!J && r.url().includes('data/tasks.json')) J = await r.json(); });
  await page.clock.install();
  await page.goto(base, {waitUntil: 'load'});
  await page.waitForSelector('#live .lv-h3', {timeout: 15000});
  await page.waitForFunction(() => document.querySelector('#lv-now .lv-row, #lv-now .lv-empty'), null, {timeout: 15000});
  const nowMs = await page.evaluate(() => Date.now());
  for (let k = 0; k < 50 && !J; k++) await page.waitForTimeout(100);

  // ── вкладка по умолчанию, порядок вкладок
  const labels = await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.dataset.tab + '|' + b.innerText));
  eq('первая вкладка', labels[0], 'live|Прогресс');
  const st0 = await state();
  eq('по умолчанию: выбрана и видна', [st0.hash, st0.selected, st0.visible], ['#live', ['live'], ['pane-live']]);

  // ── строки против JSON
  const NW = J.now.threads, UN = J.units;
  const RN = await nowRows();
  eq('«Сейчас идёт»: задачи и порядок', RN.map(r => r.tk), NW.map(t => t.tk || t.id));
  NW.forEach((t, i) => {
    const r = RN[i], k = t.tk || t.id;
    eq(`${k} состояние`, r.badge, {run: 'идёт', done: 'готово', fail: 'ошибка'}[t.state]);
    eq(`${k} название`, r.name, t.name);
    eq(`${k} роль`, r.role, t.role ? t.role + (t.version > 1 ? ' · v' + t.version : '') : '—');
    eq(`${k} начато (GMT+4)`, t.started_ts ? r.start.startsWith(g4(t.started_ts, nowMs)) : r.start, t.started_ts ? true : '—');
    eq(`${k} последнее действие (GMT+4)`, t.updated_ts ? r.upd.startsWith(g4(t.updated_ts, nowMs)) : r.upd, t.updated_ts ? true : '—');
  });
  eq('«Сейчас идёт»: пустой список — подпись', NW.length ? (await empties()).filter(x => x.startsWith('Сейчас')).length : (await empties())[0], NW.length ? 0 : 'Сейчас никто не работает.');
  const RU = await unitRows();
  eq('«Расчёты на сервере»: юниты и порядок', RU.map(r => r.name), UN.map(u => u.name));
  UN.forEach((u, i) => {
    const r = RU[i], k = u.name;
    eq(`${k} состояние`, r.badge, u.state === 'done' ? 'завершён' : {run: 'идёт', fail: 'ошибка'}[u.state]);
    eq(`${k} запущен (GMT+4)`, u.since_ts ? r.since.startsWith(g4(u.since_ts, nowMs)) : r.since, u.since_ts ? true : '—');
    eq(`${k} код возврата`, r.rc, u.rc == null ? '—' : String(u.rc));
  });
  eq('«Расчёты на сервере»: пустой список — подпись', (await empties()).filter(x => x.startsWith('Расчётов')).length, UN.length ? 0 : 1);
  eq('нет старых задач TK-599/607/609/611/610', await page.evaluate(() => /TK-(599|607|609|611|610)/.test(document.getElementById('live').innerText)), false);
  const meta = await page.evaluate(() => document.getElementById('lv-meta').innerText.replace(/\s+/g, ' '));
  eq('нагрузка в строке srv', meta.includes('загрузка ' + J.host.load.map(x => ru(x, 1)).join(' / ')), true);
  eq('диск в строке srv', meta.includes('свободно ' + ru(J.host.disk.free_gb, 1) + ' ГБ'), true);
  eq('сборки', (await page.evaluate(() => document.querySelector('[data-f=builds]').innerText)), 'Сборки: ' + J.host.builds.text);
  eq('строка Софии', J.host.sofia && J.host.sofia.ok ? meta.includes('София: загрузка ' + J.host.sofia.load.map(x => ru(x, 1)).join(' / ')) && meta.includes('свободно ' + ru(J.host.sofia.disk.free_gb, 1) + ' ГБ') : meta.includes('София: нет данных'), true);
  eq('подзаголовок: время сборки GMT+4', (await page.locator('#lv-sub').innerText()).includes(g4(J.generated_ts, nowMs)), true);
  C.warnTexts0 = await page.locator('.lv-warn').allInnerTexts();
  eq('нет предупреждений при свежих данных', await page.locator('.lv-warn').count(), ((nowMs / 1000 - J.generated_ts > 180) ? 1 : 0) + ((J.now.ok && nowMs / 1000 - J.now.pushed_ts > 300) ? 1 : 0));
  C.overflowX = await overflow();
  if (C.overflowX > 0) fail('горизонтальный перелив ' + C.overflowX);
  await page.screenshot({path: `${out}/live-${W}.png`, fullPage: true});

  // ── синтетика: все состояния строк, устаревшая передача ПК, пустые списки
  const T0 = nowMs / 1000;
  const fake = {generated_ts: T0 - 20, units_ok: true, host: J.host,
    now: {ok: true, pushed_ts: T0 - 900, threads: [
      {id: 't1', tk: 'TK-901', name: 'Проверка бегущей задачи', named: true, role: 'исполнитель', version: 1, state: 'run', started_ts: T0 - 7500, updated_ts: T0 - 40},
      {id: 't2', tk: 'TK-902', name: 'topic words only', named: false, role: 'проверяющий', version: 3, state: 'fail', started_ts: T0 - 4000, updated_ts: T0 - 3000},
      {id: 't3', tk: null, name: 'не по формату', named: false, role: null, version: null, state: 'done', started_ts: null, updated_ts: T0 - 600}]},
    units: [{name: 'tk901-run', state: 'run', since_ts: T0 - 3600, end_ts: null, rc: null}, {name: 'tk902-job', state: 'fail', since_ts: T0 - 1800, end_ts: T0 - 1200, rc: 3},
            {name: 'tk903-done', state: 'done', since_ts: T0 - 1800, end_ts: T0 - 600, rc: 0}]};
  await page.route('**/data/tasks.json*', route => route.fulfill({json: fake}));
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  const FN = await nowRows(), FU = await unitRows();
  eq('синтетика: строки задач', FN.map(r => [r.tk, r.state, r.badge, r.name, r.role]), [['TK-901', 'run', 'идёт', 'Проверка бегущей задачи', 'исполнитель'], ['TK-902', 'fail', 'ошибка', 'topic words only', 'проверяющий · v3'], ['t3', 'done', 'готово', 'не по формату', '—']]);
  eq('синтетика: «идёт 2 ч 05 мин» у бегущей', FN[0].start.includes('идёт 2 ч 05 мин'), true);
  eq('синтетика: время «последнее действие» бегущей', FN[0].upd.startsWith(g4(T0 - 40, nowMs)), true);
  eq('синтетика: юниты', FU.map(r => [r.name, r.state, r.badge, r.rc]), [['tk901-run', 'run', 'идёт', '—'], ['tk902-job', 'fail', 'ошибка', '3'], ['tk903-done', 'done', 'завершён', '0']]);
  eq('синтетика: у бегущего юнита нет «завершён»', FU[0].end, '—');
  eq('предупреждение «ПК не передаёт»', (await page.locator('.lv-warn').allInnerTexts()).some(x => x.includes('Список задач не обновлялся') && x.includes('ПК не передаёт')), true);
  await page.screenshot({path: `${out}/live-synthetic-${W}.png`, fullPage: true});
  fake.now.threads = []; fake.units = []; fake.now.pushed_ts = T0 - 10;
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('пусто: подписи', await empties(), ['Сейчас никто не работает.', 'Расчётов (юнитов tk6*) на сервере сейчас нет.']);
  eq('пусто: предупреждений нет', await page.locator('.lv-warn').count(), 0);
  fake.now = {ok: false, err: 'x', pushed_ts: null, threads: []};
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('now.json нет: предупреждение', (await page.locator('.lv-warn').allInnerTexts()).some(x => x.includes('Список задач ещё не получен')), true);
  await page.unroute('**/data/tasks.json*');

  // ── предупреждения: данные устарели; нет связи
  await page.route('**/data/tasks.json*', async route => { const r = await route.fetch(); const j = await r.json(); j.generated_ts -= 900; await route.fulfill({json: j}); });
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('предупреждение «устарели»', (await page.locator('.lv-warn').allInnerTexts()).some(x => x.includes('устарели')), true);
  await page.screenshot({path: `${out}/live-stale-${W}.png`});
  await page.unroute('**/data/tasks.json*');
  ignoreNet = true;
  await page.route('**/data/tasks.json*', route => route.abort());
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  eq('предупреждение «нет связи», данные остались', [(await page.locator('.lv-warn').allInnerTexts()).some(x => x.includes('Нет связи')), (await empties()).length + (await nowRows()).length > 0], [true, true]);
  await page.unroute('**/data/tasks.json*'); ignoreNet = false;
  await page.evaluate(() => loadLive()); await page.waitForTimeout(500);
  const L = await page.evaluate(() => ({d: LV.data, now: Date.now() / 1000}));
  eq('связь вернулась — предупреждения только по возрасту данных', await page.locator('.lv-warn').count(), ((L.now - L.d.generated_ts > 180) ? 1 : 0) + ((L.d.now.ok && L.now - L.d.now.pushed_ts > 300) ? 1 : 0));

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
  eq('после возврата строки на месте', (await nowRows()).length, (await page.evaluate(() => LV.data.now.threads.length)));

  // ── хеши и остальные вкладки
  for (const k of ['progress', 'targets', 'equity', 'features', 'model', 'live']) {
    await page.click(`#toptabs button[data-tab="${k}"]`); await page.waitForTimeout(350);
    const s = await state();
    eq(`вкладка ${k}`, [s.selected, s.visible], [[k], ['pane-' + k]]);
  }
  await page.goto(base + '#progress', {waitUntil: 'load'}); await page.waitForTimeout(300);
  eq('хеш #progress → «Ход работ»', (await state()).selected, ['progress']);
  await page.goto(base + '#live', {waitUntil: 'load'}); await page.waitForSelector('#live .lv-h3');
  eq('хеш #live → «Прогресс»', (await state()).selected, ['live']);
  eq('ошибок консоли', errs, []);
  rep.ok = rep.fails.length === 0 && errs.length === 0;
  fs.writeFileSync(`${out}/tk631-check-${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({width: W, ok: rep.ok, checks: Object.keys(C).length, fails: rep.fails, errors: errs}));
  await browser.close();
  process.exit(rep.ok ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(2); });
