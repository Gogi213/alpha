# TK-328 — П-22 §6: вход «касания спота → исполнение на бинлоге фьюча», гейт и [вживую]

Код: `lob touches-project` (CSV касаний спота → цены фьюча по §3.2: `P_f = F_mid·P_wall/S_mid`, тик в пассивную сторону,
`lob::project::project_tick`), далее обычный `bounce-grid --touches-from` на фьюч-бинлоге. `bounce-grid` не менялся.
Ветка tk328, головы: гейт d599edb6; скрипты `tools/compute/tk328/{gate-self.sh,spot2fut.sh}`; бинарник calc `/data/tk0328/b2/alpha-new` (4a2e55a9).

## Гейт «байт в байт» — self-проекция (ADAUSDT 2026-01-05, calc, alsched 1020172822194)
Спот == фьюч (один набор бинлога): проекция — тождество.
- GATE1: `touches-project` → CSV касаний совпал с исходным побайтно (28371 строка).
- GATE2: `bounce-grid` по исходному и по спроецированному CSV: тела `rounds.csv` (416 кругов) и `forms.csv` — md5 равны
  (`b4bcb4b4…`, `5dcf9af6…`). Различалась только строка-комментарий `# lob bounce-grid: … touches=csv(<путь>)` (orig/proj) —
  скрипт сравнивает тела без `#`-строк.

## [вживую] спот → фьюч, малый срез (ADAUSDT 2026-01-05, calc, alsched 1020173135597, rc=0)
Касания спота (`/srv/alpha-sda/binlog-spot/e-2026-01`, h3 notional $10 000): 4782 → после проекции 4782 (0 отброшено) →
`bounce-grid` на фьюч-бинлоге (`/data/alpha/epochs/e-jan/root`, prob:3, RTT В-68, behind/1to1/600 с, $100): 4556 сигналов,
293 круга, сумма нетто −2167 bps (ADA, один день — НЕ вывод о краю; число — признак того, что цепочка исполняется).
Проекция сдвигает цену на базис: ask спота 4009 → 4006 тика фьюча, bid 4004 → 4001 (≈ −3 тика ≈ −7 bps к споту),
что согласуется с базисом из `tk325-sync-basis-2026-10-20.md`.

## Правка по возврату Судьи (20.10, 835646eb)
- **Лоты (п.1):** `touches-project` пересчитывает 9 колонок в лотах (`size_at_touch`, `size_max_before`, `traded_during`,
  `frontrun_lots`, `swept_lots`, `traded_1s/2s/3s`, `flow_1h_lots`) + `depth_behind_lots` (если есть) по количеству:
  `lots·step_спот/step_фьюч`, до ближайшего лота; равные шаги — строка как есть (гейт self-проекции не затронут).
  Шаги — `step_e9` заголовка бинлога; в отчёте строка `lot_step_e9_spot_fut`. Тесты на разных шагах (ADA 0,01→1): 3 новых, 7/7 зелёные (calc).
  Силы в % (`strength_*`, `strength_flow_pct`) безразмерны — не меняются.
- **Гейт против B1 Летописи (п.2) — вход не тот:** B1 фьюча (`/data/p12r3a/b1-b`, `day-cells.sh`) идёт на `--signal approach`
  по `approaches-<SYM>.csv` (`wt/sub/D20/<сутки>/`), а не по `touches-<SYM>.csv`. `touches-project` подаёт только касания,
  поэтому «B1 через новый вход» требует проекции и `approaches-*.csv` (колонки: `price_tick`, `best_own_tick`, `best_opp_tick`,
  лоты `size_at_arm`, `flow_1h_lots`, `frontrun_lots_at_arm`, `traded_lots_at_arm`, `eat_60s_lots`, `size_max_60s_lots`,
  `depth_behind*_lots`). Без этого гейт к строке registry неприменим; self-проекция касаний (выше) остаётся честным гейтом самой проекции.
## Открытое для Судьи
`size_at_touch`/`frontrun_lots` остаются в лотах спота; `usd_min` и силы считаются в $, лот фьюча может ≠ лоту спота
(не проверено, на ADA не сверял) — Исследователь проверяет по монетам перед заявкой A/B/C.
