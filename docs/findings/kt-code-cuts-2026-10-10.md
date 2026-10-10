# TK-166 (В-222): где резать код в ядре — срезы по оставшимся КТ — 2026-10-10

Судья, только чтение. База замера — голова `audit/design-fixes-2026-09-22` (a46f7866 = master c65d22d9 + 69 коммитов;
`src/**/*.rs` 124 749 строк против 124 739 в master — расхождение 10 строк). Строки — `wc -l` по `git ls-files`.
«Потребитель» проверен в трёх местах: живые скрипты `tools/` (без `archive/`, без ELF `tools/compute/.claude`),
2 231 задание `/data/sched/jobs/*.json` + `/data/*.sh`, `/data/*/*.sh` на calc (grep отдельных файлов, без обходов),
вердикты `docs/efficiency-register.md` / `docs/findings/`.

## А. Резать сразу — потребителя нет, вердикт «отвергнуто» записан (≈ 9 100 строк Rust)

| что | где (файл:строка) | строк | основание | куда (тикет) |
|---|---|---:|---|---|
| Общий движок суток (Э-11) | `lob/backtest/shared_driver.rs`, `shared_multi.rs`(+tests), `sched.rs`, `shared_depth.rs`; `bounce_grid/drive.rs:557–644` (`drive_day_shared`) + ветка `:694–697`; тест `lob/backtest/tests.rs:1063–1109` | 1 747 + 88 + 4 + 47 ≈ **1 886** | Э-11 «не получен» (×1,26…×2,3 медленнее); `ALPHA_SHARED_ENGINE` 12 запусков, все пробы TK-049 05–06.10; `SHARED_CELLS/SHARED_K/SHARED_STATS` — только эти пробы | КТ-6a TK-161 (это 1 из «8 путей» С-16 — удалять, а не держать под флагом) |
| Индекс удержания (TK-048 К-4) | `lob/backtest/hold_index.rs`; ветки `fast_hold.rs:796–860`, `:933–950` (`hold_index_on/_check`), поле `:972`; тест `lob/backtest/tests.rs:2720–2816` | 409 + ≈ 85 + 97 ≈ **590** | `docs/findings/hold-index-k4-2026-10-09.md`: «закрыт, не окупается» (+45 % user); в боевых заданиях `ALPHA_HOLD_INDEX` нет | КТ-6a TK-161 |
| Развёртка горизонта (К-4) | `lob/backtest.rs:3146–3165` (`attempt_span_ns`/`horizon_grow`), вызовы `:3702`, `:3940` → `span_full` | ≈ **25** | `backtest-x4-2026-10-04.md` §К-4 «отвергнут — ×1,78 медленнее»; `ALPHA_HORIZON_GROW` — 0 заданий | КТ-6a TK-161 |
| Обрезка строк (`ALPHA_TRIM_ROWS`) | `lob/backtest/trim.rs` (`kept_rows`, `TrimRows`; реэкспорт `backtest.rs:84,87`); ветка `bounce_grid.rs:1010–1025` | 156 + ≈ 16 ≈ **172** | 0 из 2 231 задания и 0 скриптов ставят флаг | КТ-6b TK-162 (bounce_grid.rs) + 6a (backtest/) |
| `lob touch-profiles` | `commands/lob/touch_profiles.rs` + `touch_profiles/` | **1 122** | 0 скриптов, 0 заданий, входящих импортов 0 | КТ-6c TK-163 |
| `lob dashboard` | `commands/lob/dashboard.rs` + `dashboard/` + `dashboard_page.html`; `tools/serve_dashboard.py` (81) | **3 236** (+81 py) | 0 заданий; дашборд В-97 собирается `tools/dashboard/build.py` и его не зовёт; входящих импортов 0 | КТ-6c TK-163 |
| `lob fill-capacity` + `lob/capacity.rs` | `commands/lob/fill_capacity.rs` + `fill_capacity/`; `lob/capacity.rs` (единственный потребитель — fill_capacity) | 1 131 + 951 = **2 082** | 0 скриптов, 0 заданий, входящих импортов 0 | КТ-6c TK-163 |

Вместе со срезом — удалить их `ALPHA_*` (7 переменных: SHARED_ENGINE, SHARED_CELLS, SHARED_STATS, HOLD_INDEX,
HOLD_INDEX_CHECK, HORIZON_GROW, TRIM_ROWS) из учёта С-06/`RunFlags` — в `RunFlags` их не переносить.

## Б. Резать в КТ-6 вместо «под флагом» — боевой набор становится умолчанием

Боевой набор окружения (последние задания calc, 16 из 16 с явным env): `SKIP_SAME=1 SKIP_NOSIGNAL=1 SIG_CACHE=3000000
HOLDS_MEMO=1 FAST_HOLD=1 EVENT_STEPS=entry DIRECT_FEED=1 ADMIT_SOA=1 ADMIT_CACHE=1 APPROACH_BIN=1 TOUCH_BIN=1`
(+`BAND_COUNT_OFF=1` — **пустой**: код `bounce_grid.rs:280` реагирует только на `=0`). План КТ-6 («старые пути под флагом
до приёмки») дополнить: после приёмки гейта ветви «выкл» **удаляются** тем же тикетом, кроме тех, что служат оракулом
теста (медленное удержание против `fast_hold` — оставить только под `#[cfg(test)]`). Конкретно:
`EVENT_STEPS` значения `1`/`live`/`0` (`backtest.rs:1463–1480`, развилки `:1565`, `:2154`), `DIRECT_FEED`-выкл
(`backtest/feed.rs:424–436`), `ALPHA_TICK_STATS`/`ATTEMPT_STATS` — диагностика, в `RunFlags` как один `--diag`.
Объём — замерять по факту каждой ветки в КТ-6a (на глаз не даю).

## В. Резать по решению — исследовательский слой 09.2026 без потребителей (11 011 строк)

`pilot` 2 503, `profiles` 2 399, `shortlist`(команда) 1 983, `react` 1 400, `lob/markup.rs` 817, `watch`(команда) 685,
`markout`(команда) 583, `levels`(команда) 443, `power` 198. Потребителей 0 (tools, 2 231 задание), последние правки
11–24.09. Не режу сразу: (1) `pilot` — процедура `k` для пола H3 (В-30, CLAUDE.md «грабли»); (2) клубок импортов —
`profiles` отдаёт `size_bucket`, `read_verify_marker`, `FillModel`, `lifetime_bucket`, `HEADER` в `bounce_grid`,
`archive`, `backtest/feed`, `touches/numbers`, `lob/shortlist.rs`, `lob/touch_axes.rs`; `commands/lob/shortlist`
импортирует `lob/final_metrics.rs` (обратный слой). Порядок: вынести общие функции в `lob/` → архивный тег → удалить.
Решение «процедура В-30 остаётся/в архив» — CEO (это не вопрос исследования).
`lob backtest` (команда, 9-й вход в тот же движок): обработчик `backtest.rs` 639 + `args` 163 + `csv_out` 207 +
`profile_table` 95 + `pool` 105 = **1 209** + часть `backtest/tests.rs` (1 909, долю не мерил); 0 скриптов, 0
заданий. Резать в КТ-6a после выноса `read_tick_step`, `touch_view_of_approach`, `read_day_schedule`, форм
(`EntryForm…`) в `plan`/`forms`.

## НЕ резать

- Живой профиль «будет» (В-219/В-220 — живой рантайм отложен, не отменён): `feed/live`, `bybit/*` (conn, ws,
  trade_ws, rest, verify, latency), `commands/record`, `session` (6 468), `clock`, `probe`, `fee-rate`, `latency`,
  `pick` (3 793, пул В-34).
- Опора гейтов и замеров: `event-cache-probe` (225) — зовут `/data/tk048/{ecprobe,abdec,tk048-decprof}.sh`, TK-048 идёт;
  `binlog-stats` (921) — оракул теста `archive/tests.rs:402`; эталонные бинарники гейтов живут файлами на calc, срез
  исходника им не мешает.
- Живые подкоманды: `bounce-grid`, `bounce-verdict`, `touches`, `trades`, `archive`, `import-archive`, `validate`,
  `gaps`, `verify`; `fast_hold.rs` (боевой), `window_depth.rs`/`fast_depth.rs` (живой путь, не только trim).

## По КТ

| КТ (тикет) | добавить в объём | строк |
|---|---|---:|
| КТ-6a (TK-161) | А: общий движок, индекс удержания, развёртка, trim в backtest/; Б: ветки «выкл»; В: команда `lob backtest` | ≈ 2 650 + Б + 1 209 |
| КТ-6b (TK-162) | А: ветка trim `bounce_grid.rs:1010–1025`, `BAND_COUNT_OFF` (пустой) | ≈ 20 |
| КТ-6c (TK-163) | А: touch-profiles, dashboard (+py), fill-capacity+capacity | 6 440 |
| КТ-5 (TK-150) | `APPROACH_BIN`/`TOUCH_BIN` (=1 в бою) — после `cold ensure` выключенная ветка (чтение без кэша) = второй кодек, свести в один (С-30/С-35) | мерить в КТ-5 |
| КТ-3 (TK-147) | перед слиянием R2 — формы без строк в боевых `rounds.csv` Летописи не переносить | не мерил |
| КТ-4b, 4a.3 (TK-153/160) | срезов сверх своих нет; КТ-4b меньше на ≈ 2 650 строк, если КТ-6a срежет А до него | — |
| вне КТ (решение CEO) | В: исследовательский слой 09.2026 | 11 011 |

Итого Rust: А ≈ 9 100 сразу, + `lob backtest` 1 209, + В 11 011 по решению = до ≈ 21 300 из 124 749 (17 %).
