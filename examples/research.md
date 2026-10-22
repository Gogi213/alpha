# Ресурсы по микроструктуре для HFT/MM: что реально стоит читать (2025–2026)

Лучшие источники для твоего стека (крипто-перпы, Bybit, Rust, MM) — туториалы hftbacktest, серия работ Albers–Cucuringu–Howison–Shestopaloff (Оксфорд) с живыми экспериментами на Bybit/Binance/Hyperliquid, книга Bouchaud et al. «Trades, Quotes and Prices» и гайд/блог Databento. Форумы в 2025–2026 почти мертвы или шумные, а русскоязычный сегмент глубокий только в архивах 2015–2018 годов, и там MOEX, а не крипта.

## TL;DR
- **Ядро**: hftbacktest docs (queue models, латентность, L2 vs L3), статьи Albers et al. (fill probability против post-fill returns, латентность тейкера на Bybit/Binance, L4-данные Hyperliquid с отклонёнными ордерами), TQP (Bouchaud), Cartea–Jaimungal–Penalva для контроля инвентаря, Databento Microstructure Guide по механике матчинга и фидов.
- **Сообщества**: Nuclear Phynance в основном архив, Elite Trader и почти весь Smart-Lab — шум. Реальная практика на русском — архив uralpro (quantalgos.ru/Smart-Lab, 2015–17) и пара статей на Хабре. Живого русскоязычного обсуждения крипто-перпов на уровне queue/adverse selection не найдено.
- **Исключить**: QuantInsti EPAT, hftadvisory.substack (продаёт консалтинг), quantstrategy.io, bluechipalgos, marketcalls, типовые Medium-статьи, AI-курируемые подборки, «топ-каналы» Telegram, CScalp/OsEngine как «обучение HFT».

## 1. Практики: блоги и авторская документация

**hftbacktest (nkaz001)** — https://hftbacktest.readthedocs.io, https://github.com/nkaz001/hftbacktest
- Что даёт: вероятностные модели позиции в очереди на L2 (RiskAverse, Probability) и точную FIFO-позицию на L3, раздельные feed- и order-латентности, туториалы «Queue-Based Market Making in Large Tick Size Assets» (book-pressure fair price, inventory skew, спред в единицах очереди вместо цены), «Probability Queue Position Models» (калибровка A, k по GLFT/Avellaneda–Stoikov, сверка модели очереди с реальным трейдингом), Level-3 Backtesting.\[1\] Есть коннекторы Binance Futures и Bybit.\[2\]
- Почему не поверхностно: автор прямо говорит о том, что для инструментов с крупным тиком моделирование очереди — главное для симуляции филлов, и что сигнал стоит переводить в чисто очередной.\[3\]
- Слабости: на L2 позиция в очереди — оценка, нет market impact и частичных исполнений. Туториалы опираются на ребейт 0,005% (топ-тир Binance), так что без ребейта результаты не переносятся. Данные собраны при ~4 мс до биржи.\[4\] Проект жив, есть форки.\[5\]

**Databento: блог и Microstructure Guide** — https://databento.com/microstructure, https://databento.com/blog
- Что даёт: устройство матчинг-движков (гейтвеи, балансировщики, latency/fiber equalization), семантику L3/MBO (при уменьшении размера позиция в очереди сохраняется, при увеличении теряется; IEX DEEP+ передаёт это явным флагом), self-match prevention как микроструктурный хак и сигнал.\[6\]\[7\]\[8\]\[9\] Отдельный пост показывает, что наивная модель отмен p(x)=x переоценивает fill rate, потому что отмены в основном идут из хвоста очереди (сверено с MBO как ground truth).\[10\]
- Слабости: только equities/futures США, крипты нет. Это вендор, но материалы аналитические, без продажи в тексте. Активен.

**Dean Markwick** — https://dm13450.github.io
- Что даёт: OFI на секундных бакетах BTC (по Kolm–Turiel–Westray «Deep OFI»), фит моделей price impact (линейный λ, R² in-sample ~11%), Hawkes-процессы, воспроизводимый код на Julia/QuestDB на крипто-данных Coinbase.\[11\]\[12\]\[13\]\[14\]\[15\]
- Слабости: исследователь исполнения FX, а не HFT-MM; горизонты 1 с и больше, латентность не учитывается. Активен (посты в 2026).\[16\]

**Mechanical Markets** — https://mechanicalmarkets.wordpress.com
- Бывший HFT-практик: экономика выбора маркет-мейкеров, критика speed bump IEX, логика «stale quotes».\[17\] Сильная интуиция по adverse selection и дизайну бирж. Архив, давно не обновляется, про equities.

**Headlands Tech blog** — https://blog.headlandstech.com
- «Quantitative Trading Summary» (2017): book pressure, trade impulse, почему taking-стратегия на простых сигналах не получает филлов (гонка латентности, FPGA).\[18\]\[19\] Это минимальная каноническая модель. Остальное — карьерные посты; блог почти не обновляется.

**Charles-Albert Lehalle** — http://www.cmap.polytechnique.fr/~charles-albert.lehalle/
- Проектные страницы, например про локализацию queue-reactive модели (2024) для детекции экзогенных движений цены: A/C/T-события, интенсивности как функция формы стакана.\[20\] Материал уровня исследователя из CFM; ценен тем, что связывает QR-модель с практической диагностикой.

**tr8dr (Jonathan Shore)** — упоминается в переводах uralpro как источник по восстановлению стакана из order log и предсказанию направления по потоку.\[21\] Блог старый; ссылку и статус я не проверял.

## 2. Форумы и сообщества

| Ресурс | Сигнал/шум | Статус |
|---|---|---|
| Nuclear Phynance | исторически высокий (треды про структуры данных стакана, про manipulation vs normal trading)\[22\] | RSS-снимок обрывается в январе 2020, фактически архив\[23\] |
| Wilmott | в основном деривативы и прайсинг, микроструктуры мало | вялый |
| Elite Trader | низкий; ветка «nuclear phynance» там — это розничный дневник\[24\] | активен, но шумный |
| Hacker News | иногда точные комментарии практиков, тред «Nuclear Phynance is your best bet…» даёт карту\[25\] | эпизодически |
| Smart-Lab | в основном скальпинг по стакану и «ММ двигает цену»; исключения ниже | активен, шумный |
| r/highfreqtrading, Discord | не проверял | — |

**Русскоязычное, с реальной глубиной:**
- **uralpro (Виталий), quantalgos.ru и зеркала на Smart-Lab** — HFT-роботы с 2008 года, участник ЛЧИ.\[26\] «Алгоритмы маркетмейкера», части 1–5 (https://smart-lab.ru/blog/244854.php, https://smart-lab.ru/blog/245811.php): Avellaneda–Stoikov, оптимальное управление с инвентарным риском и adverse selection, дисбаланс объёма как сигнал; в части 5 есть код на C# с исправлениями из комментариев.\[27\]\[28\]\[29\] «VPIN для HFT» (https://smart-lab.ru/blog/264850.php), «Немного об HFT стратегиях» (https://smart-lab.ru/blog/267416.php: вероятность исполнения лимитки как ключевая метрика, эмуляция очереди), перевод tr8dr про поток ордеров (https://smart-lab.ru/blog/408585.php). \[21\]\[30\]\[31\] Индексы: https://www.quantalgos.ru/?cat=5, https://www.quantalgos.ru/?cat=6. Минусы: MOEX/FORTS, 2015–17, автор неактивен с 2022 года.\[32\]
- **Smart-Lab, «Вопрос про очередь заявок»** (https://smart-lab.ru/blog/297981.php) — FIFO-приоритет и то, как ММ за тобой снимается при твоей отмене.\[33\] Средний сигнал.
- **Хабр**: «Однажды в HFT-компании…» (https://habr.com/ru/articles/414037/, 2018) — пассивный MM на фьючерсах, удержание места в очереди, латентный гандикап агрессора;\[34\] обсуждение layering в комментариях.\[35\] «Построение стакана (FullOrderBook) по историческим данным» (https://habr.com/ru/post/276635) — реконструкция из full order log MOEX, айсберги.\[36\] Крипто-статьи 2025–26 (например https://habr.com/ru/articles/913696/, https://habr.com/ru/articles/993864/) — от низкой до средней глубины, частично спекулятивные или написанные с ИИ.
- **Telegram @day0market** (https://t.me/s/day0market) — Alex Hurko, HFT-фонд в Гонконге; теги #microstructure, #marketmaking, Rust.\[37\] Около 570 подписчиков;\[37\] активность в 2025–26 не подтверждена. Единственный найденный канал практика.
- Не найдено: русского перевода TQP, русскоязычного сообщества вокруг hftbacktest, русских практических текстов про OFI (Cont–Kukanov–Stoikov) и про особенности матчинга Bybit/Binance.

## 3. Книги

| Книга | Практичность | Комментарий |
|---|---|---|
| Bouchaud, Bonart, Donier, Gould — *Trades, Quotes and Prices* (CUP, 2018) | высокая | эмпирика LOB, long memory знака ордеров, propagator-модели impact, square-root law, queue dynamics, влияние тика. Лучшая единая книга для MM-практика; по описанию CUP, все модели откалиброваны на свежих данных Nasdaq, но механизмы переносятся |
| Cartea, Jaimungal, Penalva — *Algorithmic and High-Frequency Trading* (CUP, 2015)\[38\] | средне-высокая | стохастический контроль для MM с инвентарём, adverse selection, OFI-подобные сигналы, исполнение. Хорош как фреймворк для скью и спреда; стационарные допущения |
| Guéant — *The Financial Mathematics of Market Liquidity* (2016) | средняя | GLFT-решение для MM с ограничениями инвентаря, closed-form аппроксимации; база для калибровки A, k в hftbacktest |
| Lehalle, Laruelle — *Market Microstructure in Practice* (2-е изд., 2018) | средняя | фрагментация, тик, SOR, HFT в Европе; взгляд брокера и регулятора, почти ничего о внутренней кухне MM |
| Harris — *Trading and Exchanges* (2003) | средняя | таксономия участников, экономика ликвидности, order types, правила приоритета.\[39\] Устарел технологически, концептуально остаётся базой |
| O'Hara — *Market Microstructure Theory* (1995); Hasbrouck — *Empirical Market Microstructure* (2007) | низкая / средняя | Kyle, Glosten–Milgrom, инвентарные модели; у Hasbrouck — эконометрика (Roll, VAR, information share). Теория, но это язык всей литературы |

## 4. Статьи и обзоры

**Фундамент:**
- Avellaneda, Stoikov (2008), «High-frequency trading in a limit order book» — reservation price и оптимальный спред.\[40\]\[41\]\[42\] Практически нужен как язык; в сыром виде его перебивают GLFT и эмпирика.
- Stoikov (2018), «The micro-price», Quantitative Finance 18 — fair price как предел ожидаемых mid с учётом дисбаланса и спреда, мартингал; код на https://github.com/sstoikov/microprice. \[43\]\[44\]\[45\]\[46\]
- Cont, Kukanov, Stoikov (2014), «The price impact of order book events» — OFI и линейная связь изменения mid с дисбалансом потока на L1, обратная зависимость от глубины.
- Huang, Lehalle, Rosenbaum (2015), queue-reactive model, JASA 110\[47\] (https://arxiv.org/abs/1312.0563) — интенсивности add/cancel/trade зависят от состояния очередей.\[48\]\[49\] Развития: учёт размеров ордеров (https://arxiv.org/pdf/2405.18594), deep learning + QR (https://arxiv.org/pdf/2501.08822), обзор симуляций LOB (https://arxiv.org/pdf/2402.17359). \[50\]\[51\]\[52\]
- Moallemi, Yuan, «A Model for Queue Position Valuation in a Limit Order Book» (Columbia Business School, 2016, перераб. 2017; SSRN 2996221) — ценность позиции в очереди: статическая компонента (спред минус adverse selection, растущий с позицией в очереди) плюс опционная ценность удержания позиции; для ряда акций с крупным тиком ценность очереди, по оценке авторов, того же порядка, что и bid-ask спред.
- Easley, López de Prado, O'Hara (2012), VPIN — токсичность потока; применимость спорна, и пороги из практики не стоит принимать на веру.\[53\]
- O'Hara (2015), «High frequency market microstructure», JFE — повестка: order priority, модели ценообразования бирж.\[54\]

**Крипто-эмпирика, самое ценное для тебя (Albers, Cucuringu, Howison, Shestopaloff, Оксфорд):**
- «The Good, the Bad, and Latency: Exploratory Trading on Bybit and Binance», Quantitative Finance, т. 25, № 6, с. 919–947 (онлайн 24.06.2025; https://ssrn.com/abstract=4677989) — миллионы живых market- и marketable-limit ордеров. Расхождения с ожидаемым по последнему снапшоту LOB коррелируют с волатильностью, латентностью и ликвидностью; авторы фиксируют «a consistent disadvantage to the trader, pointing to an adverse selection effect for taker orders»: прибыльные ордера исполняются хуже.
- «The Market Maker's Dilemma» (arXiv 2502.18625, v2 от 23.11.2025) — живой эксперимент на BTCUSDT-перпе Binance. Отрицательная корреляция вероятности филла и post-fill return; по словам авторов, это делает распространённые стратегии «highly unprofitable», и они моделируют «Reversals» — ситуации, где контрарный мейкинг на касании против дисбаланса стакана работает; роль позиции в очереди и размера очереди.
- «An Open Book: Level 4 Order Book Data from the Hyperliquid Exchange» (SSRN 6465720, 2026) и «The "Neutrinos" of the Order Book» (SSRN 7162966) — полный поток сообщений BTC-перпа Hyperliquid, включая отклонённые ордера: post-only-ордера, отклонённые за пересечение спреда, составляют 67% трафика; восполнение спреда в основном упреждающее, ордера уже в полёте.\[55\]\[56\] Это прямой материал о гонке за приоритет в очереди.
- He, Manela, Ross, von Wachter, «Fundamentals of Perpetual Futures» (https://arxiv.org/abs/2212.06888) — no-arbitrage цена перпа через funding и границы при издержках; отклонения в крипте больше, чем в FX, и сокращаются со временем.\[57\]

**Ликвидационные каскады (осторожно):** препринты 2026 года по каскаду 10 октября 2025 года — $19,37 млрд ликвидаций за 24 часа у более чем 1,6 млн трейдеров по данным CoinGlass в пересказе CNBC (крупнейшее событие за всю историю наблюдений CoinGlass; в годовом отчёте CoinGlass за 2025 год реальный масштаб оценён в $30–40 млрд с учётом неполного раскрытия данных площадками):
- https://arxiv.org/abs/2607.27070 — сигналы раннего предупреждения неоднородны по событиям; отмечено, что Binance прекратил отдавать liquidationSnapshot, поэтому интрадей-ликвидации без платных данных не видны.\[58\]\[59\]
- https://arxiv.org/pdf/2608.03616 — ветвящийся процесс внутри каскада, данные fill log узла Hyperliquid.\[60\]
- https://arxiv.org/pdf/2603.09164 — Slippage-at-Risk по стакану Hyperliquid, связь с ADL и страховым фондом (автор ранее работал в Gauntlet).\[61\]
Всё это свежие препринты одиночных авторов, не прошедшие рецензирование; брать как идеи и описание данных, не как установленные факты.

## 5. Доклады и подкасты

- **Signals and Threads (Jane Street)** — https://signalsandthreads.com. В основном инфраструктура: эпизод про market data и multicast (state-machine replication на биржах), синхронизация часов, ML при низкой латентности.\[62\]\[63\]\[64\] Микроструктуры как торговой механики мало. Активен.
- Доклады CppCon/Meeting C++ от Optiver и других в основном о C++-приёмах, не о микроструктуре; в этом раунде не проверялись.
- Stoikov: видеоинтро к micro-price на YouTube (ссылка в SSRN-карточке) — полезно как 20-минутный разбор.\[43\]

## 6. Open-source с объясняющей документацией

- **hftbacktest** — см. раздел 1; лучший пример проекта, где документация объясняет микроструктуру, а не только API.
- **sstoikov/microprice** — эталонная реализация micro-price с данными.
- **HFTENGINE (mirkovicdev)** — https://github.com/mirkovicdev/HFTENGINE — Rust-консоль для покадрового воспроизведения бэктестов hftbacktest (оценка очереди каждого ордера, латентности, сырой фид). Сам README честно пишет об ограничениях: очередь вероятностная, нет impact и partial fills, страница Binance MM-программы помечена как устаревшая.\[4\] Полезно для отладки модели очереди.
- **DaruFinance/crypto-adverse-selection** — https://github.com/DaruFinance/crypto-adverse-selection — декомпозиция markout для quoter'а, стоящего последним в очереди, на Bybit, Binance USD-M и Hyperliquid: 10-секундный markout до комиссий отрицателен везде (Bybit net −0,839 bp), break-even ребейт на Bybit от 0,328 bp.\[65\] Независимая и не рецензированная работа, методология ограничена (quoter теряет приоритет на каждом снапшоте), но цифры хороши для sanity-check.\[65\]
- **Tardis.dev docs** (https://docs.tardis.dev) — вендор, но FAQ содержательный: каналы trades и book у бирж обрабатываются независимо, поэтому timestamp трейда не гарантирует pre/post-trade состояние стакана; Binance forceOrder с апреля 2021 года отдаёт не больше одного снапшота в секунду; ежедневные ресабскрайбы дают разрывы 300–3000 мс; для Bybit orderbook.full с генерируемым снапшотом до 10 000 уровней.\[66\]\[67\]\[68\] Это прямо про поведение WebSocket-фидов.

## 7. Крипто-ресерч вендоров и бирж

- **Kaiko Research** (https://research.kaiko.com) — глубина 0,1%/1%, концентрация ликвидности (Binance: 30,7% глубины против 64,3% объёма в 2023 году), отчёт о восстановлении ликвидности Bybit за 30 дней после взлома.\[69\]\[70\]\[71\] Полезен для межбиржевого контекста, но уровень дневных агрегатов, и материал частично продаёт их данные. Для HFT — фон, не механика.
- **Elm Wealth, «Perpetual Futures: Mechanics, History and Purpose»** — история и механика funding; средний funding на Binance ~14% годовых против ~3% по T-bills за шесть лет.\[72\] Хорошее объяснение, но это блог управляющего, не HFT.
- Документация самих бирж (Bybit V5 API, MM-программы Binance) — первоисточник по тирам ребейтов и семантике фидов; сама Binance помечает страницу MM-программы как устаревшую.\[4\]

## Исключить (маркетинг, курсы, шум)
- **QuantInsti EPAT** — модуль микроструктуры продаётся только в составе платной программы.\[73\]
- **hftadvisory.substack / electronictradinghub** — местами технически грамотно (ITCH, cancel-first book), но это воронка консалтинга («Book a strategy call»); «практические» пороги VPIN там не обоснованы.\[53\]\[74\]
- **mlquants.substack** — подборка статей, курируемая ИИ, без анализа.\[75\]
- **quantstrategy.io, bluechipalgos, marketcalls, типовые Medium** («Market Making Mechanics», «HFT Market-Making Strategies») — SEO-пересказы.
- **Rebellion Research** — пересказ micro-price с рекламой.\[44\]
- **Русский сегмент**: free-rates.ru, cryptotrade.wiki, equity.today, fortraders — SEO; ITI Capital на Хабре — блог брокера с курсами; CScalp, OsEngine (@bad_quant) — вендорское обучение уровня новичка; «топ-каналы» Telegram и @HFTResearchChat — сигналы и платные скрипты.

## Рекомендации
1. Порядок изучения: туториалы hftbacktest → Albers et al. (все четыре работы) → TQP, главы про impact и тик → Guéant/GLFT для калибровки.
2. Для Bybit: откалибровать модель очереди по собственным филлам (подход hftbacktest) и сравнить с правилом «отмены из хвоста» от Databento. На Bybit нет L3, поэтому bias модели решает.
3. Для каскадов и funding: He–Manela как база, а препринты 2026 года — источник идей по данным (fill log Hyperliquid, отсутствие интрадей-ликвидаций Binance).
4. Не тратить время на поиск живого форума: реальная дискуссия сейчас идёт в issues и discussions hftbacktest и в комментариях к препринтам, а не на NP, Wilmott или Smart-Lab.

## Оговорки
- Не проверял в этом раунде: r/highfreqtrading, Discord-серверы, доклады CppCon, активность @day0market в 2025–26, актуальную ссылку на блог tr8dr.
- Цифры из не рецензированных репозиториев и препринтов 2026 года стоит перепроверить на своих данных.

## Sources

1. [nkaz001/hftbacktest | DeepWiki](https://deepwiki.com/nkaz001/hftbacktest)
2. [hftbacktest/hftbacktest at master · nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest/tree/master/hftbacktest)
3. [Queue-Based Market Making in Large Tick Size Assets — hftbacktest](https://hftbacktest.readthedocs.io/en/latest/tutorials/Queue-Based%20Market%20Making%20in%20Large%20Tick%20Size%20Assets.html)
4. [GitHub - mirkovicdev/HFTENGINE: Replay console for hftbacktest market-making backtests: order book, queue position of every resting order, feed and order latency, fills and the raw feed from the collector, on real recorded Binance Futures data. Rust runner on the hftbacktest crate, text-mode dashboard. A backtester, not a trading bot. · GitHub](https://github.com/mirkovicdev/HFTENGINE)
5. [GitHub - 0xNyk/hftbacktest: Free, open source, a high frequency trading and market making backtesting and trading bot, which accounts for limit orders, queue positions, and latencies, utilizing full tick data for trades and order books(Level-2 and Level-3), with real-world crypto trading examples for Binance and Bybit · GitHub](https://github.com/0xNyk/hftbacktest)
6. [Self-match prevention: Microstructure hack or trading signal | Databento Blog](https://databento.com/blog/self-match-prevention)
7. [What is an exchange matching engine? | Databento Microstructure Guide](https://databento.com/microstructure/matching-engine)
8. [What is level 3 (L3) market data? | Databento Microstructure Guide](https://databento.com/microstructure/level-3-market-data)
9. [What is latency equalization? | Databento Microstructure Guide](https://databento.com/microstructure/latency-equalization)
10. [Getting queue position from L2 and order book data | Databento Blog](https://databento.com/blog/getting-queue-position-from-l2-and-order-book-data)
11. [Dean Markwick on X: "Found this interesting? Read the full blog post here: https://t.co/W6RQo53mOU" / X](https://twitter.com/DeanMarkwick/status/1503476087510032387)
12. [Fitting Price Impact Models | Dean Markwick](https://dm13450.github.io/2025/03/14/Fitting-Price-Impact-Models.html)
13. [Getting Started with High Frequency Finance using Crypto Data and Julia | Dean Markwick](https://dm13450.github.io/2021/06/25/HighFreqCrypto.html)
14. [About Me | Dean Markwick](https://dm13450.github.io/about/)
15. [Order Flow Imbalance - A High Frequency Trading Signal | Dean Markwick](https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html)
16. [Dean Markwick | Personal website for Dean Markwick. If you like stats, sports and rambling, you’ve come to the right place. All rights reserved.](https://dm13450.github.io/)
17. [Mechanical Markets](https://mechanicalmarkets.wordpress.com/)
18. [2/10/2021 Quantitative Trading Summary – Headlands Technologies LLC Blog](https://traders.studentorg.berkeley.edu/papers/Quantitative%20Trading%20Summary%20%E2%80%93%20Headlands%20Technologies%20LLC%20Blog.pdf)
19. [Headlands Quant Trading Summary Max Dama | PDF](https://www.scribd.com/document/806692263/Headlands-Quant-Trading-Summary-Max-Dama)
20. [Detection of Exogenous Price Moves: Localising the Queue Reactive Model – Charles-Albert Lehalle](http://www.cmap.polytechnique.fr/~charles-albert.lehalle/projects/2024QR/)
21. [Определение направления цены с помощью потока ордеров](https://smart-lab.ru/blog/408585.php)
22. [Updating order book data structure - Nuclear Phynance](https://phynance1.rssing.com/chan-12652191/all_p16.html)
23. [Nuclear Phynance](https://phynance1.rssing.com/chan-12652191/all_p14.html)
24. [nuclear phynance | Elite Trader](https://www.elitetrader.com/et/threads/nuclear-phynance.378106/)
25. [Nuclear Phynance is your best bet for a HN like community in quant finance. | Hacker News](https://news.ycombinator.com/item?id=22783653)
26. [Об авторе | QuantAlgos](https://www.quantalgos.ru/?page_id=2)
27. [Алгоритмы маркетмейкера. Часть 2](https://smart-lab.ru/blog/245811.php)
28. [Алгоритмы маркетмейкера. Часть 1](https://smart-lab.ru/blog/244854.php)
29. [Market Maker | QuantAlgos](https://www.quantalgos.ru/?cat=6)
30. [Немного об HFT стратегиях](https://smart-lab.ru/blog/267416.php)
31. [Измерение токсичности потока ордеров. VPIN для HFT. Часть 1](https://smart-lab.ru/blog/264850.php)
32. [uralpro: профиль на смартлабе](https://smart-lab.ru/profile/uralpro/)
33. [Вопрос про очередь заявок ...](https://smart-lab.ru/blog/297981.php)
34. [Однажды в HFT-компании… / Хабр](https://habr.com/ru/articles/414037/)
35. [Однажды в HFT-компании… / Комментарии / Хабр](https://habr.com/ru/articles/414037/comments/)
36. [Построение стакана котировок (FullOrderBook) по историческим данным / Хабр](https://habr.com/ru/post/276635)
37. [Day0markets: канал о алготрейдинге – Telegram](https://t.me/s/day0market?before=156)
38. [High Frequency Trading I: Introduction to Market Microstructure | QuantStart](https://www.quantstart.com/articles/high-frequency-trading-i-introduction-to-market-microstructure/)
39. [Книги и образовательные ресурсы по алгоритмической торговле / Хабр](https://habr.com/ru/companies/iticapital/articles/257971/)
40. [A reinforcement learning approach to improve the performance of the Avellaneda-Stoikov market-making algorithm](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9767337/)
41. [GitHub - Adnan-Husayn/avellaneda-stoikov-market-maker · GitHub](https://github.com/Adnan-Husayn/avellaneda-stoikov-market-maker)
42. [Avellaneda-Stoikov and Cartea-Jaimungal as One Framework: A Forced Uniqueness Theorem for Inventory Market Making](https://arxiv.org/pdf/2606.01477)
43. [The Micro-Price: A High Frequency Estimator of Future Prices by Sasha Stoikov :: SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694)
44. [What is Microprice? A High Frequency Estimator of Future Prices](https://www.rebellionresearch.com/what-is-microprice-a-high-frequency-estimator-of-future-prices)
45. [The micro-price: a high-frequency estimator of future prices: Quantitative Finance: Vol 18, No 12](https://www.tandfonline.com/doi/abs/10.1080/14697688.2018.1489139)
46. [Liquidity Dynamics in RFQ Markets and Impact on Pricing](https://arxiv.org/pdf/2309.04216)
47. [Stock market microstructure inference via multi-agent reinforcement learning](https://arxiv.org/pdf/1909.07748)
48. [\[PDF\] Simulating and Analyzing Order Book Data: The Queue-Reactive Model | Semantic Scholar](https://www.semanticscholar.org/paper/Simulating-and-Analyzing-Order-Book-Data:-The-Model-Huang-Lehalle/46f98d5461c0dbb47e21b04b6a7b4dd6619d2e92)
49. [\[1312.0563\] Simulating and analyzing order book data: The queue-reactive model](https://arxiv.org/abs/1312.0563)
50. [Deep Learning Meets Queue-Reactive: A Framework for Realistic Limit Order Book Simulation](https://arxiv.org/pdf/2501.08822)
51. [Limit Order Book Simulations: A Review](https://arxiv.org/pdf/2402.17359)
52. [A Novel Approach to Queue-Reactive Models: The Importance of Order Sizes](https://arxiv.org/pdf/2405.18594)
53. [Six Market Microstructure Signals That Fire Before the Price Print: A Practitioner's Execution Quality Architecture](https://hftadvisory.substack.com/p/six-market-microstructure-signals)
54. [High frequency market microstructure - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0304405X15000045)
55. [An Open Book: Level 4 Order Book Data from the Hyperliquid Exchange by Jakob Albers, Mihai Cucuringu, Sam Howison, Alexander Y. Shestopaloff :: SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6465720)
56. [The "Neutrinos" of the Order Book: Pervasive, Weakly Interacting Order Flow and its Consequences by Jakob Albers, Mihai Cucuringu, Sam Howison, Alexander Y. Shestopaloff :: SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7162966)
57. [\[2212.06888\] Fundamentals of Perpetual Futures](https://arxiv.org/abs/2212.06888)
58. [Where does the criticality live? Early-warning signals are event-heterogeneous across seven crypto-perpetual liquidation cascades](https://arxiv.org/pdf/2607.27070)
59. [\[2607.27070\] Where does the criticality live? Early-warning signals are event-heterogeneous across seven crypto-perpetual liquidation cascades](https://arxiv.org/abs/2607.27070)
60. [Measuring the engine of a liquidation cascade: subcritical branching inside a first-order transition](https://arxiv.org/pdf/2608.03616)
61. [Slippage-at-Risk (SaR): A Forward-Looking Liquidity Risk Framework for Perpetual Futures Exchanges](https://arxiv.org/pdf/2603.09164)
62. [Signals and Threads Podcast](https://signalsandthreads.com/)
63. [Signals and Threads – Podcast](https://podtail.com/en/podcast/signals-and-threads/)
64. [Signals and Threads - TopPodcast.com](https://toppodcast.com/podcast_feeds/signals-and-threads/)
65. [GitHub - DaruFinance/crypto-adverse-selection · GitHub](https://github.com/DaruFinance/crypto-adverse-selection)
66. [Bybit Derivatives | Tardis.dev Documentation](https://docs.tardis.dev/historical-data-details/bybit)
67. [Historical Data Details | Tardis.dev Documentation](https://docs.tardis.dev/historical-data-details)
68. [Data | Tardis.dev Documentation](https://docs.tardis.dev/faq/data)
69. [Understanding Centralized Exchange Liquidity Data - Kaiko - Research](https://research.kaiko.com/insights/centralized-exchange-liquidity)
70. [The Crypto Liquidity Concentration Report. - Kaiko - Research](https://research.kaiko.com/insights/the-crypto-liquidity-concentration-report)
71. [Reports - Kaiko - Research](https://research.kaiko.com/reports)
72. [Perpetual Futures: Mechanics, History and Purpose | Elm Wealth](https://elmwealth.com/perpetual-futures/)
73. [Market Microstructure for High-Frequency Trading Course](https://www.quantinsti.com/epat/market-microstructure)
74. [Cancel-First Order Book Design: From ITCH 5.0 to L1D Hot Caches - Electronic Trading Hub](https://electronictradinghub.com/cancel-first-order-book-design-from-itch-5-0-to-l1d-hot-caches/)
75. [About - Quant Papers (Market MicroStructure, Algo Trading and HFT)](https://mlquants.substack.com/about)
