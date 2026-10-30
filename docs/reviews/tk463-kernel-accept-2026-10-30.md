# TK-463 — приёмка ядра tk463-sim (Судья, 30.10)

Сдача: отчёт `docs/findings/tk463-kernel-2026-10-30.md` (b08200e3), бинарник calc `/data/tk0463/bin/tk463-sim`.

- пункт договора — да/нет — на чём проверено
- (а) ядро Rust, отдельный крейт, ячейки файлом, D2b-режимы SS/TW/БУ/ОТ — да — крейт tools/tk463-sim; допущения (а)–(г) подтвердил Исследователь (лог 21:13)
- (б) гейт D2a scan все пары — да — calc `/data/tk0463/d2a_full/full.log`: «cmp: IDENTICAL 252 из 252»
- (б) гейт scan+ctrl 7 монет × 3 ТФ — да — calc `/data/tk0463/out/gate.log`, строки IDENTICAL
- (б) D2b-режимы против эталона — да — calc `/data/tk0463/d2b/gate2.log`: 9/9 BYTES_EQ + IDENTICAL. Замечание: в таблице отчёта ссылка на `d2b/gate.log` — там старый вердикт cmp до правки b5226abe (РАСХОЖДЕНИЕ nan≠nan); верный файл — `gate2.log`
- (б) синтетика §9 п.2 — да — calc `/data/tk0463/synth/gate.log`: 6 строк IDENTICAL; покрытие — `cover-*.txt`
- (в) замер vs эталон и trade_fast, время D2a/D2b на 15 ядрах, строка «Алгоритм» — да — отчёт, таблицы и строка «Алгоритм»; D2b — оценка, отмечена как прокси
- [вживую] ctrl 1D одной монеты — да — см. ниже

## [вживую]
Где открыл: calc, `systemd-run --wait`, бинарник md5 fa014fb0 (тот же, что в регрессии D2a), вывод в `/data/tk0463/judge/`.
Срез: TWTUSDT, ТФ 1440, контроли (входы `out/TWTUSDT-1440.ctrl-in.csv`, seed 461), 1 поток.
Что видно: ядро вывело «загрузка 1.59 с, счёт 1.05 с», 40684 строк, вся работа 2,8 с; `tk463_gate.py cmp` против лежащего
`/data/tk0461/ctrl/TWTUSDT-1440.csv.gz` — «IDENTICAL 40684 строк». Сделок/с: ≈39 тыс./с на поток счёта против 0,15 тыс./с
эталона на ТФ 1440 (bench2 отчёта).

## Хвосты (вне заказа) — TK-464
D2b построчно не запускается (≈300 ГБ вывода) — агрегация в ядре; один проход минут на событие для всех ячеек (§10 (б));
одна загрузка свечей на монету; необъяснённые пропуски 4 выходов в bench3 и «пар 256» против 252 — rc и полнота в боевом
скрипте. В-73 («ещё прокачивать алгоритм до запуска D2b») — продолжение в TK-464.

## Повтор приёмки (21:3x) — ярлыки сдачи 21:27
Ярлыки [запуск:] переделаны на `ssh calc "grep …"` по логам calc; перепроверил сам по ssh: full.log «IDENTICAL 252 из 252»,
GATE.DONE «jobs 21 ok 42», d2b/gate2.log BYTES_EQ 9, synth/gate.log IDENTICAL 6, строка TWTUSDT 1440 ctrl IDENTICAL.
Ссылка отчёта на d2b исправлена на gate2.log (2e09dbff). Замечание (не меняет вывод): ярлык (5) `bash tools/compute/tk463-gate.sh 7 40`
локально ничего не проверяет — проверка пункта (5) — мой [вживую] выше и ярлык (2).

## Ключевые числа
| число | файл:строка |
|---|---|
| D2a 252/252 IDENTICAL, 30 949 856 строк | docs/findings/tk463-kernel-2026-10-30.md:8 |
| scan+ctrl 42/42 IDENTICAL | docs/findings/tk463-kernel-2026-10-30.md:9 |
| D2b 9/9 побайтно | docs/findings/tk463-kernel-2026-10-30.md:10 |
| синтетика 6/6 IDENTICAL | docs/findings/tk463-kernel-2026-10-30.md:11 |
| D2a полный: ядро 68,8 с, эталон ≈1450 с, fast ≈200 с | docs/findings/tk463-kernel-2026-10-30.md:25 |
| D2b 449 ячеек: ядро ≈58 мин (оценка), эталон ≈39 ч, fast ≈5,3 ч | docs/findings/tk463-kernel-2026-10-30.md:26 |

## Повтор приёмки — отказ кода (21:4x)
`accept` не допускает ярлык `[запуск: ssh calc "…"]`: argv[0] — только python/python3/bash + скрипт из репо. По делу всё сверено
(выше); вернуть автору на ярлыки: один скрипт в git, напр. `tools/compute/tk463-check.sh`, читает логи calc по `ssh calc` и
выходит ≠ 0 при несовпадении любого счётчика (D2a «IDENTICAL 252 из 252», GATE.DONE «ok 42», gate2.log BYTES_EQ 9 и IDENTICAL 9,
synth IDENTICAL 6, строка TWTUSDT 1440 ctrl IDENTICAL); ярлыки сдачи — `[запуск: bash tools/compute/tk463-check.sh]`.

## Повтор приёмки (b0288db2) — ярлыки на tools/compute/tk463-check.sh
Скрипт прочитан: один ssh calc, счётчики md5 бинарника, D2a 252/252, GATE.DONE, gate.log (IDENTICAL и ноль прочих строк), строка
TWTUSDT 1440 ctrl, d2b/gate2.log BYTES_EQ и IDENTICAL, synth; несовпадение любого — rc 1, нет ssh — rc 2. Запустил сам: «OK: все
счётчики сошлись», rc 0. Вердикт — принято; хвост — TK-464.

## Отказ кода (b0288db2) — bash диспетчера
`accept` гоняет ярлык `bash tools/compute/tk463-check.sh` из Windows: `bash` там — WSL (ext4.vhdx не найден), rc 1 до запуска
скрипта. Из Git Bash скрипт даёт rc 0. По делу принято; вернуть на форму ярлыка: тот же скрипт на Python
(`tools/compute/tk463-check.py`, `subprocess` ssh calc, те же счётчики, rc ≠ 0 при несовпадении), ярлыки — `[запуск: python tools/compute/tk463-check.py]`.
