# TK-427 пробы источников январь–июль 2026 (Судья, 27.10 ~22:00 GMT+4)

Gate HEAD https://download.gatedata.org/<рынок>/<мес>/BTC_USDT-<мес>.csv.gz:
202601 futures_usdt/trades 200 186641648
202601 spot/deals 200 128587547
202603 futures_usdt/trades 200 277578620
202605 futures_usdt/trades 200 162289284
202607 futures_usdt/trades 200 198127085
202601 futures_usdt/funding_updates 200
Gate API /futures/usdt/contract_stats from=01.01.2026: INVALID_PARAM_VALUE "from time exceeds 180-day limit"
Bybit /v5/market/kline linear BTCUSDT 01.01.2026 00:00: retCode 0, свеча 87594.9
Bybit /v5/market/open-interest linear BTCUSDT 5min 01.01.2026: retCode 0, openInterest 52062.614
calc 27.10: tk423-gate-dl MON=202608 job 1027183519940 rc 18:44 (старт 18:35, часы calc) — 9 мин
calc df /data: свободно 681G
