# G-022: подготовка HTX (Huobi) USDT-M swap (без боевого запуска) — лимиты, пул, каналы → формат, проба (12.11.2026)

Ветка `g022-prep-3` (от `master`). Сырьё: srv `/data/htx-probe` (кадры `frames/` + `frames/README.md`; снимок документации `docs/usdt_swap_en.html`/`.txt` от 12.11 ≈ 12:45 UTC; пул `pool-2026-11-12.json`). Скрипты проб — `docs/findings/g022-prep-3-tools/` (общие) и `docs/findings/htx-ws-prep-2026-11-12/` (пул, цепочки окон, доп. разборы), итоги окон — `htx-ws-prep-2026-11-12/results/`. Пометки: **[док]** — цитата документации, **[измерено]** — замер на srv 12.11, **[вывод]**, **[догадка]**. Время проб — UTC (GMT+4 = +4 ч).

**Что не делалось.** Адаптер и тесты — нет (ядро — Rust, В-148; по заданию «без кода Rust»); боевой запуск — нет.

**Главное в четырёх строках.** (1) Пул HTX — **49 символов** из 415 торгуемых USDT-swap (19 из них акции/металлы/сырьё), топ-10 даёт 96,7 % оборота. (2) Верх книги — канал `bbo` (запись на каждое изменение; на топ-10 — в 5,3 раза больше изменений, чем у `depth.size_20.high_freq`). (3) **Примерно каждое второе новое соединение — «медленное»**: лаг растёт ≈ +0,8 с каждую секунду до 50–70 с при потолке ≈ 90–145 сообщений/с, часть таких соединений биржа рвёт; быстрые держат 360–730 сообщений/с с лагом p50 155–230 мс (15 из 27 соединений медленные) — нужен отбор соединений по лагу при подключении, как у Binance. (4) Кадры — gzip (двоичные), пинг — JSON-объект с ответным `pong`, числа в JSON — числа, не строки; у `m` три разные точки входа (рынок, индекс, уведомления).

## 1. Лимиты HTX public WebSocket

Адреса [док] (раздел «Swap WebSocket Reference» → «WebSocket Subscription Address», [huobiapi.github.io/docs/usdt_swap/v1/en](https://huobiapi.github.io/docs/usdt_swap/v1/en/)): «Market Data Request and Subscription: wss://api.hbdm.com/linear-swap-ws»; «Order Push Subscription: wss://api.hbdm.com/linear-swap-notification»; «Index Kline Data and Basis Data Subscription: wss://api.hbdm.com/ws_index»; «System status updates subscription ：wss://api.hbdm.com/center-notification». Запасные: «If the url: api.hbdm.com can't be accessed, please use the url below: Market Data Request and Subscription Address: wss://api.btcgateway.pro/linear-swap-ws» и «If your server is deployed in AWS, we recommend using "https://api.hbdm.vn"». Страница документации — старая: последняя запись её журнала изменений — 1.1.8 от 10.11.2022; в кадрах уже есть поля, которых там нет (`is_rpi_trade`, `tradfi_labels`, `enable_rpi`) [измерено].

| что | дословно [док] | ссылка | как соблюдаем / что измерено |
|---|---|---|---|
| подписок в секунду | «40 subscriptions at most can be sent in one second in websocket connections.» | [WS Reference → API Rate Limit Illustration](https://huobiapi.github.io/docs/usdt_swap/v1/en/) | подписка по одной тематике в сообщении (пакетной нет): темп ≤ 33/с (пауза 0,03 с) — 415 подписок за 12 с без ошибок [измерено] |
| запросы `req` / `sub` | «The rate limit for “req” request is 50 times/s at most. No limit for “sub” request as the data will be pushed by server voluntarily.» | там же | `req` не используем |
| подключений | «Each UID can create 30 WS connections at most for private order push at the same time.»; «Please note that the WS request connection should not go over 30 normally.» — раздел про **приватный** push ордеров | там же, «Order Push Address» | для публичных каналов **числа подключений с IP, числа подписок на соединение и размера запроса в документации нет**; за все пробы — ≈ 40 подключений с IP без отказа по лимиту, ≤ 5 одновременно; 415 подписок на одном соединении (H2-B) приняты |
| входящих сообщений в секунду | **в документации нет** | — | [измерено] быстрое соединение держало 1 393 кадра/с (415 символов `bbo`), медленное — 88–142 кадра/с (см. п. 5) |
| сердцебиение (рынок) | «WebSocket API supports two-way heartbeat. Both Server and Client can send ping message, which the opposite side can return with pong message. WebSocket Server sends heartbeat：{"ping": 18212558000} WebSocket Client should respond:：{"pong": 18212558000}»; «the server will send a heartbeat every 5 seconds (the frequency might change). The connection will get disconnected automatically if the WebSocket Client ignores the heartbeat message for 5 times. The server will remain connection if the WebSocket Client responds one “ping” value within the latest 2 heartbeat messages.» | WS Reference → «WebSocket Heartbeat and Authentication Interface» | [измерено, H3] без ответа `pong` соединение закрыто **кодом 1003 «ping check expired» на 20,9 с** (и с подпиской, и без); с ответом — живёт ≥ 100 с (20 `ping` за 100 с, т. е. раз в 5 с) |
| сердцебиение (уведомления) | «The Server initiates ping message and the Client will return pong message … {"op": "ping","ts": "1492420473058"} … WebSocket Client should return: {"op": "pong","ts": "1492420473058"}» | там же | другой формат: на `linear-swap-notification` (канал `funding_rate`) ответ `{"op":"pong","ts":…}`; на 3 мин — 26–33 пинга, обрывов нет |
| сжатие | «All response data from WebSocket server are compressed into GZIP format. Clients have to decompress them for further use.» | там же, «Data Compression» | [измерено] все кадры (и `ping`) — двоичные, gzip (заголовок `1f8b`); текстовых кадров нет |
| плановый разрыв | «During maintenance with service suspended，all websocket notify interfaces except subscribing system status updates（Subscribe system status updates）can't work，and will push 1006 error code to clients.» | WS Reference → «WebSocket interface for system status updates» | расписания нет; периодического разрыва «раз в сутки» в документации **нет**; живьём не наблюдали |
| задержка и маршруты | «It would be better if the server is located in 1c area of AWS Tokyo with url api.hbdm.vn and implement websocket re-connection mechanism.»; «The api.hbdm.vn uses AWS's CDN service … The api.hbdm.com uses Cloudflare's CDN service.» | FAQ Q3–Q4 | с srv все три имени (`api.hbdm.com`, `api.hbdm.vn`, `api.btcgateway.pro`) дают **один адрес IPv4 43.169.95.96**, IPv6 нет (п. 5) |
| REST для пула | «For restful interfaces, products, (future, coin margined swap, usdt margined Contracts)800 times/second for one IP at most» | WS Reference / Rate Limit | два запроса на пул |

**Нет в документации (искали по снимку):** числа подключений с одного IP и подписок на одно публичное соединение; размера запроса; лимита сообщений с биржи; предельной частоты подключений; периодического разрыва по возрасту соединения; разъяснения, почему часть соединений медленнее (п. 5).

## 2. Пул по правилу G-022

Правило: USDT-перпетуалы, оборот ≥ 300 тыс. $/сутки, **топ-10 не исключается**, акции/сырьё/индексы **включены**.

- **Источник и время [измерено]:** REST `GET /linear-swap-api/v1/swap_contract_info` + `GET /linear-swap-ex/market/detail/batch_merged?business_type=swap` (`api.hbdm.com`), снято **12.11.2026 12:49:48 UTC (16:49 GMT+4)**. Отбор: `contract_type = swap`, `trade_partition = USDT`, `contract_status = 1` (торгуется) — **415** контрактов (ещё 3 со статусом 3 — не торгуются, исключены). Скрипт — `htx-ws-prep-2026-11-12/pool.py`; файл — `docs/findings/htx-ws-prep-2026-11-12-pool.json` (`pool`, `qv`, `meta`; на srv — `/data/htx-probe/pool-2026-11-12.json`).
- **Оборот** — поле `trade_turnover` из `batch_merged`; [док]: «Transaction amount, that is, sum (transaction quantity * contract face value * transaction price),from nowtime - 24 hours. Sum of both buy and sell sides» — котируемая валюта (USDT), 24 ч скользящие. Другого источника не сверяли.
- **Итог [измерено]: пул 49 символов** (≥ 300 тыс. $); ниже порога — 366. Крайние: `MSTRX-USDT` 304 916 $, `AAVE-USDT` 311 857, `AVAX-USDT` 327 371 (в полосе 300–330 тыс. $ — 3 символа). Медиана 1,08 млн $, сумма 1,28 млрд $, **на топ-10 приходится 96,7 %**.
- **Топ-10 (входят):** ETH 665 млн, BTC 473 млн, ZEC 38,3 млн, SOL 19,7, SNDK 11,8, XAU 11,5, DOGE 11,1, USOIL 3,6, ADA 3,55, SOXL 3,1 млн $ (ETH выше BTC — как отдал `batch_merged`, не пересчитывали).
- **Состав [измерено]:** 30 крипто и **19 «tradfi»** по `tradfi_labels` (акции 13, металлы 4, сырьё 2: NVDA, MU, SNDK, XAU, XAG, USOIL, NATGAS, SOXL …); правило «USDT-перпетуал» их включает.
- **Метаданные для читателя:** `contract_size` (размер контракта в монете: BTC 0,001; ETH 0,01; XAU 0,001; JCT 100), `price_tick`, `settlement_period` (цикл фандинга, ч: 8 или 4), `labels`, `tradfi_labels`, `enable_rpi` — в `meta` пула. **Размеры в данных идут в контрактах**; пересчёт в монету — `amount × contract_size` (в сделках биржа присылает и `quantity` в монете).

## 3. Каналы HTX → итоговый формат (docs/collector-ws-format.md)

Подписка: рынок — `{"sub":"market.<CODE>.<kind>","id":"…"}` (код символа — `BTC-USDT`, как есть); ответ `{"id":…,"status":"ok","subbed":"…","ts":…}`; ошибка `{"status":"error","err-code":"bad-request","err-msg":"invalid topic …"}`. Уведомления — `{"op":"sub","topic":"public.<CODE|*>.funding_rate","cid":"…"}`. Кадр данных — `{"ch":"market.<CODE>.<kind>","ts":<мс>,"tick":{…}}`. **Числа в `bbo`, `trade.detail`, `depth.*`, `detail` — JSON-числа без кавычек** (в `mark_price`/`index` — строки); читать нужно с сохранением текста числа как пришёл (В-172).

### Выбор канала для `b` (лучшие bid/ask с размером): **`bbo`**

[док] «When any one of the buy_one price, buy_one quantity, sell_one price and sell_one quantity changes, the system will push BBO price. If there are multiple changes in the price or quantity of buy_one or sell_one at the same time, the system will push the latest price and quantity of buy_one and sell one with the intermediate data discarded. When the data received by the client is failed or delayed, the old data buffer in the server will be discarded. The latest BBO will be pushed.» («Subscribe market BBO data push»); глубина: «Snapshot MBP data is checked every 100ms,pushed at least every 1s. Incremental MBP data is checked every 30ms. It will not be pushed,if MBP has no update.» (FAQ Q12). Замер на **одних и тех же 10 самых активных символах, одно соединение, 3 мин** (H1-D, 13:20:11–13:23:11 UTC, «быстрое» соединение) [измерено]:

| канал | кадров/с | лаг rx−E p50 / p99, мс | записей за окно | разных состояний верха |
|---|---|---|---|---|
| **`bbo`** | **479,0** | 155 / 530 | 84 593 | 84 593 (100 %) |
| `depth.size_20.high_freq` | 177,4 | 144 / 486 | 31 198 | 15 951 |
| `depth.step6` (20 уровней, снимок) | 88,6 | 138 / 474 | 15 530 | 8 585 |
| `detail` (24 ч) | 2,2 | 142 / 528 | 371 | 370 |

Вывод: `bbo` — единственный канал, где каждая запись — смена верха (в 5,3 раза больше смен, чем в `high_freq`, в 9,9 раза — чем в `step6`); верх у остальных каналов — первый уровень глубины. Данные как на бирже: между изменениями промежуточные состояния биржей отбрасываются (док. выше) — «tick-by-tick» здесь не «каждая сделка», а «каждое изменение верха с конфляцией на стороне биржи».

### Правила разбора (контракт для Rust-разбора)

| запись | канал → поля формата | заметки |
|---|---|---|
| **b** | `bbo`: sym = код как есть (`ETH-USDT`); **E = `ts` кадра**, **T = `tick.ts`** («Time of Respond Generation»; E − T p50 2 мс, p99 12 мс); u = `tick.version` (= `mrid`); bid/bsz = `tick.bid[0]`/`[1]`, ask/asz = `tick.ask[0]`/`[1]` — числа как в JSON (размер — в контрактах) | **ключ дедупа — `version`**: на трёх соединениях в одном окне 15 582 общих ключа, 27 и 0 только на одном (края окна; X3, 45,5 с); строго растёт по символу (0 нарушений: F1-A — 130 898 записей, H2-B — 250 281). **Одностороннее `bbo`:** `bid` или `ask` пуст/отсутствует — 1 из 130 898 (пул) и 45 из 250 281 (весь рынок) → пустая сторона в формате или `r` (решение п. 6.7). **Первый кадр после подписки несёт старое время**: у 113 из 415 символов лаг первой записи 5–103 с (последнее изменение до подписки; не задержка доставки) |
| **t** | `trade.detail`: `tick.data[]` — записи `{amount, quantity, trade_turnover, ts, id, price, direction, is_rpi_trade}`; sym; T = `data.ts`, E = `ts` кадра; **a = f = l = `id`** (на символе уникален, `id = tick.id × 10 000 + номер в кадре`); p = `price`; q = `amount` (**контракты**, как на бирже), nq = `quantity` (монета); **m = (direction == "sell")** (`direction` — сторона тейкера) | `quantity` и `trade_turnover` (USDT) — производные от `amount × contract_size` [док]; **`is_rpi_trade` — флаг типа сделки от биржи** (true у 1,2–2,6 % сделок: 16 из 1 341 в F1-B, 52 из 1 979 в H1-B), в формате столбца нет (п. 6.7). Ключ дедупа — `id` (по символу; между соединениями не сверяли: сделок мало — 7,5 зап/с на пул). [док]: «Clients can only access the recent 300 trade detail data» (для `req`); при подписке — несколько недавних записей (72 записи за первые 3 с на 49 символах при фоне ≈ 7,5/с — проверить возраст, п. «Не доказано») |
| **m** | **три потока.** `p` — `market.<CODE>.mark_price.1min` на **`wss://api.hbdm.com/ws_index`** (свеча mark-цены; `tick.close` — текущая mark; кадр раз в 1,000 с на символ; `close` меняется в **51,4 %** кадров); `i` — `market.<CODE>.index.1min` там же (`tick.close` — индекс; раз в 1 с; меняется в 77,2 %); `r` и `T` — `public.<CODE>.funding_rate` на **`wss://api.hbdm.com/linear-swap-notification`**: `data[].funding_rate`, `data[].settlement_time` (время следующего начисления, 16:00 UTC в кадре 13:20) | запись по кадру `mark_price` (1 Гц на символ): E = `ts` кадра, p = `tick.close`; `i`, `r`, `T` — последнее известное по символу; `ap`, `P` пусты. **`mark_price` на `linear-swap-ws` не принимается** (`invalid topic`, 49 из 49) — только на `ws_index`. **`funding_rate` с `*`** присылает **все 434 символа одним кадром раз в ≈ 8 с**; `funding_rate` меняется в 13,3 % записей (1 165 из 8 778), `funding_time` в записи — время рассылки (меняется каждый раз), `estimated_rate` — `null`. Дедуп: `ts` кадра (ключ `(символ, ts)`: повторов 48 из 8 979 у `mark_price` и 49 из 8 857 у `index`) |
| g | пинг ядра: `{"pong":ts}` на `{"ping":ts}` сервера (раз в 5 с) | RTT клиент–сервер из этого не измерить (меряется только ответ); REST `/api/v1/timestamp`: смещение −46,9 мс (p50, размах −625…+282 мс), RTT 381 мс |
| c, r | `connected/subscribed/closed`; ответы `status: ok/error` — в `r` как пришли | `closed` без кода (EOF) или код 1003 «ping check expired» |

Тест-векторы (реальные кадры 12.11, srv; в gzip-кадре после распаковки):

- `bbo`: `{"ch":"market.ETH-USDT.bbo","ts":1794489612549,"tick":{"mrid":100119112482604,"id":1794489612,"bid":[2008.15,5372],"ask":[2008.16,4294],"ts":1794489612548,"version":100119112482604,"ch":"market.ETH-USDT.bbo"}}` → `("b","ETH-USDT",E=1794489612549,T=1794489612548,u=100119112482604,"2008.15","5372","2008.16","4294")`.
- `trade.detail`: `{"ch":"market.XAU-USDT.trade.detail","ts":1794490515065,"tick":{"id":100000868256775,"ts":1794490515062,"data":[{"amount":80,"quantity":0.08,"trade_turnover":313.0496,"ts":1794490515062,"id":1000008682567750000,"price":3913.12,"direction":"buy","is_rpi_trade":false},{"amount":118,"quantity":0.118,"trade_turnover":461.74816,"ts":1794490515062,"id":1000008682567750001,"price":3913.12,"direction":"buy","is_rpi_trade":false}]}}` → две записи `("t","XAU-USDT",E=1794490515065,T=1794490515062,a=f=l=1000008682567750000,"3913.12",q="80",m=False,nq="0.08")` и `(…1000008682567750001,"3913.12","118",False,"0.118")`.
- `mark_price`: `{"ch":"market.ETH-USDT.mark_price.1min","tick":{"id":1794490500,"open":"2013.46","close":"2012.9","high":"2013.46","low":"2012.86","amount":"0","vol":"0","count":"0","trade_turnover":"0"},"ts":1794490511065}` → `("m","ETH-USDT",E=1794490511065,p="2012.9",ap="",P="",i=<последний index close>,r=<последний funding_rate>,T=<settlement_time>)`; `index`: `{"ch":"market.ETH-USDT.index.1min","tick":{"id":1794490500,"open":"2013.3228555714222","close":"2013.2728558914237","high":"2013.6842845371382","low":"2013.252855971424","amount":"0","vol":"0","count":0},"ts":1794490510902}` → `i="2013.2728558914237"`.
- `funding_rate`: `{"op":"notify","topic":"public.QNTX-USDT.funding_rate","ts":1794489620137,"data":[{"symbol":"QNTX","contract_code":"QNTX-USDT","fee_asset":"USDT","funding_time":"1794489612000","funding_rate":"0.000000000000000000","estimated_rate":null,"settlement_time":"1794499200000","trade_partition":"USDT"}]}` → `r="0.000000000000000000"`, `T=1794499200000`.
- пинги: `{"ping":1794489617456}` → ответ `{"pong":1794489617456}`; на уведомлениях `{"op":"ping","ts":"1794489617456"}` → `{"op":"pong","ts":"1794489617456"}`.

## 4. Адаптер

Не писался (см. выше). Правила разбора — п. 3, требования к ядру — п. 6.

## 5. Проба: числа [измерено]

Условия: srv 5.231.25.224 (Нидерланды), часы по chrony (смещение 0,11 мс); время проб — 13:20–13:39 UTC; клиент — Python `websockets` + gzip + zstd (не Rust), на машине постоянная нагрузка (load average 5–9 на 8 ядрах: идёт Rust-замер Binance/Bybit — его не трогали). Лаг = приём_мс − время записи биржи (`E`: `tick.ts` у `bbo`, `ts` кадра у остальных). Смещение часов HTX не отделить: REST `/api/v1/timestamp` — **−46,9 мс** (p50 из 30; размах −625…+282; RTT 381 мс). Первые 5 с окна в лаг не входят. Все окна ≤ 5 соединений одновременно.

### Два вида соединений: «быстрые» и «медленные» (главное замечание для ядра)

Соединения на один и тот же адрес и набор подписок делятся на два вида; вид устанавливается при подключении и до конца соединения не меняется:

- **быстрое:** лаг `bbo` p50 155–240 мс, p99 530–1 070 мс, **360–730 кадров/с** (топ-10: 359–602; пул 49: 728; весь рынок 415: 1 393);
- **медленное:** лаг **растёт ≈ +0,8 с за секунду** (7–11 с за первые 20 с, 20–51 с к концу минуты), число кадров **88–142 в секунду** независимо от символов (≈ 13–21 КБ/с); `ping` от сервера приходит с опозданием (3 пинга за 62 с вместо 12); часть таких соединений **закрыта биржей без кадра закрытия на 28,7–99,9 с**. `ts` кадра у них нормальный (разность с `tick.ts` 2 мс) — отстаёт доставка.

| окно | соединений | быстрых / медленных |
|---|---|---|
| H1 (13:20:11; пул 49, 5 соединений) | A `bbo`×49: **медленное** (лаг 1,5 → 51 с, закрыто на 63,9 с); D (топ-10 `bbo`) — быстрое; B (`trade.detail`) и E (уведомления) в счёт `bbo` не входят | 1 / 1 |
| H2 (13:23:22; три имени хоста, топ-10) | `api.hbdm.com`: медленное (лаг p50 37,6 с, закрыто на 99,9 с); `api.hbdm.vn`: медленное (36,6 с, закрыто на 90,1 с); `api.btcgateway.pro`: быстрое (197 / 673 мс, 359 кадров/с); весь рынок 415 (B): быстрое | 2 / 2 |
| X1, X2 (13:29, 13:30; по 5 соединений, топ-10 `bbo`, 60 с) | быстрых 1 + 1 (477 и 602 кадров/с, лаг 197 и 219 мс), **медленных 7** (97–142 кадров/с, лаг p50 12,6–33 с; X1-D закрыто на 38,8 с, X2-B на 28,7 с) | 2 / 7 |
| X3, X4 (13:32, 13:33; по 5, с замером TCP connect, 50 с) | X3: 4 быстрых + 1 медленное (A: лаг 11,7 → 37 с); X4: 2 быстрых + 3 медленных (B, C, E: TCP connect **508–521 мс**, rtt 72 мс; E закрыто) | 6 / 4 |
| F1 (13:35; отбор попытками) | `bbo` A: 1-я попытка медленная (отказ на 15 с), 2-я быстрая; остальные 4 — с первой попытки быстрые | — |

**Итого по подключениям к `linear-swap-ws` с `bbo`: 15 медленных из 27 (56 %).** Связь с временем TCP connect и `rtt` из `ss -ti` **не однозначна**: у медленных TCP connect 4,8 мс (1) и 508–521 мс (3), rtt 0,14–72 мс; у быстрых 4,2–5,1 мс и 4–18 мс (`results/ss/`, `results/connections.txt`). Имя хоста значения не имеет: все три разрешаются в один адрес 43.169.95.96. Причина не установлена [догадка: ограничение/очередь на узле, через который попало соединение; по поведению близко к «медленному маршруту» Binance из плана G-022, п. 5]. Выбор — отбор по лагу: в F1 соединение закрывалось и переоткрывалось, если медиана лага за 5–15 с после первого кадра > 3 с (`g022-prep-3-tools/fast.sh`): отказ за 15 с, быстрое окно — со второй попытки.

### H1 — пул (49), рабочие окна на быстрых соединениях (F1, 13:35:10–13:38:40, 180 с, по соединению на вид)

| вид | эндпоинт | кадров/с | КБ/с | лаг p50 / p99 / max, мс | примечание |
|---|---|---|---|---|---|
| `bbo` ×49 | рынок | 728,3 | 108 | 232 / 750 / 1 127 | 36,0 % записей > 250 мс |
| `trade.detail` ×49 | рынок | 5,8 (7,5 зап/с) | 1,2 | 223 / 872 / 938 | 705 buy + 636 sell |
| `mark_price.1min` ×49 | ws_index | 50,0 | 8,1 | 286 / 664 / 980 | раз в 1,000 с на символ (p50 интервал 999 мс, p99 1 126) |
| `index.1min` ×49 | ws_index | 49,4 | 8,0 | **672** / 1 834 / 2 401 | 100 % > 250 мс: `ws_index` стабильно медленнее рынка (в H2 — 655 / 3 825) |
| `funding_rate` (`*`) | уведомления | 49,0 (51,4 зап/с) | 10,0 | 2 868 / 9 443 / 10 512 | весь рынок (434 символа) раз в ≈ 8 с; в H1 было 520 / 946 мс — лаг уведомлений нестабилен |

Весь рынок на одном быстром соединении (H2-B, 13:23:22, 415 символов `bbo`): 1 393 кадров/с, 206 КБ/с, лаг 187 / 634 / 103 415 мс (максимум — первые кадры с устаревшим `ts`), 13,1 % > 250 мс, 415 подписок за 12 с.

### Тишина и пинг (H3, ≈ 13:26; 100 с, 4 соединения)

Без ответа на серверный `ping`: закрыто **кодом 1003 («ping check expired») на 20,9 с**, и с подпиской на `bbo` (20 с, 1 284 кадра), и без подписки; с ответом `{"pong":ts}`: открыто весь срок (5 062 кадра за 99,5 с на `bbo` одного символа; на пустом соединении — 20 пингов). Сервер шлёт `{"ping":ts}` раз в 5 с: 36 пингов за 180 с.

### Входы

DNS (srv): `api.hbdm.com`, `api.hbdm.vn`, `api.btcgateway.pro` → **один адрес IPv4 43.169.95.96** (на всех трёх), IPv6 нет; заголовок `server: openresty`; TCP connect 4–5 мс (у части соединений 508–521 мс — п. выше). Независимой точки входа типа `stream.bybit.kz` у HTX с этого сервера **нет**. Разница «быстрое/медленное» между именами хоста не наблюдалась (на всех трёх встречались оба вида).

Одинаковость потока на разных соединениях (X3, три быстрых, топ-10 `bbo`, окно 45,5 с): 15 582 общих `(символ, version)`, 27 и 0 только на одном; разница прихода p50 −0,15 / −0,05 мс, p1 −106 / −136 мс, p99 +381 / +335 мс.

## 6. Что нужно от ядра (в терминах контракта, не Python)

1. **Двоичные gzip-кадры.** Все кадры приходят как opcode 2 с gzip-телом (в том числе `ping`); ядро разжимает кадр до разбора адаптером; в `r` и в сырые кадры — разжатый текст (или оба).
2. **Пинг на уровне сообщения.** Сервер шлёт `{"ping":ts}` раз в 5 с; нужно ответить `{"pong":ts}` (на уведомлениях — `{"op":"pong","ts":…}`), иначе закрытие кодом 1003 через ≈ 21 с. Протокольный `ping` ядра для HTX не нужен; метрика `g` по пингу сервера недоступна (RTT не измерить) — нужна альтернатива (напр. время доставки `ping`: `rx − ts`, но у `ping` значение — время сервера).
3. **Отбор соединений по лагу.** 56 % соединений медленные (лаг растёт, 88–142 кадра/с), вид не виден ни по connect-времени, ни по `rtt`: нужна проверка «лаг p50 за 5–15 с после первого кадра ≤ ~1–3 с», замена линии без разрыва потока; ожидание ≈ 2 попытки на соединение. Первые кадры после подписки несут **устаревшее время** (113 из 415 символов): их в проверку лага не брать (или брать только символы с изменениями за последние секунды).
4. **Подписка по одной тематике** (пакетов нет), ≤ 40 в секунду (проверено 33/с); для пула 49 × 5 видов — 245 подписок за 7,5 с.
5. **Три эндпоинта для `m`:** `linear-swap-ws` (`bbo`, `trade.detail`), `ws_index` (`mark_price.1min`, `index.1min`) и `linear-swap-notification` (`funding_rate`); у `ws_index` и уведомлений другой лаг (655–672 мс; 0,5–2,9 с) — свои линии и свои пороги проверки «медленности».
6. **Снятие дублей.** Ключи: `b` — `version`, `t` — `id` (по символу), `m` — `(символ, ts кадра mark_price)`; все ключи монотонны по символу (0 нарушений). Сброс нумерации при перезапуске в документации не описан.
7. **Формат: решение координатора.** Нужны: столбец `is_rpi_trade` (флаг типа сделки; 1,2–2,6 % сделок; у Bitget аналогичный `isRPI` — 3–4 %), ответ на «пустая сторона» в `bbo` (0,02 %: 45 из 250 281), нужна ли `trade_turnover`/`quantity` рядом с `amount` (контракты, в формате — `q` и `nq`), хранение числа как пришло (в JSON — число без кавычек: нужен разбор по тексту числа, не через `f64`). `ap`/`P` для `m` у HTX нет.
8. **Объём `m` — решение координатора.** `mark_price` 50 зап/с на 49 символов (≈ 4,3 млн/сутки), `bbo` 728 зап/с (≈ 63 млн/сутки), `trade.detail` 7,5 зап/с; изменение `mark` в 51 % кадров, индекса в 77 %.
9. **Метаданные символов:** `contract_size` в `pool-<день>.json` (`meta`); размеры в контрактах.
10. **Входы.** Один адрес (43.169.95.96), три имени; `--entrance` не разводит линии; запасных входов нет.

## Отклонения и не доказано

- Скрипты и результаты — вне перечня SCOPE (отчёт + «малые артефакты рядом»): общий `docs/findings/g022-prep-3-tools/` для трёх бирж; сырые кадры — на srv, в репозиторий не кладутся.
- Окна неравномерны: основные 180 с (F1, H1, H2), 60–50 с (X1–X4), 100 с (H3); первый заход H1 сломан медленными соединениями и перезамерен на «быстрых» (F1); `mark_price` в H1 подписан не на тот эндпоинт (`invalid topic`) — перезамер в F1 на `ws_index`. Одно окно на точку; лаг — одно время суток (13:20–13:40 UTC).
- Лаг клиента не отделён от лага сети: Python на загруженной машине (load average 5–9 из 8). У медленных соединений лаг растёт на десятки секунд при 88–142 кадрах/с и 0,5–2 с CPU — клиентской причиной это не объясняется (быстрые соседи в то же время идут чисто), но проверка на Rust-клиенте не делалась.
- Не изолированы: причина медленных соединений; поведение на плановом техобслуживании (код 1006 по документации); сделки между соединениями (дедуп `trade.detail` не сверяли); возраст записей, пришедших при подписке на `trade.detail` (72 записи за 3 с на 49 символах при потоке ≈ 7,5/с — часть, видимо, историческая); суточное поведение.
- Не пробовались: `depth.size_150.high_freq`, `req`, канал `liquidation_orders`, `contract_info`, `center-notification`, подписка на фьючерсы с датой.
- Приближение оборота: `trade_turnover` из `batch_merged` — скользящие 24 ч по документации; точная сумма по свечам не считалась.
