# TK-364 · шаг 1 (новая постановка CEO 23:31): юниверс перпов Bybit с ончейн-спотом

Артефакт: `docs/findings/tk364-universe-2026-10-22.csv` (808 USDT-перпов Bybit; скрипт `tools/compute/tk364-universe.py`).
Снимок на 22.10 (24 ч): тикеры Bybit linear/spot + DexScreener search (пулы с ликвидностью ≥ $50 тыс.). Это **текущий срез**,
а не янв–сен 2026: состав и объёмы за прошлые месяцы нужно перепроверить по самим свопам.

## Числа
- Из 808 перпов найден ончейн-пул (≥ $50 тыс. ликв.) у 139; без мажоров — 127.
- «Ончейн-доминантные» (DEX-объём 24 ч > спот Bybit 24 ч, DEX ≥ $1 млн, оборот перпа ≥ $1 млн): **20** монет —
  BSC 10, Solana 5, Robinhood-chain 3, Base 2.
- Ядро по обороту перпа (млн $: перп / OI / спот Bybit / DEX): ZEC 465/175/0/5,8 (BSC — ZEC-обёртка, скорее не
  первичный рынок) · IRYS 73/7/0/8,5 · BTW 32/15/0/3,4 · AXIS 17,5/1,5/0/10 (Base) · TRUMP 17/24/1,5/4,7 · AEROM 14,5/21/3,3/3,4 ·
  BTR 12,5/5,5/0/5,3 · CRCL 11/15/0/6,4 · PONS 7,8/11/1/9 · CASHCAT 7,8/5,3/0/2,7 · JUP · ZEREBRO · ARIA · MEWM · HUMA · DOS · ARK · STONK · DGAI · KMNO.
- Список из 20 — `data/tk364/core.json` (gitignored). Мало Solana-мемов: у большинства спот на CEX уже есть.
- Оговорки: DexScreener сопоставляет по тикеру (возможны чужие одноимённые токены — проверять адрес контракта);
  ZEC/CRCL/STONK — токенизированные/обёрнутые, не первичная цена; «PONS»/«BTW» не проверены на манипуляции (шаг 5 CEO).

## Источник исторических свопов (без трат и регистраций — не проверялось на живых запросах)
| источник | история | время | массовость | цена | вывод |
|---|---|---|---|---|---|
| Dune `dex.trades` / `dex_solana.trades` | с запуска пула | блок (сек); у Solana слот | SQL по списку контрактов, все сети сразу | free-тариф мал по кредитам; платный от ≈$400/мес (не проверено) | 1-й выбор: один запрос на 20 токенов |
| Bitquery DEXTrades | да | блок/слот, есть Jito-tip у Solana | GraphQL/стрим, лимиты по очкам | платно (не проверено) | 2-й: нужен для разметки арбитражёров (tip) |
| Flipside/Allium | да | блок | SQL | Flipside free ограничен | запасной |
Время блока: BSC ≈0,45 с, Base 2 с, Solana ≈0,4 с — горизонты ≥ 1–10 с годятся, 100 мс — нет.

## Что дальше
1. Решение CEO: регистрация на Dune (free) + проба на 1 токене × 3 суток (ключ API → имя переменной `DUNE_API_KEY`). Расход $0 на пробе.
2. Перп Bybit для монет вне пула В-171 — `lob import-archive` (проверить покрытие архива по 20 монетам).
3. Протокол псевдотеста (В-54, пороги X, T, фильтр манипуляций) — Судье до счёта.

## v2 (23:55, по возврату Судьи): CEX-споты, контракт, мажоры
Скрипт `tools/compute/tk364-universe2.py`, csv `docs/findings/tk364-universe-v2-2026-10-22.csv`, мажоры `docs/findings/tk364-majors-2026-10-22.json`. Срез 22.10 (24 ч), не янв–сен.
- Споты 24 ч добавлены: Binance (USDT/USDC/FDUSD), Upbit (KRW→USD по KRW-USDT), OKX, Bitget + Bybit spot. «Лидер» = максимум из {DEX по контракту, Binance, Upbit, OKX, Bitget, Bybit spot}; колонка `lead`.
- Контракт: DEX-объём — сумма всех пулов (ликв. ≥ $50 тыс.) данного адреса через DexScreener `tokens/{addr}`; адрес сверен с CoinGecko `platforms` (`cg_id`; пусто = не найден → обёртка/чужой токен; `cg_wrapped` = токенизированный/обёрнутый).
- Мажоры исключены (топ-20 CoinGecko нестейблов + BTC/ETH/SOL/XRP): BTC, ETH, BNB, XRP, SOL, TRX, FIGR_HELOC, ZEC, HYPE, DOGE, XMR, WBT, LINK, LEO, ADA, NEAR, XLM, RAIN, BCH, LTC. ZEC теперь в мажорах (и его BSC-пул — обёртка).
- Из 139 с пулом без мажоров осталось 128; лидер DEX у 41, Binance у 53.

Ядро «ончейн-лидер» (DEX > сумма всех CEX-спотов; DEX ≥ $1 млн, перп ≥ $1 млн), млн $ 24 ч:

| монета | сеть | перп | DEX | CEX-сумма | CoinGecko | прим. |
|---|---|---|---|---|---|---|
| AKE | bsc | 43.8 | 13.4 | 0.0 | akedo |  |
| BTW | bsc | 32.4 | 3.4 | 2.6 | bitway |  |
| VVV | base | 19.3 | 15.9 | 9.5 | venice-token |  |
| AERO | base | 14.5 | 14.7 | 13.0 | aerodrome-finance |  |
| BTR | bsc | 12.5 | 6.1 | 0.0 | bitlayer-bitvm |  |
| CRCL | robinhood | 11.3 | 6.4 | 0.0 | circle-internet-group-robinhood-tokenized-stock | токенизированная акция — не первичный рынок |
| PONS | robinhood | 7.8 | 9.1 | 5.6 | pons |  |
| CASHCAT | robinhood | 7.8 | 2.7 | 1.3 | cash-cat |  |
| ZEREBRO | solana | 6.3 | 1.6 | 1.3 | zerebro |  |
| ARIA | bsc | 5.1 | 2.4 | 0.1 | aria-ai |  |
| DOS | bsc | 1.9 | 1.6 | 3.1 | dappos |  |
| ARK | bsc | 1.8 | 2.5 | 1.9 | ark-3 |  |
| STONK | solana | 1.7 | 3.8 | 0.0 | stonk-3 |  |
| DGAI | bsc | 1.0 | 6.4 | 5.9 | dgrid-ai |  |
| KMNO | solana | 1.0 | 3.1 | 2.0 | kamino |  |

Итог: 15 монет (v1 было 20): выпали те, где есть спот CEX крупнее DEX — TRUMP, JUP, HUMA (Binance), MEW (Upbit), ZEC (мажор); обёртки BSC (TRUMP BSC не найден в CoinGecko) не сидят в ядре.
Отдельно — **ведёт Upbit (корейский спот), не DEX**: IRYS (перп 73, Upbit 71), IOST (перп 63, Upbit 18), ONDO (перп 36, Upbit 16), DOOD (перп 32, Upbit 43), AXIS (перп 18, Upbit 122), ERA (перп 6, Upbit 5), MEW (перп 5, Upbit 4), CP (перп 3, Upbit 9). Это другой источник «лидлага» (KRW-спот → Bybit), проверяется публичным REST Upbit без ключей — кандидат на отдельную постановку.
Оговорки: объёмы DexScreener — самоотчёт пулов, возможны вошинг-трейды (шаг 5 CEO, фильтр манипуляций — до счёта); CRCL — токенизированная акция; контракты сверены по CoinGecko, не по сайтам проектов.

## v3 (23.10 00:20, CEO 00:05 / владелец: «хайкапы тоже смотрим»): группа high
Скрипт `tools/compute/tk364-universe3.py`, csv `docs/findings/tk364-universe-v3-2026-10-23.csv` (колонка `group` = high/mid), списки групп `docs/findings/tk364-groups-2026-10-23.json`. Срез 22–23.10 (24 ч), не янв–сен.
- Группы: **calib** (мажоры калибровки) = топ-5 нестейблов CoinGecko BTC/ETH/BNB/XRP/SOL; **high** = нестейблы рангов 6–50 (45 шт.); **mid** = остальные (основное).
- Из 45 high у Bybit USDT-перп — 34; ещё 11 без перпа (FIGR_HELOC, WBT, LEO, RAIN, USDG, USYC, USDY, BUIDL, EURSAFO; SHIB/PUMP — под 1000-префиксом, не сопоставились — проверить). Из 34 пул ≥ $50 тыс. по адресу/тикеру нашёлся у 16; у 18 (HYPE, ADA, XLM, BCH, CC, AVAX, SUI, GRAM, HBAR, CRO, QNT, ENA, RLUSD, OKB, M, DOT, PAXG, MNT) — нет: нативные монеты своих сетей, ончейн-пула этого тикера нет.
- Лидер ликвидности у high почти везде — Binance. Млн $ 24 ч (перп / DEX / CEX-сумма): ZEC 465/9,2/510 · NEAR 268/0,8/234 · WLD 126/0/138 · XMR 110/9,1/0,6 (лидер DEX Solana — вероятно обёртка/чужой токен, проверить) · DOGE 86/0,1/116 · PEPE 67/0/58 · XAUT 47/1,8/122 · LINK 45/2,2/46 · UNI 42/5,9/77 · ONDO 36/1,8/41 (лидер Upbit) · AAVE 34/0,4/43 · TAO 32/1,1/38 · LTC 22/0,4/36 · ASTER 7,7/1,0/15 · TRX 5,5/0,1/43. (BTW в рангах CoinGecko попал в top-50 — он же в mid-ядре.)
- Вывод: у хайкапов DEX-доля 0–5 % объёма CEX → «лидер = DEX» не ожидается; группа в псевдотесте — контрольная (ждём ≈0 эффекта, отличие от mid — часть вывода). Отдельный вопрос: wrapped-пулы ZEC/NEAR/TAO/LTC (`cg_wrapped=1`) — не первичная цена, исключать из счёта.
- Для хайкапов дешёвая проба: пулы ETH L1 (UNI, LINK, AAVE, ONDO) — основной ончейн-рынок, Dune/Bitquery те же; ключ не нужен до решения по Dune (см. ask-owner Судьи 23:48).

## v4 (23.10 00:22, возврат Судьи по v3): группа high исправлена + проба RPC BSC
**high v4** — `tools/compute/tk364-universe4.py`, csv `docs/findings/tk364-universe-v4-high-2026-10-23.csv` (47 строк: high 45 + gold 2), списки `tk364-groups-2026-10-23.json`. v3-csv/раздел v3 выше заменены этим (числа в отчёте = фильтр csv).
- Ранги 6–50 берутся из нестейблов: исключены стейблы, токенизированные фонды/казначейки (USDG, USYC, RLUSD, USDY, BUIDL, EURSAFO, USDGO), обёртки/ликвидный стейкинг; золото XAUT/PAXG — `group=gold`, вне рангов, пометкой.
- Перп Bybit — по тикеру с префиксами 1000/10000/1000000 и алиасам SHIB→SHIB1000USDT, PUMP→PUMPFUNUSDT. Из 45 high перп есть у 39; без перпа: FIGR_HELOC, WBT, LEO, RAIN, HTX, BGB (в csv — с пустым `perp`, все 45 строк на месте).
- Токены — только каноническими адресами CoinGecko `platforms` по id (копии: DOGE-0x4206…, PEPE-BSC-peg, XMR-Solana-WXMR больше не засчитываются). Пул ≥ $50 тыс. ликвидности + перп: **21 из 45** (LINK, UNI, GRAM, SHIB, CRO, QNT, ENA, BTW, AAVE, PUMP, ONDO, WLD, PEPE, MNT, ASTER, WLFI, SKY, MORPHO, ICP, VVV, ARB); у нативных монет своих сетей (ZEC, HYPE, DOGE, XMR, ADA, NEAR, XLM, SUI, AVAX, DOT, TAO, TRX, LTC, BCH, HBAR) канонического EVM/Solana-контракта нет — «пул есть» = нет. PEPE/SHIB — у каноники ETH-пул $1,3 / $0,04 млн против CEX-десятков млн → контроль. **PUMP** (Solana, DEX $26 млн, перп PUMPFUN) и VVV (Base) — реальные кандидаты внутри high.
- Dune из плана убрана (владелец, ключ платный).

## Проба публичных RPC BSC (23.10 00:20, без регистрации и трат)
Пул: AKE/WBNB PancakeV3 `0x4D3bF29Ba30f8Bfe4624e7678709afA195689c5D` (DexScreener: $12,8 млн за 24 ч, ликвидность $2,6 млн, создан 20.08.2025 — янв–сен 2026 покрыт). Скрипт `tools/compute/tk364-bsc-probe.py` (поиск блока по времени, eth_getLogs Swap, время блока).
| RPC | eth_getLogs | история |
|---|---|---|
| bsc-dataseed.bnbchain.org, dataseed3.ninicoin.io, defibit | «limit exceeded» даже на 50 блоках (после 1–2 запросов) | — |
| rpc-bsc.48.club | ≥2000 блоков за вызов, свежие данные есть (444 свопа/2000 блоков) | **заголовки только ≈6 суток назад** (блок 128,05 млн; 10 млн блоков назад — null) |
| 1rpc.io/bnb | ≤ 50 блоков | — |
| bsc.blockrazor.xyz | ≤ 25 блоков | — |
| publicnode, nodies | 403 | — |
| drpc, meowrpc, blastapi | 429 | — |
| ankr | нужна авторизация | — |
**Итог: янв–сен 2026 публичными RPC BSC получить нельзя** — нет архива (≤ 6 суток), остальные режут лимитами. Свежие 6 суток через 48.club — возможны (для проверки инструмента, не для 9 месяцев). Нужен архивный источник: ключ (NodeReal/Alchemy/QuickNode free — архив, лимиты по запросам), The Graph (ключ), Bitquery/Flipside — регистрация только словом владельца (CEO 00:11). Ни одного свопа за янв–сен не скачано; сверка с DexScreener по числу свопов за сутки — не сделана.
