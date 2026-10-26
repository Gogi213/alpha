# TK-410 — охват настоящей середины пула (26.10 ~09:00 GMT+4)

Источник: calc `/data/tk0393/tk410/{mid-all,mid-eth,cover.csv}` + sqd*/summary. Монеты с книгой gev2 (best-пул по vol24).

| класс | середина есть | нет | не-EVM |
|---|---|---|---|
| G (остальные) | 188 | 41 | Solana 66, Sui 7 |
| H (тяжеловесы) | 14 | 0 | Solana 1 |
| P (практики) | 1 (Q) | — | 13 монет P в gev2 нет (спот по сделкам) |

Было 20/394 → EVM-пулов с серединой теперь 203 из 244.

**2 пустых eth-пула (56/58):** OGN `0x56a3…cf86` и CVX `0xb576…0b0e0d4` — оба **Curve** (events TokenExchange, нет sqrtPriceX96/Sync) — парсер v3/v2 их не видит. Причина: тип пула, не сбой. Остаются без середины (или Curve-путь: балансы пула — отдельный парсер).

**41 «нет в сырье»:** 39 eth + 1 base — **Uniswap v4** (poolId 66 зн.) + base aerodrome (KGEN, VVV, YFI), avalanche traderjoe (GUN), curve (CRV). Для v4 в `sqd/ethereum-v4/swaps.csv.gz` лежат только amount0/amount1 (без sqrtPriceX96/liquidity): нужна перекачка Swap v4 с полем data (tk380-sqd-v4.py правится на разбор data: sqrtPriceX96, liquidity, tick). 37 из 43 пулов eth-v4 имеют свопы.

Следующий шаг: (1) перекачать/разобрать v4 eth+base с sqrtPriceX96; (2) Solana (swaps3) середина; (3) aerodrome/traderjoe — по типу события; (4) карта tk410-map.py на всём охвате, бой.
