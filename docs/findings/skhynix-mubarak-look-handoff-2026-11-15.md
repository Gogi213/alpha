# Передача дел: взгляд на SKHYNIX и MUBARAK (Bybit — OKX), 15.11.2026 ~20:15 GMT+4

Ветка `bb/g-022-skhynix-look-v1-thr_5xi7vejsga`. Оба отчёта и графики готовы и закоммичены; открытых шагов нет.

- Отчёты: `docs/findings/skhynix-bybit-okx-2026-11-15.md` (+ `.png`, `-hourly.csv`), `docs/findings/mubarak-bybit-okx-2026-11-15.md` (+ `.png`, `-hourly.csv`). Копии PNG — `Downloads/skhynix-bybit-okx.png`, `Downloads/mubarak-bybit-okx.png`.
- Код: `tools/arb_stats/pair_look.py` (счёт одной пары; `KRX=1` — режимы KRX для SKHYNIX), `pair_plot.py` (график), `parse_tail.py` (хвост книг из живых файлов).
- Запуск на srv (`/opt/swings/tools/arb_stats/`, результаты `/data/swings/skhynix/`): `systemd-run --pipe --wait --nice=15 -p CPUQuota=200% -p MemoryMax=12G [-E KRX=1] python3 pair_look.py <parsed[:parsed2]> <t0_мс> <t1_мс> res.json series.npz <символ_Bybit> <символ_OKX> [t1_вкладки_мс]`, затем `pair_plot.py series.npz res.json res.json.eps out.png --title … [--clip ±%] [--krx] [--burst-from мс]`.
- Таблицы отчётов собирает одноразовый генератор в `data/skhynix/make_doc*.py` (в git не входит, `data/` игнорируется); при пересчёте правки вносить в отчёт руками или восстановить генератор из `git show` отчёта.
- Поправка к первому отчёту (SKHYNIX): разбор книг оставляет запись только при смене (bid, ask); размер на лучшем уровне — на момент смены цены. Текст § 1 исправлен тем же коммитом.
- Проверки: счёт (I) воспроизводит строки вкладки для обеих монет (SKHYNIX: 1 эпизод 0,283 % / 90 мс / +0,154 %; MUBARAK: 23 эпизода, пик 0,222 / 0,470 / 0,594 %, схождение 18 мс / 925 мс, чистая +0,064 / +0,301 %); (II) проверен на экстремумы по ряду; (I) SKHYNIX проверен прямым перебором.
