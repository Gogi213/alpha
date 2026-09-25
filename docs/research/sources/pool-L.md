# Пул гипотез L — микроструктура и литература (сборщик, 2026-09-25)

## Данные: есть / нет

- **Стакан 50 уровней (`orderbook.50`, `.50`)** — есть, весь пул 76 монет, вся история записи (`src/bybit/ws.rs`); это
  основной источник для `lob levels/markout/touches/touch_axes`.
- **Глубокий стакан 200 уровней (`orderbook.200`, `.200`)** — есть, но **только окно 15–24.09** (`deep/` на Steam
  Deck, 23 ГБ, В-106 — не удалять).
- **Лента сделок (`publicTrade`)** — **есть в бинлоге**, но не экспортирована в атомы анализа. Разбирается в
  `src/bybit/ws.rs` (`struct Trade`, `trade_from_raw`) и **пишется в бинлог** через `Recorder::stage_trade`
  (`src/commands/record.rs:422`, `Event::Trade` на строке 778) — не отбрасывается. У сделки есть: цена, объём, сторона
  агрессора (`aggressor_is_buy` — из поля `S` Buy/Sell), время матчинга (`T`), и два флага, важных для интерпретации
  плотности: **`block`** (блочная сделка — не потребляет видимую ликвидность, но засчитывается в объём и «портит»
  метку снятия стены) и **`rpi`** (сделка об RPI-заявку одобренного маркет-мейкера — тоже не видна в `.50`/`.200`, тот
  же эффект порчи метки, см. `docs/findings/hft-underground-2026-09-16.md` §12). Итог: **сырьё для потока сделок
  перед входом есть на диске**, но `sell_15m`/`imbalance` в атомах (`tools/compute/loss-atoms.py`) пусты — нужен
  **новый экспорт из бинлога (Rust)**, который читает `Event::Trade` за окно перед касанием и агрегирует сторону/объём/
  `block`/`rpi` отдельно от книги. Это ровно то, что в `CLAUDE.md` помечено «не измерено».
- **Минутные свечи** — не пишет коллектор непрерывно; тянутся отдельно REST-скриптом `tools/compute/ref-klines.py`
  (по требованию, не всегда).
- **Тикеры (`tickers.*`)** — нет: подписки на этот топик в `src/bybit/ws.rs`/`sub_*` нет.
- **Открытый интерес (`openInterest`)** — нет в записи; как признак уже отклонён владельцем 25.09 («на
  микроструктуре плоховат», `docs/plan/HYPOTHESES.md` §«Отклонено»).
- **Фандинг (`funding`)** — нет в записи, нет REST-скрипта аналога `ref-klines.py`.
- **Ликвидации** — нет в записи; Г-16 (подписка на поток ликвидаций Bybit) отмечена в банке как «нежелательная»
  (владелец, В-100) — **не предлагать повторно как «записывать поток ликвидаций»**; ниже L-08 обходит это прокси по
  уже записанной ленте сделок, не через отдельный фид.
- **Kline-топик (`kline.*`)** — строка топика парсится в тестах (`src/bybit/ws/tests.rs:147,204`), но живой подписки
  на него нет — это просто проверка общего разбора имени топика, не поток данных.
- **Внешнее (Binance, календарь новостей, история фандинга)** — не подключено; для отдельных гипотез ниже отмечено
  «внешнее» с ценой «средне» (разовый REST-пул по образцу `ref-klines.py`, не хот-пас).

## Литература

| id | формулировка | класс | механизм | источник | данные | цена |
|---|---|---|---|---|---|---|
| L-01 | Дисбаланс объёма (OBI) на 3–5 верхних уровнях книги в момент касания предсказывает знак хода на 10–60 с | против нас на стороне разгрузки стоит толпа с той же информацией: перекос объёма — не случайность, а обещание потока в эту сторону | [Order Book Imbalance — QuestDB](https://questdb.com/glossary/order-book-imbalance/); [emergentmind OBI](https://www.emergentmind.com/topics/order-book-imbalance-obi) | стакан .50 | средне |
| L-02 | Обобщённый Order Flow Imbalance (Cont–Kukanov–Stoikov: добавления/отмены/сделки на лучших ценах за 10–30 с до касания) точнее статичного OBI как признак устоит/не устоит | цена двигает не запас на уровне, а поток событий: кто активнее добавляет/снимает прямо сейчас | [Cont, Kukanov, Stoikov — Price Impact of Order Book Events](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822) | стакан .50 | дорого |
| L-03 | Устойчивость книги после локального проеда (resilience): в первые 10–20 с после касания книга либо в режиме «доливают» (лимитки возвращаются), либо в режиме «снимают» (отмены без долива) — регион различим и предсказывает исход касания | это ровно механизм отскока/пробоя: возврат лимитных заявок = отскок, чистые отмены = пробой без сопротивления | [Resiliency of the Limit Order Book](https://opus.lib.uts.edu.au/bitstream/10453/98964/1/Lo_Hall_Resiliency_of_the_limit_order_book_Accepted_Manuscript.pdf) | стакан .50 | дорого |
| L-04 | Стена-айсберг: уровень, где размер после частичных сделок **восстанавливается до того же отображаемого объёма** несколько раз подряд, держит сильнее статичной стены того же видимого размера | повторный долив — подпись реального крупного лимита позади видимого пика, а не случайного скопления мелких заявок | [CME Iceberg Order Detection](https://arxiv.org/pdf/1909.09495); [Impact of Iceberg Orders — Frey/Sandås](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1108485) | стакан .50 | дорого |
| L-05 | Стена-приманка (спуфинг/лееринг): полная отмена стены за 1–3 с **до** касания ценой (а не после) — признак «фальшивой» стены сильнее, чем просто «стена исчезла» (Г-05), потому что ловит именно момент относительно приближения, а не факт исчезновения | классический спуфинг снимается перед самым исполнением, чтобы не потребить свою же заявку — снятие после захода означает другое (реальное решение убрать риск) | [Learning the Spoofability of LOBs](https://arxiv.org/html/2504.15908v1); [Layering — KX glossary](https://kx.com/glossary/layering/) | стакан .50 | дорого |
| L-06 | Скорость и «токсичность» ленты сделок перед касанием (VPIN-подобная мера: дисбаланс объёма покупок/продаж в объёмных, не временных, барах за 1–2 мин до касания) предсказывает пробой лучше, чем ход цены | однородный по стороне поток — подпись информированного продавца (adverse selection), а не шум розницы | [VPIN — Easley/López de Prado/O'Hara](https://www.quantresearch.org/VPIN.pdf) | сделки | дорого |
| L-07 | Автокорреляция знака сделок (устойчиво односторонний поток за 1–3 мин, а не чередующийся) перед касанием — подпись расщеплённого метаордера крупного игрока, который «пройдёт сквозь» стену | order splitting даёт длинную память знака потока; устойчивый по знаку поток — не случайные встречные сделки, а один участник исполняет план | [Long memory of order flow — order splitting](https://arxiv.org/pdf/2307.02375) | сделки | дорого |
| L-08 | Каскад ликвидаций-прокси **без подписки на фид ликвидаций** (владелец отклонил Г-16): всплеск объёма и скорости однонаправленных агрессивных сделок (из уже записанной ленты) за 30–60 с до касания — признак принудительных продаж, нечувствительных к цене; такая просадка не откатывается | форс-майорные закрытия толкают цену дальше при исчезающей ликвидности; отличается от органической просадки тем, что продавец не остановится у стены | [Cascade mechanism — arxiv 2607.27070](https://arxiv.org/html/2607.27070); [Slippage-at-Risk framework](https://arxiv.org/pdf/2603.09164) | сделки | дорого |
| L-09 | Всплеск мелких однонаправленных сделок высокой частоты прямо у стены («momentum ignition»): много мелких агрессивных сделок за короткое время вместо редких крупных — признак попытки спровоцировать пробой алгоритмами, а не органического давления | тактика HFT: создать временный дисбаланс, чтобы вызвать чужие стоп-заявки/алгоритмы и заработать на развороте или продолжении | [Momentum Ignition](https://www.momentumignition.com/) | сделки | дорого |
| L-10 | Вход не на первом касании, а после «прокола-возврата» (цена на секунды провалилась ниже стены и закрылась выше неё) — по опыту institutional-паттернов, вход после завершения свипа надёжнее входа во время самого свипа | стоп-заявки/алгосвипы у стены выбивает быстрая толпа; настоящее давление продавца исчерпывается в момент прокола, отскок начинается после, а не во время | [Liquidity sweep / stop hunt patterns](https://www.quantum-algo.com/blog/guides/liquidity-sweep-trading-complete-guide/) | стакан .50 | средне |
| L-11 | Размер собственной заявки в лестнице входа стоит уменьшать на рангах, которые исполняются «слишком легко» (близко к фронту очереди на подходе) — там же выше цена анти-селекции по модели ценности места в очереди | эмпирическая находка: чем выше вероятность исполнения места в очереди, тем хуже пост-исполнительная доходность — плата за лёгкое исполнение | [Value of Queue Position — Moallemi](https://moallemi.com/ciamac/papers/queue-value-2016.pdf); [Market Maker's Dilemma](https://arxiv.org/pdf/2502.18625) | стакан .50 | средне |
| L-12 | Микроцена (microprice: середина, взвешенная размерами лучших бид/аск) расходится с последней сделкой в момент касания — вход только если микроцена уже тянет вверх, а не просто цена коснулась стены | микроцена быстрее ловит перекос спроса/предложения на первом уровне книги, чем сырой mid или last | [Order Book Imbalance / microprice — hftbacktest tutorial](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html) | стакан .50 | средне |
| L-13 | Взвешенный по расстоянию дисбаланс по всей глубине 50 уровней (не только у лучшей цены) — уточняет Г-07 («что за стеной»/«лестница») непрерывным числовым признаком вместо качественной оценки | глубже, чем top-of-book — литература показывает, что более глубокие уровни OBI дают предсказательную силу сверх top-level | [OBI predictive power — emergentmind](https://www.emergentmind.com/topics/order-book-imbalance-obi) | стакан .50 | дорого |
| L-14 | Состав ленты, съедающей стену: много мелких сделок (розница/грайндинг) против нескольких крупных (один продавец) — уточняет механизм Г-04 (поглощение) составом потока, а не только объёмом | одна крупная сделка = один информированный участник, который может продолжить; много мелких — рассеянное давление, которое быстрее выдыхается | [Trade size and informed trading — VPIN literature](https://medium.com/@simomenaldo/understanding-order-flow-toxicity-58fa317b3d01) | сделки | дорого |
| L-15 | Фандинг-рейт на экстремуме (верхний/нижний дециль за N часов) — сигнал перегретой перекошенной позиции рынка; вход в лонг-отскок при экстремально отрицательном фандинге (шорты перегружены) статистически другой режим, чем при экстремально положительном | скученность на одной стороне через ставку фандинга исторически предшествует резким разворотам против переполненной стороны | [Funding rate anticipates reversals — ForkLog](https://forklog.com/en/the-funding-rate-how-it-helps-anticipate-price-reversals-in-bitcoin-and-ethereum/) | внешнее (фандинг Bybit REST, не пишем сейчас) | средне |
| L-16 | Издержка переноса фандинга: если удержание 4 ч пересекает расчёт фандинга (каждые 8 ч), долгая лонг-позиция платит при положительном фандинге — сейчас эта стоимость не моделируется отдельно от комиссий/проскальзывания (Г-18) | прямая денежная утечка, не видная в комиссиях биржи, пропорциональная времени удержания через границу расчёта | [Funding payment mechanics — Coinbase](https://www.coinbase.com/learn/perpetual-futures/understanding-funding-rates-in-perpetual-futures) | внешнее (фандинг Bybit REST) | средне |
| L-17 | Тонкая (секунды) синхронизация BTC→альт: лаг между движением BTC и реакцией конкретной альты оценивается литературой в 16–118 с — признак на входе «BTC уже двинулся, альта ещё не отреагировала» отличается от часовых осей режима Г-09/Г-10/Г-11 масштабом (секунды, не часы) | альты реагируют на общий шок с задержкой информационной диффузии; за секунды до отражения — предсказуемое досрочное движение | [Price Transmission BTC→altcoins, high-frequency](https://link.springer.com/article/10.1007/s10690-026-09589-z) | стакан .50 (обе монеты) | дорого |
| L-18 | = Г-17 (Binance ведёт цену), но конкретно как поправка на **направление** входа: при расхождении Binance/Bybit цены стены в момент касания на 5+ bps — не входить, дать цене на Bybit «догнать» | пробой Bybit-стены иногда объясняется чужой ценой, которая уже там; вход без учёта чужой цены платит за то, что уже случилось на другой бирже | [Price transmission BTC/altcoins](https://link.springer.com/article/10.1007/s10690-026-09589-z) | внешнее (Binance, новая биржа) | дорого |
| L-19 | = Г-14 (время суток/выходные), но с конкретными часами из литературы: пик волатильности/неликвидности 16–17 UTC («час чая»), всплеск на азиатском открытии ~00 UTC, затишье 02–06 и 21–23 UTC, будни вторник–четверг лучше выходных | ликвидность и состав участников не одинаковы по часам — стены в неликвидные часы держат/ломаются иначе, чем в пиковые | [Crypto trades at tea time — Springer](https://link.springer.com/article/10.1007/s11156-024-01304-1); [Periodicity in crypto volatility/liquidity](https://arxiv.org/pdf/2109.12142) | есть (метка времени в записи) | дёшево |
| L-20 | Круглые числа как источник **чужих стоп-заявок** — уточняет Г-08 не тем, где ставить свой вход, а тем, где **не ставить свой стоп** (сразу за круглым числом, где кластер чужих стопов сносит цену дальше, чем оправдано движением) | стопы длинных позиций кластеризуются чуть ниже круглого числа, коротких — чуть выше; собственный стоп в этой зоне попадает под чужой снос | [Round number stop clustering](https://tradeciety.com/the-order-clustering-effect-around-round-numbers) | стакан .50 | дёшево |
| L-21 | Портфельный тормоз на опережение: число монет пула, **одновременно приближающихся** к своей стене прямо сейчас (не только открытых позиций, как в Г-20) — заранее снижает размер новых входов при системной просадке, до того как сработают все касания | Г-20 ограничивает уже открытые позиции постфактум; здесь сигнал раньше — по числу подходов, что даёт время среагировать до одновременного набора | [Cascade / systemic liquidity evaporation](https://arxiv.org/html/2607.27070) | стакан .50 (весь пул) | средне |
| L-22 | Единый «микроструктурный балл направления» перед касанием — взвешенная сумма OBI (L-01), OFI (L-02) и автокорреляции знака сделок (L-07) как один гейт входа, а не три отдельных признака | по литературе OBI/OFI перекрывают более простые метрики (сделочный дисбаланс становится избыточным при наличии OBI/OFI) — значит, лучше один составной признак, чем три коррелирующих | [OBI supersedes trade imbalance](https://www.emergentmind.com/topics/order-book-imbalance-obi) | стакан .50 + сделки | дорого |
| L-23 | Отдельная разметка `block`-сделок и `RPI`-сделок (уже пишутся в бинлог, `src/bybit/ws.rs:119-128`) при расчёте «съедено ли объём стены»: они не потребляют видимую ликвидность, поэтому текущий Г-04/эффект «съели» может ошибочно засчитывать долив как исполнение и наоборот | блочная/RPI-сделка увеличивает объём в ленте, не убирая видимый уровень — без разметки метрика поглощения (Г-04) смешивает два разных события | [hft-underground-2026-09-16.md §12 (внутренняя находка)] | сделки | дорого |
| L-24 | Проверка на «фальшивое устойчивое касание»: стена, которая ни разу за час не потеряла заявок от отмен (только от исполнения) — что менее ликвидная монета «подделывает» устойчивость отсутствием активности, а не силой спроса | контроль в духе Г-03 (фальшивая стена на случайном уровне), но для *активности*, а не для наличия стены — тихая монета может выглядеть «держит», просто потому что там никто не торгует | [Resiliency measurement caveats](https://www.sciencedirect.com/science/article/abs/pii/S1386418106000528) | стакан .50 | средне |
| L-25 | Вход после проверки чужого fill-rate: если модель очереди (`--queue-model prob:3`, уже есть в бэктесте, F10) даёт нашей заявке высокую вероятность исполнения на дальнем ранге лестницы — снижать размер этого ранга (см. L-11), не входить полным размером | существующая модель очереди уже оценивает вероятность нашего исполнения; неиспользуемый сейчас выход этой модели — прямой вход в размер позиции, а не только да/нет | [Queue-value decomposition — fill probability × adverse selection](https://moallemi.com/ciamac/papers/queue-value-2016.pdf) | стакан .50 (модель очереди в коде) | средне |
| L-26 | Режим «сильная устойчивость» vs «режим отмен» первых секунд после касания (см. L-03) как **фильтр выхода**, а не только входа: если после входа книга уходит в режим отмен — закрывать раньше дедлайна/трейла, не дожидаясь стопа | тот же механизм резилентности работает и после нашего входа: ранний сигнал «сопротивление исчезает» дешевле, чем стоп 2 % позже | [Resiliency regimes](https://opus.lib.uts.edu.au/bitstream/10453/98964/1/Lo_Hall_Resiliency_of_the_limit_order_book_Accepted_Manuscript.pdf) | стакан .50 | дорого |

## Данные / гипотезы по классам (сводка)

- **стена** — 6 (L-04, L-05, L-13, L-14, L-20, L-23)
- **режим** — 3 (L-15, L-19=Г-14, L-24)
- **подход** — 6 (L-01, L-02, L-06, L-07, L-17, L-22)
- **реакция** — 3 (L-03, L-08, L-09)
- **вход** — 4 (L-10, L-11, L-12, L-25)
- **выход** — 1 (L-26)
- **портфель** — 1 (L-21)
- **издержки** — 1 (L-16)
- **направление** — 2 (L-18=Г-17, L-22 пересекается с подходом)

Итого 26 строк (L-01…L-26), из них 2 явно помечены как совпадающие с банком (L-18=Г-17, L-19=Г-14) и оставлены ради
конкретных цифр/механизма, которых в `HYPOTHESES.md` нет дословно.

## Источники

- [Order Book Imbalance in High-Frequency Markets — emergentmind](https://www.emergentmind.com/topics/order-book-imbalance-obi)
- [Order Book Imbalance — QuestDB glossary](https://questdb.com/glossary/order-book-imbalance/)
- [Cont, Kukanov, Stoikov — The Price Impact of Order Book Events (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822)
- [The Price Impact of Order Book Events (arXiv PDF)](https://arxiv.org/pdf/1011.6402)
- [Resiliency of the Limit Order Book — Lo & Hall (UTS)](https://opus.lib.uts.edu.au/bitstream/10453/98964/1/Lo_Hall_Resiliency_of_the_limit_order_book_Accepted_Manuscript.pdf)
- [Measuring the resiliency of an electronic limit order book — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1386418106000528)
- [Learning the Spoofability of Limit Order Books (arXiv)](https://arxiv.org/html/2504.15908v1)
- [Layering — KX glossary](https://kx.com/glossary/layering/)
- [CME Iceberg Order Detection and Prediction — Zotikov (arXiv)](https://arxiv.org/pdf/1909.09495)
- [The Impact of Iceberg Orders in Limit Order Books — Frey & Sandås (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1108485)
- [Round Numbers: Support/Resistance & Levels — LuxAlgo](https://www.luxalgo.com/library/concept/round-numbers/)
- [The Order Clustering Effect Around Round Numbers — Tradeciety](https://tradeciety.com/the-order-clustering-effect-around-round-numbers)
- [Where does the criticality live? Liquidation cascades (arXiv 2607.27070)](https://arxiv.org/html/2607.27070)
- [Slippage-at-Risk (SaR) framework for perpetuals (arXiv)](https://arxiv.org/pdf/2603.09164)
- [The funding rate: anticipating price reversals — ForkLog](https://forklog.com/en/the-funding-rate-how-it-helps-anticipate-price-reversals-in-bitcoin-and-ethereum/)
- [Understanding Perpetual Futures Funding Rates — Coinbase](https://www.coinbase.com/learn/perpetual-futures/understanding-funding-rates-in-perpetual-futures)
- [VPIN — Easley, López de Prado, O'Hara (quantresearch.org PDF)](https://www.quantresearch.org/VPIN.pdf)
- [Understanding Order Flow Toxicity — Menaldo (Medium)](https://medium.com/@simomenaldo/understanding-order-flow-toxicity-58fa317b3d01)
- [The crypto world trades at tea time — Springer](https://link.springer.com/article/10.1007/s11156-024-01304-1)
- [Periodicity in Cryptocurrency Volatility and Liquidity (arXiv)](https://arxiv.org/pdf/2109.12142)
- [Price Transmission from Bitcoin to Altcoins, high-frequency evidence — Springer](https://link.springer.com/article/10.1007/s10690-026-09589-z)
- [Cross-cryptocurrency return predictability — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0165188924000551)
- [Online Learning of Order Flow and Market Impact — order splitting / long memory (arXiv)](https://arxiv.org/pdf/2307.02375)
- [Momentum Ignition](https://www.momentumignition.com/)
- [Liquidity Sweep Trading: Stop Hunts Explained](https://www.quantum-algo.com/blog/guides/liquidity-sweep-trading-complete-guide/)
- [The Value of Queue Position in a Limit Order Book — Moallemi](https://moallemi.com/ciamac/papers/queue-value-2016.pdf)
- [The Market Maker's Dilemma: Fill Probability vs Post-Fill Returns (arXiv)](https://arxiv.org/pdf/2502.18625)
- [Market Making with Alpha — Order Book Imbalance — hftbacktest docs](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html)
