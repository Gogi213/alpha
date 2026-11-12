# TK-528: удалены скачанные датасеты на srv и Storage Box (В-131, В-132)

Статус: **выполнено**, метки `TK528_DONE` и `ALL_DONE` в `/root/tk528` стоят с 12.11 08:26 GMT+4. Все числа взяты из файлов
`/root/tk528` и `df` (ГиБ = 2^30 байт; du/счётчики файлов сняты юнитами до удаления). Серверные скрипты не менялись.

## Юниты (`systemctl show -p ActiveState,Result`)

| юнит | что делал | итог |
|---|---|---|
| `tk528-del` (фаза 1) | сырьё srv + бокса (метка `P1_DONE`, 11.11 21:37) | остановлен судьёй на ожидании `D2NAIVEV_DONE` (бэктест остановлен CEO по слову владельца) |
| `tk528-p3` | фаза 3: L1 srv + `alpha/stakan/p40l1` на боксе | inactive, Result=success, status 0 |
| `tk528-p4` | фаза 4: копии сырья/L1 в `/root` и `/data` | inactive, Result=success, status 0 |
| `tk528-par-{aster,binance,bitget,bybit,gate,okx}` | параллельный `rm -r` по папке биржи на боксе (запуск координатора 05:30) | inactive, Result=success |
| `tk528-bn-00..05` | `rm -r` по символам внутри `binance_futures` (490 папок, запуск 08:18) | inactive, Result=success, в логах `done` |

## Что удалено

### srv 5.231.25.224 (из `removed-srv-p{1,3,4}.tsv`)

| фаза | каталоги | файлов | ГиБ |
|---|---|---|---|
| 1 | `/data/stakan` (сырьё Binance, 71 203 файла, 171,0 ГиБ), `/data/peek`, `/data/peek2`, `/data/p40d-stage`, `/data/p40rc-probe`, `/data/stage3`, `/data/gate`, `/data/gate3`, `/data/bybit`, `/data/bybit3`, пустые `/data/p40*-stage*`, `/data/tk520/DOGEUSDT_…_ob200.data.zip`, `/root/p522raw`, `/root/p40ref/raw` | 72 905 | 172,8 |
| 3 | `/data/p40l1/{binance,bitget,okx,hyperliquid,bybit,gate,aster}_futures` (bitget 59,8 ГиБ, okx 13,9), `/data/p40l1c` (33,6), `/data/p40hl` (4,9), `/data/p40l1-hl`, `/data/p40l1-fix`, `/data/p40l1-carry`, `/root/p40core/data` | 1 598 938 | 115,2 |
| 4 | `/root/p40/tmp` (990 parquet стакана), `/root/p40/smp`, `/root/tk515/src`, `/root/p40core-tk522/{l1stage,gl1a,gl1a_carry,gl1a_hyperliquid,gl1a_okx}`, `/data/tk520/out`, `/data/tk522/m`, `/data/p40-reap/ok` | 45 564 | 2,3 |
| итого | | 1 717 407 | 290,3 |

Списки удалённых файлов — `files-srv-p{1,3,4}.tsv.gz`.

### Storage Box u677479 (`removed-box-p{1,3}.tsv`, `boxrm-*.log`)

| фаза | каталоги | ГиБ (du до удаления) |
|---|---|---|
| 1 | `alpha/root` (49,6), `alpha/deep` (32,2), `alpha/stakan/chft` (560,0, вкл. mark/OI/liq — В-132), `alpha/stakan/bybit-linear-ob200`, `alpha/stakan/gate-futures`, файлы `*.sha256` | 641,8 |
| 3 | `alpha/stakan/p40l1` (L1: binance, bitget, okx, hyperliquid, bybit, gate, aster) | 253,9 |
| итого | | 895,7 |

Число файлов на боксе: листинг фазы 1 — `files-box-p1.ls.gz` (≈1,46 млн строк, включая каталоги), для фазы 3 листинг не
закончил и не использован — число файлов не измерено.

## Место до/после (счётчики `df`)

| машина | до (17:34/17:39 11.11) | после фазы 1 (21:37) | после всего (08:26 12.11) |
|---|---|---|---|
| srv `/` | занято 315,0 ГиБ (84 %) | 142,8 ГиБ (38 %) | **28,5 ГиБ (8 %)**, свободно 348,9 ГиБ |
| Box `/home` | 896,6 ГиБ (88 %) | 254,6 ГиБ (25 %) | **1,40 ГиБ (1 %)**, свободно 1022,6 ГиБ |

Разница srv «до − после» (286,5 ГиБ) меньше суммы удалённого (290,3): между снимками на `/` писали работающие коллекторы
(`/data/bnws`, `/data/bbws`, `/data/health` и др.).

## Метод и скорость на боксе

Урезанный shell бокса удаляет ~0,5 ГБ/мин одним `rm -r` (с 04:58 по 05:28 df: 263,4 → 252,9 ГБ, листинг `ls -R` перед
удалением шёл 40+ мин). Координатор запустил параллельные `rm -r` по биржам (05:30, `tk528-par-*`): темп 0,7–1,3 ГБ/мин
(05:46 — 240,1 ГБ → 07:45 — 146,0 ГБ), затем по символам binance (`tk528-bn-00..05`, 08:18). Бокс опустел к 08:26. Исходный
`rm -r` в `tk528-p3` поэтому поймал «нет такого файла» (rc=1 в `boxrm-p3.log`) — это ожидаемо, не ошибка удаления.

## Что оставлено

- Аналитика и выходы: `/root/p40core/out_d2naivev` (1,6 ГиБ, помечен `ОБОРВАН.txt` — бэктест остановлен CEO), `/root/p40charts`
  (51 МБ), `/root/p40prelim` (129 МБ), `docs/`, код и скрипты, `/data/tgt-plain` (cargo).
- Данные новых коллекторов и их проб: `/data/bnws*`, `/data/bbws*`, `/data/health*`, `*-ladder`, `/data/p40-reap`.
- `/data/p40l1`: остались только метки/служебные файлы (≈163 МБ, 68 имён: `*DONE`, `audit525_*.txt` и т. п.).
- Box: `alpha/collect` (1,27 ГБ, не в задаче) и пустое дерево каталогов `alpha/stakan/p40l1` (≈15 МБ, файлов нет).

## Ошибки удаления

Реальных ошибок нет: во всех `boxrm-*.log` (par-*, bn-part-*, p3) единственный тип сообщений — `rm: cannot remove …: No such
file or directory` (объект уже удалён параллельным юнитом; 526 426 строк в `boxrm-p3.log`, 225 280 binance, 266 989 okx,
32 793 aster, 0 bitget/bybit/gate/bn-02). Строки `rc=1` в `boxrm-p3.log` — тот же эффект. Все юниты — Result=success.
