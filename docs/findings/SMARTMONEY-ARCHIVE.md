# Архив разработки смарт-мани (31.10.2026, TK-474, В-82)

Владелец 31.10: «ок убираем из альфы все что связано с разработкой смарт мани. оставляем только теоретическую базу и выводы.
возвращаемся к пампам» (В-82). Разработка (код, промежуточные таблицы, протоколы-процедуры, приёмки) убрана из дерева и лежит
в теге **`archive/smartmoney-2026-10`** (коммит `3b6beeff`; тег локальный, в origin не отправлен).

## Что осталось в дереве

**Итоговые отчёты и теория (`docs/findings/`):** `sancho-2026-10-30.md`, `sancho-strategy-2026-10-30.md`, `sancho-channel-2026-10-30.md`,
`sancho-xlsx-2026-10-30.md`, `bp-2026-10-30.md`, `bp-sancho-dictionary-2026-10-30.md`, `smartmoney-synthesis-2026-10-30.md`,
`sancho-bp-exits-2026-10-31.md`, `holdout-2026-2026-10-31.md`, `core-coins-2026-10-31.md`, `g017-sm-features-2026-10-30.md`, `loss-causes-2026-10-30.md`.
**Реестры и вердикты (csv):** `bp-claims-2026-10-30.csv`, `sancho-deposit-claims-2026-10-30.csv`, `sancho-bp-exits-q3-2026-10-31.csv`,
`holdout-2026-2026-10-31-q3.csv`, `core-coins-2026-10-31.csv`, `smartmoney-survivors-2026-10-30.csv`.
**Входы `tools/g017db/sm_survivors.py` (оставлены, чтобы не терять сборку признаков):** `sancho-2026-10-30-layer2.csv`,
`bp-b2-holm-2026-10-30.csv`, `smartmoney-synthesis-2026-10-30.csv`. Для `sm_survivors.py --check` нужны ещё `bp-g*-events-2026-10-30.csv.gz` (≈85 МБ) — достать из тега (ниже).
**Протоколы (`docs/research/`):** `P-TK451-sancho-layers12`, `P-TK454-sancho-trades`, `P-TK455-sancho-layer3`, `P-TK456-smartmoney-program`,
`P-TK456-appendix-bp-claims`, `P-TK457-b2-protocol`, `P-TK458-b2-g{1,2,3,32,4,41,42,5,6,61,63,7,8}`, `P-TK459-synthesis-S`, `P-TK461-exits`,
`P-TK473-holdout-2026` (на них ссылаются оставленные отчёты).
**История команды:** `.claude/tickets/`, `.claude/goals/`, `.claude/decisions.md`. **Общее с пампами:** `tools/g017db/` (+ БД G-017), `tools/compute/progress.sh`,
`tools/compute/tk450*` и `tk453*` (хвост TK-453 — свечи Binance, опрос до 31.10 22:00; сырьё на calc `/data/tk453`, `/data/tk450`).

## Что убрано (в теге)

- `tools/compute/{tk451*,tk46x*,tk47x*,sancho*,bp*,sm_*,check_tk466*,test_sancho*,test_tk4*}`, `tools/{bp_*,sancho*,sm_s*,tk461*,tk462*}`,
  `tools/tk463-sim/` (Rust-ядро событийного счёта, отдельный крейт; `src/` и корневой `Cargo.toml` не менялись).
- `docs/findings/`: промежуточные csv (events, years, slices, cells, split, curve, total, syms, axes), `tk4[5-7]*`, `sancho-trades-*`, `sancho-pilot`,
  `sancho-bp-exits-2026-10-31.csv` (полная таблица 21 288 вариантов) и `-check-*`, `core-coins-v2*`, `holdout-*-{coins,months,winners*,summary}`.
- `docs/research/`: остальные `P-TK45x…P-TK47x` (процедуры и csv), `docs/research/reviews/` приёмки/возвраты TK-45x…473, `docs/reviews/{TK-457-b1*,tk463-*,tk464-*}`.

## Ссылки в оставленных отчётах

Оставленные отчёты и протоколы — принятые Судьёй артефакты, их текст не правился: пути скриптов и таблиц, которых нет в дереве,
читаются как «лежат в теге `archive/smartmoney-2026-10`».

## Как восстановить

```bash
git checkout archive/smartmoney-2026-10 -- <путь>          # один файл или каталог
git show archive/smartmoney-2026-10:<путь> > <куда>         # без записи в дерево
git ls-tree -r --name-only archive/smartmoney-2026-10 -- tools/tk463-sim   # что было
```

Сырьё не трогали (вне git): локальные `data/tk451`, `data/c12-rep`, `data/bp-video`, `data/g017/g017.db`; на calc `/data/g017/g017.db`,
`/data/tk0451…tk0473`, `/data/tk450,453,455,458,459,462`, `/srv/alpha-sda/candles-1m/binance-um`.
