# Чистка сервера счёта calc — TK-477 (G-020, В-84)

Судья, первое ноября. Сервер `89.163.242.211`, диск `/data` (sdb). Все замеры — юнитами на сервере (инвентарь `tk477-inv`,
содержимое `tk477-ls`, ссылки Летописи `tk477-ref2`, удаление `tk477-rmA`); машиночитаемый итог —
`docs/findings/tk477-calc-cleanup-2026-11-01.tsv`, сырой вывод — `data/tk477/{inv.raw,ls.tsv,rmA.tsv}`.

## Было / стало (`/data`, ГБ)

| число | файл:строка | что |
| --- | --- | --- |
| 1362 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:2` | занято до |
| 378 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:3` | свободно до |
| 1277 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:18` | занято после |
| 463 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:19` | свободно после |
| 85.0 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:20` | удалено всего (сумма `du` перед `rm`; совпадает с разницей `df`) |

## Удалено — класс А (выходы счёта закрытой линии стенок/скорости, тикеты приняты, регенерируемы скриптами из репо/тегов `archive/*`)

Явные пути, без шаблонов, симлинки пропускались; код возврата каждого `rm` — ноль (`data/tk477/rmA.tsv`). Ссылки Летописи
`/data/registry` на эти пути — только строки команд, шагов guard, опирающихся на них, нет.

| число (ГБ) | файл:строка | путь |
| --- | --- | --- |
| 34.5 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:4` | `/data/tk0319/jrn` |
| 11.5 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:5` | `/data/p12r3a/jrn` |
| 3.1 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:6` | `/data/tk0289/sig` |
| 6.1 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:7` | `/data/tk0289/sig2` |
| 0.7 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:8` | `/data/tk0289/f2` |
| 8.0 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:9` | `/data/tk0315/p17f2` |
| 5.0 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:10` | `/data/tk200/grid` |
| 2.7 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:11` | `/data/tk051/abin-train` |
| 2.4 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:12` | `/data/tk051/stand-out` |
| 2.1 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:13` | `/data/tk048/abin-t46` |
| 3.0 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:14` | `/data/tk046/aug` |
| 2.8 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:15` | `/data/tk046/sep` |
| 1.9 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:16` | `/data/tk046/jul` |
| 1.2 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:17` | `/data/tk046/oct` |

## Оставлено

- **Класс Г — под вопросом владельцу (хранится, пока владелец не скажет):**

| число (ГБ) | файл:строка | путь | почему вопрос |
| --- | --- | --- | --- |
| 14.4 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:21` | `/data/tk0364` | промежуточное линии DEX↔CEX (на холде) |
| 6.7 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:22` | `/data/tk0399` | BBO Bybit по миллисекундам для арбитража Gate↔Bybit (арбитраж снят) |
| 13.0 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:23` | `/data/tk0402` | арбитраж Gate↔Bybit на всех общих монетах (снят) |
| 3.9 | `docs/findings/tk477-calc-cleanup-2026-11-01.tsv:24` | `/data/tk084` | выход счёта клеток В-198 (линия стенок закрыта; сырьё докачки — проверить) |

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
