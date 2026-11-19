# G-022: подготовка OKX (без боевого запуска) — лимиты, пул, каналы → формат, проба (12.11.2026)

Ветка `okx-ws-prep` (от `bb/tk-532-bybit-thr_tdywiip4wk`, f6e00fc7). Сырьё и скрипты проб: srv `/data/okx-probe` (кадры `frames/` + `frames/README.md`, снимок документации `docs-en.html`/`docs-en.txt` от 12.11 08:18 UTC), скрипты и итоги — `docs/findings/okx-ws-prep-2026-11-12/` (`results/` — JSON каждой пробы). Пометки: **[док]** — цитата документации, **[измерено]** — замер на srv 12.11, **[вывод]**, **[догадка]**. Время проб — UTC (GMT+4 = +4 ч).

**Что не делалось.** По слову координатора (владелец, В-148: ядро будет турбо, вероятно Rust) черновик `okx_ws.py` и `test_okx_ws.py` на Python **не писались** (п. 4 снят). Вместо адаптера — правила разбора в п. 3 с тест-векторами на реальных кадрах и записанные кадры как сырьё для гейта Rust-разбора. `wscore/` не менялся.

## 1. Лимиты OKX public WebSocket

Адрес: `wss://ws.okx.com/ws/v5/public` ([#overview-production-trading-services](https://www.okx.com/docs-v5/en/#overview-production-trading-services): «Public WebSocket: wss://ws.okx.com/ws/v5/public»). Рынок: глобальный OKX; региональные пользователи по документации идут на другие домены ([#overview](https://www.okx.com/docs-v5/en/#overview), «Important - Regional API Domain Requirement»): «US & AU users (registered on app.okx.com): Use us.okx.com as your API domain.» / «EU users (registered on my.okx.com): Use eea.okx.com as your API domain.»

| что | дословно [док] | ссылка | как соблюдаем |
|---|---|---|---|
| подключений | «Connection limit: 3 requests per second (based on IP)» | [#overview-websocket-connect](https://www.okx.com/docs-v5/en/#overview-websocket-connect) | слово «requests» в разделе Connect читаем как попытки подключения [вывод]: не более 3 в секунду с IP (общий лимитер, п. 6.1); числа «подключений на IP всего» в документации нет |
| запросов на соединение | «The total number of 'subscribe'/'unsubscribe'/'login' requests per connection is limited to 480 times per hour.» | там же | подписка пачками ≤ 100 аргументов: 346 символов × 3 вида — 4+4+11 запросов на соединение (измерено, ошибок нет); пул «на ходу» тратит эти 480/ч |
| размер запроса | «Users can choose to subscribe to one or more channels, and the total length of multiple channels cannot exceed 64 KB.» | [#overview-websocket-subscribe](https://www.okx.com/docs-v5/en/#overview-websocket-subscribe) | запрос из 100 аргументов — 5 510 знаков [измерено по файлу подписки] — в 12 раз ниже |
| подписок (аргументов) на соединение | **в документации нет числа** для public-каналов; единственное — для глубокой книги: «If you need to subscribe to many 50 or 400 depth level channels, it is recommended to subscribe through multiple websocket connections, with each of less than 30 channels.» (к нашим `bbo-tbt`/`trades`/`mark-price`… не относится) | [#order-book-trading-market-data-ws-order-book-channel](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel) | 1 038 аргументов на одном соединении подписались без ошибок [измерено, P4]; лестницы не было — число назначает координатор после неё |
| соединений на канал | «The limit will be set at 30 WebSocket connections per specific WebSocket channel per sub-account.» — только перечисленные каналы (Orders, Account, Positions, Balance and positions, Position risk warning, Account greeks) | [#overview-websocket-connection-count-limit](https://www.okx.com/docs-v5/en/#overview-websocket-connection-count-limit) | к public не относится |
| тишина 30 с | «The connection will break automatically if the subscription is not established or data has not been pushed for more than 30 seconds.» | [#overview-websocket-connect](https://www.okx.com/docs-v5/en/#overview-websocket-connect) | [измерено] без подписки и пинга сервер закрыл соединение на 30,9 с кодом 4004, причина «No data received in 30s.»; протокольный ping `ws.ping()` раз в 5 с держит соединение без подписки и без данных ≥ 70 с (12 pong, RTT 260–264 мс, один выброс 2 094 мс); текстовый `ping` раз в 20 с тоже держит (3 ответа текстом `pong`). Ядру достаточно протокольного пинга, что оно и делает |
| ping/pong | «1. Set a timer of N seconds whenever a response message is received, where N is less than 30. 2. If the timer is triggered, which means that no new message is received within N seconds, send the String 'ping'. 3. Expect a 'pong' as a response. If the response message is not received within N seconds, please raise an error or reconnect.» | там же | см. строку выше |
| плановый разрыв | «60 seconds prior to the upgrade of the WebSocket service, the notification message will be sent to users indicating that the connection will soon be disconnected.» Кадр: `{"event":"notice","code":"64008","msg":"The connection will soon be closed for a service upgrade. Please reconnect.","connId":…}` | [#overview-websocket-notification](https://www.okx.com/docs-v5/en/#overview-websocket-notification) | по `notice` 64008 открыть замену и закрыть старое после перекрытия (п. 6.3); периодического разрыва «раз в сутки» в документации нет — не найдено (grep `24 hours`/`reconnect`/`disconnect` по всему снимку) |
| порт | «WebSocket port 8443 will be retired soon. Please migrate to port 443.» (код 64010) | там же | только 443 |
| лимиты логина/подписки | «WebSocket login and subscription rate limits are based on connection.» | [#overview-rate-limits](https://www.okx.com/docs-v5/en/#overview-rate-limits) | как строка «запросов на соединение» |
| входящих сообщений в секунду | **в документации нет** (поиск `per second` по разделам WebSocket и каналов даёт только строку Connect про подключения); ошибка `60014 Requests too frequent` — про запросы клиента | — | замер: 2 327 кадров/с и 396 КБ/с на одном соединении (P4, mark) без обрыва и задержек (п. 5) |

REST для пула ([#order-book-trading-market-data-get-tickers](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers): «Rate Limit: 20 requests per 2 seconds», правило IP; [#public-data-rest-api-get-instruments](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments): «Rate Limit: 20 requests per 2 seconds», «IP + Instrument Type»; [#order-book-trading-market-data-get-candlesticks](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks): «Rate Limit: 40 requests per 2 seconds»). **Грабля [измерено]:** запрос REST без заголовка `User-Agent` (по умолчанию `Python-urllib`) получает `403 error code: 1010` (Cloudflare); со своим `User-Agent` — 200. Вебсокет-клиент `websockets` с его заголовком проходит.

**Нет в документации (искали по снимку):** числа аргументов на соединение для `bbo-tbt`/`trades`/`mark-price`/`index-tickers`/`funding-rate`; общего числа соединений с одного IP; лимита входящих сообщений в секунду; периодического разрыва по возрасту соединения.

## 2. Пул по правилу G-022

Правило: USDT-SWAP, оборот ≥ 300 тыс. $/сутки, **топ-10 не исключается** (правило топ-10 — только Binance/Bybit).

- **Источник и время [измерено]:** OKX REST `/api/v5/public/instruments?instType=SWAP` + `/api/v5/market/tickers?instType=SWAP`, снято **12.11.2026 08:28:26 UTC (12:28 GMT+4)**, `www.okx.com`. Отбор: `ctType=linear`, `settleCcy=USDT`, `state=live` — **528** контрактов (в SWAP всего 543: ещё 15 инверсных `*-USD-SWAP`, исключены).
- **Оборот** — в тикере OKX нет оборота в долларах: `volCcy24h` — «24h trading volume, with a unit of currency … If it is a derivatives contract, the value is the number of base currency» ([#order-book-trading-market-data-get-tickers](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers)). Принято: **оборот = `volCcy24h` × `last`** (один запрос). Сверка с точной суммой `volCcyQuote` 24 часовых свечей (`/market/candles?bar=1H&limit=24`, 528 запросов, 39 с): по порогу расходятся **1 символ из 346** (`STABLE-USDT-SWAP`: 305 271 $ против 295 888 $; в первом снимке 08:20 — 2 из 347). Это приближение, не оборот биржи: решение о точной сумме по свечам — за координатором (цена — 528 запросов на старте).
- **Итог [измерено]: пул 346 символов** (≥ 300 тыс. $); ниже порога — 182. Крайний — `MASK-USDT-SWAP` 300 030 $; 11 символов в полосе 300–330 тыс. $ (граница чувствительна к пересчёту). Медиана 2,39 млн $, сумма 20,4 млрд $, на топ-10 приходится 79,6 %.
- **Топ-10 (входят в пул):** BTC 5,03 млрд, ETH 4,40, SNDK 1,78, ZEC 1,58, SOXL 1,38, SOL 0,57, NVDA 0,55, XAU 0,36, PEPE 0,31, MU 0,26 млрд $.
- **Состав по `instCategory` [измерено]:** 234 крипто, **104 акции, 8 сырьё** (NVDA, MU, SNDK, XAU…). Правило «USDT-SWAP» их включает; профиль активности акций и сырья (часы базового рынка) может отличаться от крипто [догадка, не проверялось]. Исключать ли — решение координатора; здесь не исключены.
- **Метаданные для читателя:** размер контракта `ctVal` (BTC 0,01; ETH 0,1; ANTHROPIC 1…, `ctMult` = 1 у всех) — размеры в данных идут в контрактах как на бирже, пересчёт в базовую валюту — `sz × ctVal`. Файл: `docs/findings/okx-ws-prep-2026-11-12-pool.json` (`pool`, `qv`, `meta`; формат `pool-<день>.json` ядра + `meta`; на srv — `/data/okx-probe/pool-2026-11-12.json`). Скрипт — `okx-ws-prep-2026-11-12/pool.py`.

## 3. Каналы OKX → итоговый формат (docs/collector-ws-format.md)

Все каналы — `/ws/v5/public`, подписка `{"op":"subscribe","args":[{"channel":"…","instId":"…"}]}`, кадр `{"arg":{"channel","instId"},"data":[{…}]}`; у всех `data` из одного элемента [измерено: 100 % кадров во всех пробах]. Времена OKX — миллисекунды строкой.

### Выбор канала для `b` (лучшие bid/ask с размером): **`bbo-tbt`**

[док] «bbo-tbt: 1 depth level snapshot will be pushed in the initial push. Snapshot data will be pushed every 10 ms when there are changes in the 1 depth level snapshot.»; `books5` — снимок 5 уровней «every 100 ms when there are changes»; `tickers` — «The fastest rate is 1 update/100ms» ([#order-book-trading-market-data-ws-order-book-channel](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel), [#order-book-trading-market-data-ws-tickers-channel](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-tickers-channel)). Замер на одних и тех же 10 самых активных символах, одновременно, соединение 172.64.144.82, 3 мин (P2/P3) [измерено]:

| канал | кадров/с | лаг rx−ts p50 / p99, мс | отдельных состояний верха книги за 3 мин (P2, v4a) | ключ порядка |
|---|---|---|---|---|
| **bbo-tbt** | **156,5** | 132 / 143 | 28 145 (100 % изменений) | `seqId` |
| books5 | 73,4 | 133 / 142 | 11 058 (39 %) | `seqId` |
| tickers | 62,2 | 207 / 239 | — (без `seqId`) | нет |

Согласованность: у 4 489 общих `(символ, seqId)` лучшие bid/ask и размеры `bbo-tbt` и `books5` совпали во всех 4 489 (0 расхождений) [измерено] — `bbo-tbt` это верх настоящей книги. Вывод: `bbo-tbt` — самая частая (в 2,1 раза чаще `books5`, в 2,5 раза чаще `tickers`), самая быстрая (у `tickers` +75 мс), единственная с полным потоком изменений верха и с монотонным `seqId`; данные как на бирже (В-172). **Риск [док]:** в разделе SBE: «The bbo-tbt channel is available to users of any trading fee tier but requires login.» ([#order-book-trading-sbe-market-data-general-information](https://www.okx.com/docs-v5/en/#order-book-trading-sbe-market-data-general-information)) — относится к бинарной ветке `/ws/v5/public-sbe`; в JSON `/ws/v5/public` без логина `bbo-tbt` шёл во всех пробах [измерено]. Если биржа введёт логин и на JSON — запасной вариант `books5` (верх книги теряет 61 % изменений).

### Правила разбора (контракт для Rust-разбора; ключ строки формата — `codec.py`)

| запись | канал → поля формата | заметки |
|---|---|---|
| **b** | `bbo-tbt`: sym = `arg.instId` (как есть, `BTC-USDT-SWAP`); E = `data[0].ts`; **T = E** (ET = 0: второго времени у OKX нет); u = `seqId`; bid/bsz = `bids[0][0]`/`[1]`, ask/asz = `asks[0][0]`/`[1]` (строки как пришли, размер в контрактах) | **ключ дедупа — `seqId`**: одинаков на разных соединениях [измерено: 44 030 общих `(символ, seqId)` в окне 174,7 с, расхождений на краях окна 1–5 из 44 030; P3 — 0–8 из 44 281] и строго растёт по символу [измерено: 0 нарушений на 123 231 кадре P1 и 157 305 кадрах P4, кроме одного повтора ниже]. Форма проверена: ключи `asks,bids,seqId,ts`, по одному уровню, четыре элемента `[цена, размер, "0", число ордеров]`, третий всегда `"0"` [док: «is part of a deprecated feature and it is always "0"»]. Пустая сторона/другая форма → кадр в `r` (в пробах 0). **Повтор снимка при тишине [измерено]:** `FLY-USDT-SWAP` через 47,7 с без изменений прислал снимок с тем же `seqId`, теми же ценами и новым `ts` — писатель снимет его как дубль (`k ≤ last`); 1 случай на 346 символов за 3 мин |
| **t** | `trades`: sym; E = `ts`, T = E; **a = l = `tradeId`**, **f = `tradeId − count + 1`** ([док]: «The `tradeId` field in the message becomes the last trade ID in the aggregation… if `tradeId` = 123 and `count` = 3, it means the message aggregates the trades of `tradeId` = 123, 122, and 121»); p = `px`, q = `sz` (контракты), m = (`side` == "sell") — `side` = сторона тейкера, покупатель-мейкер при продаже тейкера; nq пусто | это прямой аналог `aggTrade` Binance (f..l — диапазон исходных сделок). **Ключ дедупа — `tradeId`**: 45 532 из 45 532 переходов `tradeId − предыдущий − count = 0` (P4; в P1 — 23 152 из 23 152), т. е. диапазоны смежны без пропусков; в формате `da = count − 1`, `df = 0` — обратимо и точно. `seqId` сделки не ключ: [док] «The seqId may be the same for different trade updates that occur at the same time.» Сделки с `source = "1"` (Enhanced Liquidity Program) — **в формате столбца нет** (п. 6.8) |
| **m** | **`mark-price`** (`markPx`, `ts`) + **`index-tickers`** (`idxPx`; `instId` индекса = символ без `-SWAP`, `BTC-USDT`) + **`funding-rate`** (`fundingRate`, `fundingTime`, `nextFundingTime`): запись на каждый кадр `mark-price`: E = `ts`, p = `markPx`, ap и P пусты, i = последний `idxPx`, r = последний `fundingRate`, T = `fundingTime` | состояние `i` и `r` держит адаптер по символу (как `tickers` у Bybit); пока из трёх каналов не пришло по кадру — запись не выпускать: первые кадры всех трёх каналов приходят в первые ≈ 1 с после подписки (100 из 100 символов: mark 0,6 с, funding 0,7–0,95 с, index 0,97 с) [измерено]. T: у OKX `fundingTime` — «Settlement time» ближайшего начисления (в кадре 12.11 08:24 UTC: 16:00 UTC; `prevFundingTime` — 08:00), `nextFundingTime` — следующее за ним; интервал начисления может быть 1–8 ч ([док]), по разности `fundingTime`/`nextFundingTime` читатель его видит. **Ключ дедупа — `ts` кадра `mark-price`**: на двух соединениях ключи `(канал, символ, ts)` совпали полностью — 6 947 общих, 0 только на одном (P5, окно 85,8 с); повторов `ts` у одного символа на одном соединении 0 из 312 302 (P4) |
| g | пинг ядра (`ws.ping()` → pong): RTT ≈ 231–264 мс [измерено] — это время до сервера OKX за Cloudflare, не TCP RTT (11 мс) | ядро как есть |
| c, r | `connected/subscribed/closed`; ответы `{"event":"subscribe"…}`, `{"event":"error"…}`, `{"event":"notice"…}` — без `ts`, пишутся в `r` как пришли | ядро как есть |

Тест-векторы (реальные кадры 12.11, srv, P1-проба cap1):

- `{"arg":{"channel":"bbo-tbt","instId":"BTC-USDT-SWAP"},"data":[{"asks":[["73525.1","410.62","0","32"]],"bids":[["73525","607.92","0","47"]],"ts":"1794471872705","seqId":352851720643}]}` → `("b","BTC-USDT-SWAP",1794471872705,1794471872705,352851720643,"73525","607.92","73525.1","410.62")`.
- `{"arg":{"channel":"trades","instId":"BTC-USDT-SWAP"},"data":[{"instId":"BTC-USDT-SWAP","tradeId":"3065992813","px":"73525.1","sz":"10.28","side":"buy","ts":"1794471876286","count":"6","source":"0","seqId":352851722116}]}` → `("t","BTC-USDT-SWAP",1794471876286,1794471876286,3065992813,"73525.1","10.28",3065992808,3065992813,False,"10.28")`; `side:"sell"`, `count:"1"`, `tradeId:"3065992805"`, `sz:"2.23"` → a = f = l = 3065992805, m = True.
- `mark-price` `{"instId":"BTC-USDT-SWAP","instType":"SWAP","markPx":"73525.0","ts":"1794471872430"}` при состоянии `idxPx` = `"73557.7"` (`index-tickers`, `BTC-USDT`) и `fundingRate` = `"0.0000871484324502"`, `fundingTime` = `"1794499200000"` → `("m","BTC-USDT-SWAP",1794471872430,"73525.0","","","73557.7","0.0000871484324502",1794499200000)`.

### Что остаётся за форматом

- Число ордеров на уровне (4-й элемент `bbo-tbt`) и `source` сделки (ELP: **1,5–2,3 % сделок** — 676 из 45 869 в P4, 538 из 23 252 в P1) в формат не помещаются; ET всегда 0. По правилу формата («новое поле… отдельным решением и версией, не молча») — решение координатора: расширить формат или принять потерю (п. 6.8).
- `trades-all` (каждая сделка отдельно) — канал `/ws/v5/business`: на public ответ `60018 … channel:trades-all … doesn't exist` [измерено]. `trades` OKX агрегирует по тейкерскому ордеру и цене — как `aggTrade`.
- **Расхождение с документацией [измерено]:** [док] «Data will be pushed every 200 ms when the mark price changes, and will be pushed every 10 seconds when the mark price does not change» ([#public-data-websocket-mark-price-channel](https://www.okx.com/docs-v5/en/#public-data-websocket-mark-price-channel)), на деле `mark-price` идёт **каждые ≈ 200 мс по всем 346 символам** (897–906 кадров на символ за 180 с; промежуток p50 201 мс, p99 206, макс. 216), при этом `markPx` отличается от предыдущего кадра символа только в **7,0 %** кадров (`idxPx` — в 16,2 % кадров `index-tickers`). Писать ли каждый кадр (как на бирже — 1 735 записей/с, ≈ 150 млн/сутки [вывод]) или изменения плюс сердцебиение — решение координатора (п. 6.9).

## 4. Адаптер

Не писался (см. выше). Что ядру-Rust нужно знать об адаптере — п. 3 (таблица разбора) и п. 6.

## 5. Проба: числа [измерено]

Условия: srv 5.231.25.224 (Нидерланды; Cloudflare, граница AMS, TCP connect 11 мс, TLS 51–95 мс), часы srv по chrony: смещение 0,086 мс. Лаг = приём_мс − `ts` кадра. Лаг включает неизвестное смещение часов OKX: REST `/public/time` дал `ts` OKX минус середина запроса = +18,6 мс (p50 из 30; RTT REST 283 мс, т. е. оценка ±140 мс) — отделить смещение часов от доставки нельзя. Первые 5 с после подписки в лаг не входят (начальные снимки). Проходы: окна по 180 с, все ≤ 5 соединений одновременно (P1 — 2, P2/P3 — 4, P4 — 3, P5 — 2 + P4 = 5). `User-Agent`/ключи не требуются.

### P1 — пул, как планируется кормить шарды (2 соединения, 12.11 08:29:13–08:32:13 UTC, 100 самых активных символов, подключение по DNS)

| соединение | аргументов | кадров/с | КБ/с | канал: кадров/с | лаг p50 / p99 / max, мс |
|---|---|---|---|---|---|
| A | 200 | 813,8 | 149,1 | bbo-tbt 684,7 | 123 / 136 / 201 |
| | | | | trades 129,2 | 124 / 141 / 181 |
| B | 300 | 741,6 | 129,5 | mark-price 498,0 | 129 / 143 / 247 |
| | | | | index-tickers 241,3 | 177 / 225 / 259 |
| | | | | funding-rate 2,2 | 135 / 143 / 145 |

### P4 — весь пул, по одному соединению на вид (3 соединения, 08:43:10–08:46:10 UTC, 346 символов)

| вид | аргументов | кадров/с | КБ/с | канал: кадров/с | лаг p50 / p99 / max, мс | символов с данными за 3 мин |
|---|---|---|---|---|---|---|
| book | 346 | 874,0 | 154,5 | bbo-tbt 874,0 | 118 / 137 / 256 | 346 из 346 |
| trade | 346 | 254,8 | 54,9 | trades 254,8 | 124 / 157 / 175 | 337 из 346 (молчали AEHR, FLY, GME, GPRO, HIMS, PENG и ещё 3) |
| mark | 1 038 | 2 326,9 | 396,5 | mark-price 1 735,3 | 137 / 159 / 248 | 346 из 346 |
| | | | | index-tickers 583,5 | 183 / 241 / 321 | 346 из 346 |
| | | | | funding-rate 8,1 | 139 / 161 / 178 | 346 из 346 |

Подписки: 346 / 346 / 1 038 ответов `subscribe`, событий `error` нет. Обрывов, пауз и повторных соединений в окнах нет (кадры идут до конца окна; 11 текстовых `pong` на каждое соединение — от тестового текстового пинга пробного скрипта). Это не лестница: излом соединения не искали, число аргументов на соединение не назначалось.

### Входы (P2 08:32:15 и P3 08:39:15 UTC; 4 соединения на четыре адреса `ws.okx.com`, одинаковая подписка: топ-10 × bbo-tbt, books5, tickers, trades)

DNS `ws.okx.com`: 104.18.43.174, 172.64.144.82 (IPv4); 2a06:98c1:3103::ac40:9052, 2a06:98c1:3108::6812:2bae (IPv6) — anycast Cloudflare, на srv граница AMS (`cf-ray …-AMS`). Те же адреса отдают `www.okx.com`, `openapi.okx.com`, `wspri.okx.com`, а также `wseea.okx.com` и `wsus.okx.com` (это другие рынки по документации — не использовать); `wsaws.okx.com`, `ws.okx.cab`, `ws.okx.cc` не резолвятся. **Независимой точки входа, как `stream.bybit.kz` у Bybit, у OKX нет** [измерено/вывод]: вторая линия — другой адрес того же хоста.

| окно | вход | лаг bbo-tbt p50 / p99 / max, мс | записей с лагом > 250 мс |
|---|---|---|---|
| P2 | 104.18.43.174 | 129 / 327 / 506 | **4,44 %** (1 207 из 27 172), в 18 из 18 десятисекундных корзин |
| | 172.64.144.82 | 132 / 143 / 204 | 0 |
| | 2a06:98c1:3103::ac40:9052 | 120 / 321 / 502 | **4,06 %** (1 106 из 27 212), в 18 из 18 корзин, те же корзины, что у 104.18.43.174 |
| | 2a06:98c1:3108::6812:2bae | 122 / 135 / 206 | 0 |
| P3 (повтор, порядок запуска обратный) | 104.18.43.174 | 135 / 143 / 186 | 0 |
| | 172.64.144.82 | 123 / 131 / 173 | 0 |
| | 2a06:98c1:3103::ac40:9052 | 129 / 139 / 281 | 4 (0,01 %) |
| | 2a06:98c1:3108::6812:2bae | 120 / 131 / 375 | 33 (0,12 %) |

- В P2 два соединения из четырёх шли с хвостом лага (+≈ 200 мс у ≈ 4 % записей, постоянно по окну), в P3 хвоста нет ни у одного. **Вывод:** хвост — эпизод пути или бэкенда, не свойство адреса; причину не установили, одного повторного окна мало (урок Bybit: нужна мера по записям `rx − E > 2 с` на часах работы — она в ядре есть; порога 2 с в этих окнах не достиг никто, максимум 506 мс).
- Одинаковость потока на разных соединениях [измерено]: в окне 174,7 с общих ключей 44 030, лишь на одном соединении — 1–5 (края окна); P3 — 44 281 общих, 0–8. Потерь записей между соединениями нет, дубли снимаются по `seqId` (ключ `b`).
- Разница прихода одной записи между соединениями (P3, относительно 104.18.43.174): p50 −12,1 мс (172.64.144.82), −5,3 (…3103::ac40:9052), −14,3 (…3108::6812:2bae) — в пределах единиц–десятков мс.

### Тишина и пинг (12.11 08:32–08:37 UTC)

См. строки «тишина 30 с» в п. 1. Подписка `price-limit` на тихий символ не оказалась тихой (23–26 кадров за 75 с), поэтому тест держания пингом сделан на соединениях без подписки (70 с).

## 6. Что нужно от ядра (в терминах контракта, не Python)

1. **Лимитер подключений по секундам.** У OKX лимит — 3 подключения/с на IP; нет 5-минутного окна. Сейчас `ConnectBudget(limit, window_s=300)` один на процесс, `--connect-budget` задаёт число. Нужен лимитер с окном 1 с (3; с запасом 2) и возможность задать окно адаптером; он же считает замены линий и пробы.
2. **Пинг.** Достаточно протокольного `ping` (измерено, п. 1); текстовый `ping`/`pong` не нужен. Но метрика `ping_ms` для OKX ≈ 230–260 мс — это путь до сервера OKX за Cloudflare, а не RTT TCP (11 мс); на дашборде число несравнимо с Binance/Bybit.
3. **Плановый разрыв.** Кадр `notice` с кодом 64008 приходит за 60 с до разрыва; сейчас он пишется в `r` без реакции. Нужна смена соединения по сигналу адаптера (то же перекрытие, что `rotated`). Сброс через закрытие с кодом 4004 при тишине > 30 с ядру не нужен, пока идёт пинг.
4. **Снятие дублей и сброс нумерации.** Ключи: `b` — `seqId`, `t` — `tradeId`, `m` — `ts` кадра `mark-price` (п. 3). [док] «The sequence number may be reset due to maintenance» ([#order-book-trading-market-data-ws-order-book-channel](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel), Sequence ID): после сброса `seqId` символа станет меньше записанного, и писатель (`k ≤ last_key` → дубль) отбросит все записи символа, пока нумерация не догонит. Нужен порядок сброса `last_key` (по сигналу адаптера или по `notice`/переподключению). Повтор снимка при тишине (тот же `seqId`) писатель снимает верно.
5. **Метаданные символов.** `ctVal` нужен читателю; `load_pool` возвращает только `(пул, топ)`. Нужно, чтобы ядро записывало метаданные символов рядом с `pool-<день>.json` (мой `pool.py` пишет их в `meta`).
6. **Ключ «символ с данными» для health.** `symbol(frame)` — `instId` нижним регистром (`btc-usdt-swap`); кадры `index-tickers` несут `BTC-USDT` (без `-SWAP`) — привести к символу пула.
7. **Подписка.** Сообщение `{"op":"subscribe","args":[…]}`, до 100 аргументов за запрос, пауза 0,2 с между запросами (проверено: 1 038 аргументов за 11 запросов, без `60014`). Ответ `subscribe` приходит по одному на аргумент (`event=subscribe`), ошибка — `event=error` с кодом (`60018` — неверный канал/адрес).
8. **Формат: решение координатора.** Нужны: столбец источника сделки (`source`, ELP 1,5–2,3 %), число ордеров на уровне книги; иначе потеря (а запись `source=1` в `r` рвёт непрерывность `t`: тогда `df`/`da` покажут пропуски диапазона `tradeId`).
9. **Объём `m` — решение координатора.** Каждый кадр `mark-price` (≈ 200 мс на все символы): 1 735 записей/с на 346 символов (≈ 150 млн/сутки [вывод]), против `b` 874/с (≈ 76 млн/сутки) и `t` 255/с (≈ 22 млн/сутки); изменённых `markPx` — 7,0 %.
10. **Пороги пути.** `CONNECT_RTT_MAX_MS` (300) и окно проверки `PROBE_P50_MS` (350) на OKX не мешают (RTT до Cloudflare 11 мс, лаг p50 118–183 мс), но и не ловят хвост из P2 (p99 до 335 мс); этот хвост видит только мера свежести по записям.
11. **Входы.** Один хост `ws.okx.com`, четыре адреса; ядро `dial` берёт первый адрес из `getaddrinfo` (на srv — IPv6 `2a06:98c1:3103::ac40:9052`); линии разводятся `--entrance ws.okx.com@<ip>`.

## Отклонения и не доказано

- Не сделан п. 4 (адаптер и тесты) — по указанию координатора (В-148). Скрипты проб (`cap.py`, `stats.py`, `tail.py`, `l1check.py`, `idle.py`, `pool.py`, `plan.py`) лежат в `docs/findings/okx-ws-prep-2026-11-12/` (с `results/`), пул — `docs/findings/okx-ws-prep-2026-11-12-pool.json`: вне перечня SCOPE (отчёт + два файла адаптера), добавлены как артефакты замеров; сырые кадры в репозиторий не кладутся (37 МБ, srv).
- П. 5 выполнен шире тикета «1–2 соединения»: P1 — 2 соединения как заказано; P2/P3 (входы, 4 соединения), P4 (весь пул, 3), P5 (ключи mark, 2) — дополнительные, каждое ≤ 5 одновременно, окна ≤ 3 мин (P5 — 90 с, пробы тишины — 70–75 с).
- Одно окно на каждую точку; лаг — одно время суток (12:29–12:46 GMT+4). Ступени нагрузки (излом на соединении) не искались; максимум, что выдержало соединение без ошибок, — 1 038 аргументов и 2 327 кадров/с (P4).
- Приближение оборота (`volCcy24h × last`), а не оборот биржи; точная сумма по свечам даёт 345 вместо 346.
- Хвост лага в P2 не объяснён; суточное поведение (смена суток, возможный разрыв по возрасту, сброс `seqId` при техобслуживании) вживую не наблюдалось.
- Не проверено: логин на `bbo-tbt` в JSON (сегодня не нужен), поведение при превышении 3 подключений/с.
