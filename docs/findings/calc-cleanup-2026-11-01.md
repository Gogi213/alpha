# Чистка сервера счёта calc — TK-477 (G-020, В-84)

Судья, первое ноября. Сервер `89.163.242.211`, диск `/data` (sdb). Все замеры — юнитами на сервере (инвентарь `tk477-inv`,
содержимое `tk477-ls`, ссылки Летописи `tk477-ref2`, удаление `tk477-rmA`); машиночитаемый итог —
`docs/findings/tk477-calc-cleanup-2026-11-01.tsv`, сырой вывод — `data/tk477/{inv.raw,ls.tsv,rmA.tsv}`.

## Было / стало (`/data`, ГБ)

| число | что | файл:строка |
|---|---|---|
| 1362 | занято до | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:2` |
| 378 | свободно до | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:3` |
| 1277 | занято после | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:18` |
| 463 | свободно после | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:19` |
| 85.0 | удалено всего (сумма `du` перед `rm`; совпадает с разницей `df`) | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:20` |

## Удалено — класс А (выходы счёта закрытой линии стенок/скорости, тикеты приняты, регенерируемы скриптами из репо/тегов `archive/*`)

Явные пути, без шаблонов, симлинки пропускались; код возврата каждого `rm` — ноль (`data/tk477/rmA.tsv`). Ссылки Летописи
`/data/registry` на эти пути — только строки команд, шагов guard, опирающихся на них, нет.

| число (ГБ) | путь | файл:строка |
|---|---|---|
| 34.5 | `/data/tk0319/jrn` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:4` |
| 11.5 | `/data/p12r3a/jrn` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:5` |
| 3.1 | `/data/tk0289/sig` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:6` |
| 6.1 | `/data/tk0289/sig2` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:7` |
| 0.7 | `/data/tk0289/f2` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:8` |
| 8.0 | `/data/tk0315/p17f2` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:9` |
| 5.0 | `/data/tk200/grid` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:10` |
| 2.7 | `/data/tk051/abin-train` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:11` |
| 2.4 | `/data/tk051/stand-out` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:12` |
| 2.1 | `/data/tk048/abin-t46` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:13` |
| 3.0 | `/data/tk046/aug` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:14` |
| 2.8 | `/data/tk046/sep` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:15` |
| 1.9 | `/data/tk046/jul` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:16` |
| 1.2 | `/data/tk046/oct` | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:17` |

## Оставлено

- **Класс Г — под вопросом владельцу (хранится, пока владелец не скажет):**

| число (ГБ) | путь | почему вопрос | файл:строка |
|---|---|---|---|
| 14.4 | `/data/tk0364` | промежуточное линии DEX↔CEX (на холде) | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:21` |
| 6.7 | `/data/tk0399` | BBO Bybit по миллисекундам для арбитража Gate↔Bybit (арбитраж снят) | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:22` |
| 13.0 | `/data/tk0402` | арбитраж Gate↔Bybit на всех общих монетах (снят) | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:23` |
| 3.9 | `/data/tk084` | выход счёта клеток В-198 (линия стенок закрыта; сырьё докачки — проверить) | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:24` |

- **Класс В — не трогали (сырьё, единственные копии, опора живых выводов):** `/data/alpha` (бинлоги), `/srv/alpha-sda/*`
  (свечи, `binlog-spot*`, `tk305`, `tk366*`, `tk379-ob`), данные DEX (`tk0378`, `tk0380`, `tk0393`, `tk387`), закачка стакана
  `tk420`, `tk415`, `tk417`, `tk445`, корни `tk037`, Binance/смарт-мани `tk0451`, `tk0461`, `tk0471`, кубы G-017 `tk0440`,
  `tk0436` (линия пампов снова живая, В-82), БД `/data/g017`.
- **Класс Б (байтовые дубли):** пуст — подтверждённых байтовых копий среди крупных каталогов не найдено, удалять по этому
  классу нечего.
- `/data/runs`, `/data/sched/logs`, `/tmp` — вместе меньше гигабайта, чистка не окупается, оставлены.

## Проверка после

- `df` — выше; `alpha-sched`, `alpha-bus`, `alpha-launch`, юниты закачки TK-476 (`tk0s-tk476-by-*`, `tk476-mover`) — `running`.
- `/data/g017/g017.db` — `pragma quick_check` = `ok` (открыт только на чтение); `/data/alpha/epochs` на месте.
