# Слияние tk479-statarb → master: проверка, 2026-11-12 — ПРИНЯТО

Предмет: `g022-master-merge` @ `6cfa8dbf` (merge --no-ff; родители `475b7f64` = master, `840b3c3d` = tk479-statarb).
Против master: 1433 файла — 318 A, 1098 D, 17 M (+44 462 / −545 592 строк). Гейт (test/clippy/fmt) здесь не гонялся — его
координатор запускает на srv отдельно; ПРИНЯТО действует при зелёном гейте.

| пункт | итог | доказательство |
|---|---|---|
| 1. Из master ничего не потеряно | подтверждено | `git diff --name-status 840b3c3d 6cfa8dbf` — ровно `R100 tools/compute/{→candles/}tk476-cl-install.sh` и `R100 …tk476_reasons.py`: результат = tk479 + перенос b54a5d73; блобы `f28a4725`, `3d034d38` совпадают с master, в корне `tools/compute/` копий нет. 475b7f64 — прежнее слияние tk479, его содержимое уже в tk479 |
| 2. Секретов нет, крупное перечислено | подтверждено (с замечанием З-1) | grep по добавленным строкам (ghp_/sk-/AKIA/Telegram-токен/PRIVATE KEY/`KEY=значение`) — пусто; `BYBIT_API_*` — только имена (AGENTS.md, preferences.md). `CRYPTOHFTDATA_API_KEY` читается из `/root/.chft.env` на сервере, значения нет. Файлов .env/.pem/.parquet/.zst/.db/архивов среди добавленных нет. Добавлено 7,5 МБ в 318 файлах; > 100 КБ — 10 файлов, 5,8 МБ (список ниже) |
| 3. Удалённое восстановимо | подтверждено | тег `archive/rpv-2026-11-12` → `cd7f0f04` (родитель 988210c2, предок результата). Из 1098 удалённых 574 есть в его дереве; остальные 524 (pump-png, tk413–449, g017db, research/sources) удалены по В-108 и все есть в `archive/pump-g017-2026-11` / `archive/pump-g017-2026-11-wip` (`comm` с деревьями тегов — 0 непокрытых). Плюс всё остаётся в истории через родителя `475b7f64` |
| 4. AGENTS.md / CLAUDE.md новые, ссылки живы | подтверждено (с замечанием З-2) | `CLAUDE.md` = `@AGENTS.md`; AGENTS.md — bb + pstack (73 строки, новый). Существуют: preferences.md, decisions.md, poteto-mode/SKILL.md, playbooks/landing.md, references/models.md, alpha-research/, G-022-plan, SETTLED.md, SETTLED-index.md, BUSINESS-TASK.md, EXPERIMENTS.md, JOURNAL.md, reviews/, ARCHITECTURE.md, COMMANDS.md, claude-md-history-*.md, src/{binlog,book,bybit,feed,lob,stats,commands}, `src/lob/strategy.rs`, tools/vps-check.sh (+ tools/srv-build-run.sh). `tools/collector/`, `wscore/` — по тексту AGENTS.md в ветках tk527/tk532 (проверено: есть там) |
| 5. Cargo.toml/Cargo.lock/src | подтверждено (с замечанием З-3) | Cargo.toml, Cargo.lock, vendor/ — без изменений. Изменён только `src/statarb/{cli,kernel,reference,tests}.rs` (+734/−68): TK-482 (delay_x, Rule::Funding, `bench --digest`) + исправление E-010 (эпоха минут фандинга). В добавленных строках src нет `todo!/unimplemented!/dbg!/TODO/FIXME/#[ignore]/#[allow]` |

Крупнейшие добавления (байт): `p40-d1-naive-gap-2026-11-10/naive.json` 1 723 205; `p40-universe-2026-11-09/probe_all.jsonl`
1 368 159; `p40-d1-naive-gap…/pairs.csv` 690 235; `spot-candles-coverage-2026-11-02.csv` 535 748;
`p40-d1-nobinance-ge20-2026-11-11.csv` 347 438; `p40-universe…/probe_all_pairs_mult.csv` 346 577; `…/recon_kept.json` 340 902;
`…/probe_all_pairs.csv` 255 586; `docs/COMMANDS.md` 140 840; `p40-universe…/prices.json` 124 686 (все в `docs/findings/`).

## Замечания (не меняют вердикт)

- **З-1.** ~5,8 МБ csv/json-выгрузок П-40 в `docs/findings/` — это промежуточные данные, по AGENTS.md им место в `data/`. Не
  нарушение прежней практики (в master уже лежат `pump-dump-episodes-sep-2026-10-27.csv` 26,6 МБ, `tk402-agg-2026-10-25.csv`
  21,2 МБ), но при следующей уборке — вынести в `data/` или Storage Box.
- **З-2.** AGENTS.md ссылается на `docs/research/P-40-*` — в результате (и в master, и в tk479) протокола нет: единственная
  копия `docs/research/P-40-convergence-statarb-lob.md` — в ветке `tk493-xdl9` (197 коммитов не в master). Отзывы П-40 в
  `reviews/` и данные `docs/findings/p40-*` в ствол пришли, сам протокол — нет. Исправить: слить tk493-xdl9 (или перенести
  протокол) либо в AGENTS.md указать ветку. Также: AGENTS.md «cargo запрещён хуком» — в `.claude/settings.json` такого хука
  нет (только `bare_systemd_run.py`), как и в master; не регрессия, но формулировку проверить.
- **З-3.** Отдельного файла приёмки Судьи для TK-482 после возврата `50c56171` (E-010) нет; тикет в архиве rpv —
  `status: done`. Исправление E-010 косвенно принято в TK-489 (`tk489-funding-remeasure-accept-2026-11-03.md`: digest
  funding-pp ≠ cross, тесты E-010 есть на `a62ef6eb`). Зелёный гейт на srv закрывает сборочную сторону.
- **З-4.** Номера `В-146…В-150` в `.claude/decisions.md` совпадают с номерами в `docs/plan/SETTLED.md` (другие решения, напр.
  SETTLED В-147 «сборки не на машине владельца» vs decisions В-147 «не ждать редких событий»); `decisions.md:573` ссылается на
  старый В-147. Нумерация реестра была с В-1 и до слияния — не дефект слияния, но «грепом по номеру» даёт два ответа.

## Что исправить

До слияния — ничего обязательного. После: З-2 (протокол П-40 в ствол или ссылка на ветку), З-1 по ходу уборки данных.
