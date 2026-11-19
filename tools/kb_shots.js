// TK-629: клик-тест сайта kb на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node kb_shots.js <url> <каталог выхода> [ширина]
// Проверяет: ошибки консоли и внешние запросы, число карточек, поиск (в т. ч. ё/регистр/дефис), фильтры тип/статус/задача и хеш, переход в карточку,
// обратные ссылки, горизонтальное переполнение, граф (узлы, наведение, клик); пишет снимки и report.json; код 1 при провале.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.replace(/#.*$/, '').replace(/index\.html$/, '').replace(/\/?$/, '/');

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [], external = [], checks = [];
  const origin = new URL(base).origin;
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  page.on('request', r => { if (!r.url().startsWith(origin) && !r.url().startsWith('data:')) external.push(r.url()); });
  const check = (name, ok, got) => checks.push({name, ok: !!ok, got});
  const shot = async name => { await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(250); await page.screenshot({path: `${out}/${name}-${W}.png`, fullPage: true}); };
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const rows = () => page.evaluate(() => [...document.querySelectorAll('#list .row .id')].map(a => a.textContent));
  const setq = async v => { await page.fill('#q', v); await page.waitForTimeout(150); };

  // ── главная
  await page.goto(base + 'index.html', {waitUntil: 'load'});
  const total = (await rows()).length;
  check('index: карточек ≥ 10', total >= 10, total);
  check('index: счётчик «N из N»', (await page.textContent('#cnt')) === `${total} из ${total}`, await page.textContent('#cnt'));
  check('index: нет горизонтального переполнения', (await overflow()) <= 1, await overflow());
  await shot('index');

  await setq('заморозка');
  let r = await rows();
  check('поиск «заморозка» → TK-626', r.includes('TK-626'), r);
  await setq('ПРОБОЕВ');
  r = await rows();
  check('поиск по регистру/словоформе → TK-625', r.includes('TK-625'), r);
  await setq('В-161');
  r = await rows();
  check('поиск «В-161» → id первым', r[0] === 'В-161', r);
  await setq('несуществующее-слово-xyz');
  check('пустой результат', (await rows()).length === 0 && /Ничего не найдено/.test(await page.textContent('#list')), await page.textContent('#list'));
  await setq('');

  await page.selectOption('#type', 'decision');
  r = await rows();
  check('фильтр тип=decision → 390 решений, В-161 среди них', r.length === 390 && r.includes('В-161'), r.length);
  check('хеш хранит фильтр', /type=decision/.test(await page.evaluate(() => location.hash)), await page.evaluate(() => location.hash));
  await page.selectOption('#type', '');
  await page.selectOption('#status', 'running');
  r = await rows();
  check('фильтр статус=running → P-42, srv, srv-pages', ['P-42', 'srv', 'srv-pages'].every(x => r.includes(x)) && !r.includes('TK-625'), r);
  await page.selectOption('#status', '');
  await page.selectOption('#task', 'TK-625');
  r = await rows();
  check('фильтр задача=TK-625 → сама, P-42, отчёт, TK-626', ['TK-625', 'P-42', 'p42-successful-breakouts', 'TK-626'].every(x => r.includes(x)) && !r.includes('srv'), r);
  await shot('index-filter-task');
  await page.selectOption('#task', '');
  await page.reload({waitUntil: 'load'});
  await page.goto(base + 'index.html#q=' + encodeURIComponent('заморозка') + '&status=done', {waitUntil: 'load'});
  r = await rows();
  check('хеш восстанавливает поиск и фильтр', (await page.inputValue('#q')) === 'заморозка' && r.includes('TK-626'), r);

  // ── карточка: клик из списка (кириллический id), ссылки, обратные ссылки
  await page.goto(base + 'index.html', {waitUntil: 'load'});
  await page.click('#list a.id:text-is("В-161")');
  await page.waitForLoadState('load');
  check('клик по В-161 открывает карточку', (await page.textContent('h1')).startsWith('В-161'), await page.textContent('h1'));
  const body161 = await page.textContent('main');
  check('В-161: обратные ссылки (правило kb-cards-not-handoffs)', /kb-cards-not-handoffs/.test(body161.split('Обратные ссылки')[1] || ''), '');
  await shot('card-decision');

  await page.goto(base + 'c/P-42.html', {waitUntil: 'load'});
  check('P-42: заголовок', (await page.textContent('h1')).startsWith('P-42'), await page.textContent('h1'));
  check('P-42: таблица в теле', (await page.locator('article table').count()) === 1, '');
  check('P-42: нет горизонтального переполнения', (await overflow()) <= 1, await overflow());
  const backs = await page.locator('h2:text-is("Обратные ссылки") + ul a').allTextContents();
  check('P-42: обратные ссылки TK-625, TK-626, p42-forward-refreeze', ['TK-625', 'TK-626', 'p42-forward-refreeze'].every(x => backs.includes(x)), backs);
  check('P-42: файл по пути — code, не ссылка', (await page.locator('code:text-is("docs/research/P-42-level-breakout.md")').count()) >= 1, '');
  await shot('card-p42');
  await page.click('article a:text-is("TK-625")');
  await page.waitForLoadState('load');
  check('клик по [[TK-625]] в теле → карточка TK-625', (await page.textContent('h1')).startsWith('TK-625'), page.url());
  await page.goBack();
  await page.click('h2:text-is("Ссылки") + ul a:text-is("TK-626")');
  await page.waitForLoadState('load');
  check('клик по ссылке из списка → TK-626', (await page.textContent('h1')).startsWith('TK-626'), page.url());
  check('TK-626: «на графе»', (await page.locator('a:text-is("на графе")').count()) === 1, '');

  // ── граф
  await page.goto(base + 'graph.html', {waitUntil: 'load'});
  const nodes = await page.locator('#graph .n').count();
  check('граф: узлов = карточек', nodes === total, nodes);
  const lines = await page.locator('#graph line.e').count();
  check('граф: рёбер ≥ 10', lines >= 10, lines);
  check('граф: легенда 7 типов', (await page.locator('#legend span').count()) === 7, '');
  check('граф: нет горизонтального переполнения', (await overflow()) <= 1, await overflow());
  await shot('graph');
  await page.locator('#graph .n[data-id="P-42"]').dispatchEvent('mouseenter');
  const dimmed = await page.locator('#graph .n.dim').count();
  check('граф: наведение гасит чужие узлы', dimmed > 0 && dimmed < nodes, dimmed);
  await shot('graph-hover');
  await page.locator('#graph .n[data-id="P-42"]').dispatchEvent('click');
  await page.waitForLoadState('load');
  check('граф: клик по узлу → карточка', (await page.textContent('h1')).startsWith('P-42'), page.url());
  await page.goto(base + 'graph.html#TK-625', {waitUntil: 'load'});
  check('граф: #TK-625 подсвечивает узел', (await page.locator('#graph .n.foc').count()) === 1, '');
  await shot('graph-focus');

  check('нет ошибок консоли/запросов', errs.length === 0, errs);
  check('нет внешних запросов', external.length === 0, external);
  const failed = checks.filter(c => !c.ok);
  fs.writeFileSync(`${out}/report-${W}.json`, JSON.stringify({url: base, width: W, total: checks.length, failed: failed.length, checks}, null, 1));
  console.log(`kb_shots ${W}px: ${checks.length - failed.length}/${checks.length} проверок`);
  failed.forEach(c => console.log('  FAIL', c.name, JSON.stringify(c.got)));
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
