// TK-646: клик-тест летописи /chron/ на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца. Образец — kb_shots.js.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node chron_shots.js http://5.231.25.224:8091 <каталог выхода> [ширина]
// Проверяет (TK-647 — и /chron/m/: фильтры, сортировка, числа записи, фильтр «числа» в списке): ошибки консоли и внешние запросы, список записей и счётчик, поиск (подстрока, регистр, ё), фильтры проект / вердикт / вид / даты,
// переход в запись (поля, «откуда поля», ссылки и обратные ссылки), раздел протокола, список и текст протокола, навигацию kb ↔ летопись,
// горизонтальное переполнение; пишет снимки и report.json; код 1 при провале.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const origin = new URL(url).origin;

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [], external = [], checks = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  page.on('request', r => { if (!r.url().startsWith(origin) && !r.url().startsWith('data:')) external.push(r.url()); });
  const check = (name, ok, got) => checks.push({name, ok: !!ok, got});
  const shot = async name => { await page.waitForTimeout(200); await page.screenshot({path: `${out}/${name}-${W}.png`, fullPage: true}); };
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const ids = () => page.evaluate(() => [...document.querySelectorAll('#list .erow .id')].map(a => a.textContent));
  const cnt = async () => parseInt((await page.textContent('#cnt')).replace(/\D/g, ''), 10);
  const go = async p => { const r = await page.goto(origin + p, {waitUntil: 'load'}); return r.status(); };
  const submit = async () => { await Promise.all([page.waitForNavigation({waitUntil: 'load'}), page.click('form.ctl button')]); };

  // ── список
  check('/chron/ HTTP 200', (await go('/chron/')) === 200);
  const total = await cnt();
  check('всего записей ≥ 224 (223 перенесено + TK-646)', total >= 224, total);
  check('на странице 100 строк и пейджер', (await ids()).length === 100 && !!(await page.$('.pager')), (await ids()).length);
  check('первая — самая новая (L-' + total + ')', (await ids())[0] === 'L-' + total, (await ids())[0]);
  check('нет переполнения (список)', (await overflow()) <= 1, await overflow());
  await shot('chron-index');

  // ── поиск: подстрока, регистр, ё
  await page.fill('#q', 'заморож'); await submit();
  const nz = await cnt();
  check('поиск «заморож» (подстрока) > 0', nz > 0, nz);
  await page.fill('#q', 'ЗАМОРОЖ'); await submit();
  check('поиск регистронезависим', (await cnt()) === nz, await cnt());
  await page.fill('#q', 'перенос журнала'); await submit();
  check('поиск находит запись TK-646', (await ids()).includes('L-224'), await ids());
  await page.fill('#q', 'несуществующее-слово-xyz'); await submit();
  check('пустой результат', (await cnt()) === 0 && /Ничего не найдено/.test(await page.textContent('#list')), await page.textContent('#list'));
  await page.fill('#q', '');

  // ── фильтры
  await page.selectOption('#project', 'П-42'); await submit();
  const n42 = await cnt();
  const badges = await page.evaluate(() => [...document.querySelectorAll('#list .erow')].every(r => /П-42/.test(r.textContent)));
  check('фильтр проект П-42: > 50 записей, у всех строк П-42', n42 > 50 && badges, n42);
  await page.selectOption('#verdict', 'не ясно'); await submit();
  const nv = await cnt();
  check('П-42 + вердикт «не ясно»: 1…n42', nv > 0 && nv < n42, nv);
  await shot('chron-filter');
  await page.selectOption('#project', ''); await page.selectOption('#verdict', '—'); await submit();
  check('вердикт «—» (не размечено) > 0', (await cnt()) > 0, await cnt());
  await page.selectOption('#verdict', ''); await page.selectOption('#kind', 'prereg'); await submit();
  check('вид prereg > 0', (await cnt()) > 0, await cnt());
  await page.selectOption('#kind', '');
  await page.fill('#since', '2026-11-20'); await page.fill('#until', '2026-11-20'); await submit();
  const nd = await cnt();
  const dates = await page.evaluate(() => [...document.querySelectorAll('#list .erow .d')].map(x => x.textContent));
  check('даты 20.11–20.11: > 0, все строки 2026-11-20', nd > 0 && dates.every(d => /2026-11-20/.test(d)), dates.slice(0, 3));
  check('фильтр в адресе', /since=2026-11-20/.test(page.url()), page.url());

  // ── запись с разобранными полями
  check('/chron/e/124 HTTP 200', (await go('/chron/e/124')) === 200);
  const e124 = await page.textContent('main');
  check('L-124: $ и сделок в полях (327, 180.83)', /сделок\s*327/.test(e124) && /180\.83/.test(e124), '');
  check('L-124: «откуда поля» есть', !!(await page.$('details.meta')), '');
  await page.click('details.meta summary');
  check('«откуда поля» раскрывается', await page.evaluate(() => document.querySelector('details.meta').open), '');
  check('нет переполнения (запись)', (await overflow()) <= 1, await overflow());
  await shot('chron-entry');
  const link = await page.$('main a[href^="/chron/s/"], main a[href^="/chron/n/"]');
  check('в записи есть ссылки на раздел / узел', !!link, '');
  if (link) {
    await Promise.all([page.waitForNavigation({waitUntil: 'load'}), link.click()]);
    check('переход по ссылке → 200 и не пусто', (await page.textContent('main')).length > 50, page.url());
  }

  // ── запись TK-646
  await go('/chron/e/224');
  const e224 = await page.textContent('main');
  check('L-224: TK-646, ссылка на коммит и CHRONICLE.md', /TK-646/.test(e224) && /f5cea9252/.test(e224) && /CHRONICLE\.md/.test(e224), '');

  // ── числа (TK-647): /chron/m/, фильтры, сортировка по заголовку, числа в записи, значок и фильтр в списке
  const mcnt = async () => parseInt((await page.textContent('#cnt')).replace(/\D/g, ''), 10);
  const msubmit = async () => { await Promise.all([page.waitForNavigation({waitUntil: 'load'}), page.click('form.ctl button')]); };
  const col = (k) => page.evaluate(k => [...document.querySelectorAll('main table tbody tr')].map(tr => tr.children[k].textContent.trim()), k);
  const val = t => t === '—' ? null : parseFloat(t.replace('−', '-'));
  check('/chron/m/ HTTP 200', (await go('/chron/m/')) === 200);
  const mt = await mcnt();
  check('чисел ≥ 14 000', mt >= 14000, mt);
  check('на странице 200 строк и пейджер', (await col(0)).length === 200 && !!(await page.$('.pager')), (await col(0)).length);
  check('в меню «Числа»', !!(await page.$('header a[href="/chron/m/"]')), '');
  check('нет переполнения (числа)', (await overflow()) <= 1, await overflow());
  await shot('chron-nums');
  await page.selectOption('#m-project', 'П-42'); await page.selectOption('#m-side', 'short'); await msubmit();
  const ms = await mcnt();
  const sides = await col(1);
  check('П-42 + шорт: 1…всего, у всех строк «шорт»', ms > 0 && ms < mt && sides.every(x => x === 'шорт'), [ms, sides.slice(0, 3)]);
  await page.selectOption('#m-uni', 'h775'); await page.fill('#m-min', '100'); await msubmit();
  const tr = (await col(5)).map(val);
  check('вселенная h775 + сделок ≥ 100', (await mcnt()) > 0 && tr.every(x => x >= 100) && (await col(3)).every(x => x === 'h775'), tr.slice(0, 3));
  await Promise.all([page.waitForNavigation({waitUntil: 'load'}), page.click('main table thead a:text("Σ $ (500 $)")')]);
  const u1 = (await col(6)).map(val).filter(x => x !== null);
  check('сортировка по заголовку Σ $ ↓', u1.length > 1 && u1.every((x, i) => i === 0 || u1[i - 1] >= x), u1.slice(0, 3));
  await Promise.all([page.waitForNavigation({waitUntil: 'load'}), page.click('main table thead a:text("Σ $ (500 $)")')]);
  const u2 = (await col(6)).map(val).filter(x => x !== null);
  check('второй щелчок — Σ $ ↑', u2.length > 1 && u2.every((x, i) => i === 0 || u2[i - 1] <= x), u2.slice(0, 3));
  await page.selectOption('#m-sort', '-sharpe'); await msubmit();
  const sh = (await col(8)).map(val).filter(x => x !== null);
  check('порядок «Шарп ↓»', sh.length > 1 && sh.every((x, i) => i === 0 || sh[i - 1] >= x), sh.slice(0, 3));
  check('фильтры в адресе', /side=short/.test(page.url()) && /uni=h775/.test(page.url()), page.url());
  await go('/chron/m/?text=1');
  check('«из текста» > 1000', (await mcnt()) > 1000, await mcnt());
  await go('/chron/m/?cell=несуществующая-клетка-xyz');
  check('пустой результат (числа)', (await mcnt()) === 0 && /Ничего не найдено/.test(await page.textContent('main')), '');
  const src1 = await (async () => { await go('/chron/m/?entry=L-120'); return page.$('main table tbody tr td:last-child a'); })();
  check('числа L-120: > 900 строк, у строки ссылка на источник', (await mcnt()) > 900 && !!src1, await mcnt());
  await shot('chron-nums-entry');
  check('/chron/e/120 HTTP 200', (await go('/chron/e/120')) === 200);
  const e120 = await page.textContent('main');
  check('запись L-120: блок «Числа (N)» и ссылка на все числа', /Числа \(\d+/.test(e120) && !!(await page.$('main a[href="/chron/m/?entry=120"]')), '');
  check('нет переполнения (запись с числами)', (await overflow()) <= 1, await overflow());
  await shot('chron-entry-nums');
  await Promise.all([page.waitForNavigation({waitUntil: 'load'}), page.click('main a[href="/chron/m/?entry=120"]')]);
  check('переход «все числа записи» → /chron/m/?entry=120', /entry=120/.test(page.url()) && (await mcnt()) > 900, page.url());
  await go('/chron/');
  await page.selectOption('#nums', 'есть'); await submit();
  const nn = await cnt();
  check('список: фильтр «числа: есть» 60…80, у всех строк значок «чисел»', nn >= 60 && nn <= 80 &&
        await page.evaluate(() => [...document.querySelectorAll('#list .erow')].every(r => /чисел \d+/.test(r.textContent))), nn);
  await page.selectOption('#nums', 'нет'); await submit();
  check('«числа: нет» + «есть» = всего', (await cnt()) + nn === total, [await cnt(), nn, total]);

  // ── протоколы
  check('/chron/p/ HTTP 200', (await go('/chron/p/')) === 200);
  const np = await page.evaluate(() => document.querySelectorAll('main .erow').length);
  check('протоколов 66', np === 66, np);
  await shot('chron-protocols');
  await go('/chron/p/P-42-level-breakout');
  const sec = await page.evaluate(() => document.querySelectorAll('section.sec').length);
  check('P-42: разделов > 100', sec > 100, sec);
  check('нет переполнения (протокол)', (await overflow()) <= 1, await overflow());
  await shot('chron-p42');
  const s1 = await page.$('section.sec a[href^="/chron/s/"]');
  await Promise.all([page.waitForNavigation({waitUntil: 'load'}), s1.click()]);
  const st = await page.textContent('main');
  check('раздел: «до счёта» / «неизменяем» и обратные ссылки', /неизменяем|черновик/.test(st) && /Ссылки|Обратные/.test(st), page.url());
  await shot('chron-section');

  // ── навигация kb ↔ летопись
  await go('/index.html');
  const nav = await page.evaluate(() => [...document.querySelectorAll('header a, nav a')].map(a => a.getAttribute('href')));
  check('kb: в навигации «Летопись» и «Протоколы»', nav.includes('/chron/') && nav.includes('/chron/p/'), nav);
  await go('/chron/');
  await Promise.all([page.waitForNavigation({waitUntil: 'load'}), page.click('header a[href="/index.html"]')]);
  check('летопись → карточки kb', /index\.html/.test(page.url()), page.url());

  check('нет ошибок консоли / запросов', errs.length === 0, errs.slice(0, 5));
  check('нет внешних запросов', external.length === 0, external.slice(0, 5));
  await browser.close();
  const fail = checks.filter(c => !c.ok);
  fs.writeFileSync(`${out}/report-${W}.json`, JSON.stringify({width: W, checks, errs, external}, null, 1));
  console.log(`chron_shots ${W}: ${checks.length - fail.length} из ${checks.length}` + (fail.length ? '\nПРОВАЛ: ' + fail.map(c => c.name + ' → ' + JSON.stringify(c.got)).join('\n  ') : ''));
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
