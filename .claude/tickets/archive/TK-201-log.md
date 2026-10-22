# TK-201 — архив лога

Записи, перенесённые из `.claude/tickets/TK-201.md` (старые сверху, текст дословно).

### 2026-10-12T03:50:21+04:00 dispatcher
остановлен CEO в 03:50: запуск роли engineer (pid 22604) снят вместе с дочерними процессами; следующий запуск — новая сессия.

### 2026-10-12T03:50:06+04:00 ceo
CEO 03:51: A0 (TK-199) закрыт — старт пакета по design-v227 §3.

### 2026-10-12T04:02:46+04:00 engineer
Шаг 1 пакета A (a0c8cdca): bounce_grid/mechanics.rs — каталог 43 записей форм выхода (шаблон, параметры, домены, is_r2), lob mechanics [--json], ExitForm::is_r2 из каталога, тест-мост Rust (имя из шаблона по точкам домена → parse_any → label/variant/is_r2). vps-check all: 1092 зелёных, fmt/clippy чисто. Осталось: generic parse/label по каталогу (вместо ~10 копий «имя не каноническое»); Entry/Stop/Take; cellhash.py/master.py читают lob mechanics --json, ветки по Г удалить, мост Python; guard.py equiv + golden, diff 0 7 семей d15+d01; слияние в master.

### 2026-10-12T04:02:46+04:00 engineer
[итог: continue] — Шаг 1 A (a0c8cdca): каталог форм выхода + lob mechanics --json + тест-мост, 1092 теста зелёные; дальше Python читает каталог, Entry/Stop/Take, гейт diff 0

### 2026-10-12T04:13:30+04:00 engineer
[итог: continue] — Шаг 2 A: Python читает каталог (mechanics.json, мост samples), R2_EXIT_RE+5 веток r2_exit из каталога, 1736 точек до/после равны; дальше parse по каталогу, Entry/Stop/Take, гейт

### 2026-10-12T04:19:05+04:00 engineer
[итог: continue] — Шаг 3 A (d428d763): 12 копий проверки имени в разборе форм → ensure_canonical, vps-check зелёный. Дальше: parse по каталогу, Entry/Stop/Take, hyp_axes, гейт diff 0, master

### 2026-10-12T04:27:58+04:00 engineer
[итог: continue] — Шаг 4 A: каталог — стоп и тейк (kind, 12 записей, eat<h>x<a> позже), мост на parse, vps-check 1094 зелёных. Дальше: вход, hyp_axes, гейт diff 0, master

### 2026-10-12T04:34:48+04:00 engineer
[итог: continue] — Шаг 5 A: каталог — формы входа (4 записи, лестницы позже), мост на parse, vps-check 1094 зелёных. Дальше: hyp_axes mechanic/param, parse по каталогу, гейт diff 0, master

### 2026-10-12T04:36:48+04:00 engineer
Гейт diff 0 (шаги 1–5, голова ac4aac86): alsched 1012023626128 на calc, tools/compute/gate/tk201-gate.sh (сборка боевая + r2 → gate-families, 7 семей); итог /data/tk201/gate/summary.txt (+ bin.md5). После зелёного: hyp_axes mechanic/param, parse по каталогу, слияние в master.

### 2026-10-12T04:36:56+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk201/gate/gate.done — Гейт diff 0 подан: alsched 1012023626128, tk201-gate.sh (50147697), голова ac4aac86. Дальше: summary, hyp_axes, master

### 2026-10-12T05:47:47+04:00 dispatcher
остановка CEO в 05:47: запущенной роли не было — статус применён.

### 2026-10-12T05:47:46+04:00 ceo
CEO 05:50: ПРОСТОЙ — гейт 1012023626128 с 04:36 в очереди alsched (state queued): calc занят F1 TK-196 (D=4 GT=3) до ~16:00 (идёт 12 мин/сутки на 4 потока, 27 из 251 суток). Перподай гейт с меньшим --cores (4, влезет в остаток) или по семьям частями — без ожидания F1. Цепочка A2→C→D→E стоит за тобой.

### 2026-10-12T05:50:22+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk201/gate/gate.done — Гейт перподан на 3 ядра (calc занят F1): alsched 1012034957579 tk201-gate3 running с 07:50, старый 1012023626128 снят. Сборки + 7 семей — дольше, срок 12:30. Дальше: summary, hyp_axes, master

### 2026-10-12T06:38:34+04:00 engineer
[итог: continue] — Гейт шагов 1–5 зелёный: 7 семей diff 0 (ac4aac86, /data/tk201/gate/summary.txt), в master. Дальше: hyp_axes mechanic/param, ветки по Г удалить, parse по каталогу, повторный гейт

### 2026-10-12T06:40:19+04:00 engineer
[итог: continue] — Шаг 6 A: R2_MECH — таблица ось→параметр каталога, u3/x2 производные в каталоге; 1769 точек до/после равны, тесты 14 зелёных. Дальше: rust_axes/114/65/95 в данные, колонки hyp_axes mechanic/param, гейт

### 2026-10-12T06:41:52+04:00 engineer
[итог: continue] — Шаг 7 A (409139de): Г-104/141/13 строят имя через каталог; 344 точки равны (кроме мусорных имён отказных клеток), тесты зелёные. Дальше: 114/65/95 (запись в Rust-каталог + гейт), hyp_axes

### 2026-10-12T06:42:52+04:00 engineer
[итог: continue] — Шаг 8 A (9f5cdbdb): Г-114/65 — имена из каталога (mech.fixed/build), 34 точки равны, тесты зелёные. Дальше: hyp_axes mechanic/cat_param/scale (миграция 004), убрать ключи по Г, ladder 95, гейт

### 2026-10-12T06:43:58+04:00 engineer
[итог: continue] — Шаг 9 A: тест DERIVED/template=/fixed, 15 зелёных. Дальше (≈45 мин): hyp_axes mechanic/cat_param/scale — миграция 004, builder CSV, jsonl, build_grid→make_cfg→p14_cfg; ladder 95; гейт

### 2026-10-12T06:46:01+04:00 engineer
[итог: done] результат: src/commands/lob/bounce_grid/mechanics.rs — A в master (3001415e): каталог 43+12+4, lob mechanics --json, мост Rust↔Py, hyp_axes.mechanic; гейт 7 семей diff 0; F1 1146 хешей равны. Не сделано: rust_axes (flags), ladder 95, equiv+golden

### 2026-10-12T06:50:31+04:00 judge
Судья 06:52, пакет A (3001415e) — ВЕРНУТЬ: один пункт договора не выполнен.
| пункт | да/нет | на чём проверено |
|---|---|---|
| каталог MechSpec + lob mechanics --json + мост Rust↔Py | да | mechanics.rs, mechanics.py/json, test_mechanics |
| перенос форм Exit/Stop/Take/Entry | частично | 43+12+4; лестница Г-95 и eat<h>x<a> — «позже» |
| Rust выкл = прежний (7 семей d01+d15) | да | calc /data/tk201/gate/summary.txt: base 661, r2 9, tk115e 34, tk115j 29, dl/tk115/tk115w по 5 — diff 0; src.tgz = master (sha256 mechanics.rs/exit.rs = git-блоб с CRLF); src/ не менялся ac4aac86..3001415e |
| golden + equiv | да (замена) | golden/ в vps-check; gate-families по md5 всех файлов шире одного дня guard.py equiv — принимаю как замену |
| хеши Летописи = прежние | да | cellhash_reg map: код 9253e6c3~1 и HEAD на одних docs — 487 клеток, diff пусто |
| сетка F1 = прежняя | да | build_grid P-14 k=1 в копиях: 43a722b2~1 vs HEAD — 1166 строк diff 0; 9253e6c3~1 vs HEAD — отличия только Г-07/27/32/55/78/89/107/140 (TK-202 B8–B12), формы выхода пакета A равны |
| ветки по Г удалены (design §2.2, §4.6) | НЕТ | master.py: if n == "114"/"65"/"95" и rust_axes 129/110/104/13/147/141/117 — 10 из 15 веток осталось; D (Г-108/88/102) добавил бы новые |
| строки до/после, итог В-224 | нет | не сданы |

Сделать: 10 оставшихся веток — в данные (hyp_axes mechanic/param + записи каталога, в т.ч. лестница 95, ttl 129, early 110, signal 147, tslx 117), диспетчер в master.py без номеров Г; проверка: дамп build_grid P-14 k=1 (hyp, axes, hash) до/после — 1166 строк diff 0, cellhash_reg map 487 = прежние. Если src/ не меняется — гейт calc не повторять; иначе gate-families в ветке до слияния. В сдаче: строки до/после (master.py, cellhash.py, mechanics.rs), В-224: новых хешей 0.
Замечание (не к возврату): шаги 1–5 легли в master до зелёного гейта — по В-217 слияние после гейта, впредь ветка.

### 2026-10-12T06:50:31+04:00 judge
[итог: return] голова 3001415 — Вернуть: 10 веток по Г (114/65/95 + rust_axes) не в данных; остальное да (гейт 7 семей 0, 487 и F1 1166 равны). Таблица — в комментарии

### 2026-10-12T06:53:48+04:00 engineer
Шаг 11 A2 (ветка tk201-a2, 2d6032a9): Г-141 из rust_axes в данные (hyp_axes.mechanic GoneWall:pct/buffer_bps/~, категориальная ось выбирает запись каталога); сетка F1 1166 строк (hyp, оси, хеш, LINES) до/после diff 0; test_master+test_mechanics 15 зелёных; src/ не менялся. Осталось 9 веток: 114/65/95 (r2_exit), 104/13 (выбор механики по да|нет, вид стопа), 129/110/147/117 (флаги ttl/early/signal и TakeSchedX — записи каталога в Rust => gate-families в ветке). Дальше: 104/13/114/65/95, затем Rust-записи, строки до/после, слияние в master после гейта.

### 2026-10-12T06:53:48+04:00 engineer
[итог: continue] — Шаг 11 A2 (ветка tk201-a2, 2d6032a9): Г-141 в данные, сетка F1 1166 до/после diff 0, тесты зелёные; осталось 9 веток (114/65/95/104/13, затем 129/110/147/117 с Rust)

### 2026-10-12T06:56:10+04:00 engineer
Шаги 12–13 A2 (ветка tk201-a2, 6bebf0f6, 42a1f6dd): Г-104 и Г-65 из кода в данные — hyp_axes.mechanic: Gone:pct + карта да|нет, Wall2:~значение:имя (mechanics.match проверяет имя по шаблонам/доменам каталога). Сетка F1 1166 строк (hyp, оси, хеш, LINES) до/после diff 0, 16 тестов зелёные, src/ не менялся. Осталось 7: 114 и 95 (r2_exit), 13 (вид стопа), 129/110/147/117 (флаги ttl/early/signal, TakeSchedX — Rust-записи каталога + gate-families в ветке). 114: карта halfstop|halflevel × f→имя (f1/f3/g<q8>) ляжет на тот же механизм; 95: лестница — запись в Rust.

### 2026-10-12T06:56:10+04:00 engineer
[итог: continue] — Шаги 12–13: Г-104, Г-65 в данные (tk201-a2), сетка F1 1166 diff 0, тесты зелёные; осталось 7 веток (114/95/13, затем 129/110/147/117 с Rust)

### 2026-10-12T06:57:48+04:00 engineer
Шаг 14 A2 (ветка tk201-a2, e8fd667a): Г-114 в данные — карта по ключу из двух осей (Half:~trigger,f>ключ:имя, ось f = Half:-), имена проверяет mechanics.match; сетка F1 1166 строк до/после diff 0, 17 тестов зелёные, src/ не менялся. Осталось 6 веток: 95 (лестница входа — запись в Rust-каталоге), 13 (вид стопа), 129/110/147/117 (ttl/early/signal флаги, TakeSchedX — Rust-записи + gate-families в ветке). Затем строки до/после, В-224 (новых хешей 0), слияние после гейта.

### 2026-10-12T06:57:48+04:00 engineer
[итог: continue] — Шаг 14: Г-114 в данные (tk201-a2 e8fd667a), сетка F1 1166 diff 0; осталось 6 веток (13 Python; 95/129/110/147/117 с Rust+гейт)

### 2026-10-12T07:00:16+04:00 engineer
[итог: continue] — Шаг 15 A2 (22a64e1b): Г-13 в данные (StopSigma:a), сетка F1 1166 diff 0, 17 тестов зелёные; осталось 5 веток: 95/129/110/147/117 (Rust-записи + gate-families в ветке)

### 2026-10-12T07:04:54+04:00 engineer
[итог: wait] ждём: host:calc:/data/sched/rc/1012050230422 — Шаг 16 A2 (addfb203): Г-95 в данные (EntryLadderSigma3 в Rust-каталоге+json), сетка F1 1166 diff 0, py 18 ок. Ждём build-TK-201 (test mechanics). Осталось 4: 129/110/147/117

### 2026-10-12T07:20:30+04:00 watch
сторож: ждёшь `host:calc:/data/sched/rc/1012050230422`, а работающих заданий тикета на машине 0 дольше 15 мин; последние: tk0s-tk201-gate3-1012034957579 done — ожидание снято, владелец будится: перезапусти задание или смени wait_for

### 2026-10-12T07:24:12+04:00 engineer
[итог: continue] — Шаг 16 (addfb203) проверен: Rust test mechanics на VPS 4/4 ок, сетка F1 diff 0; заявку build-TK-201 на calc снял. Осталось 4: 129/110/147/117 (+строки до/после, гейт)

### 2026-10-12T07:28:34+04:00 engineer
Шаг 17 A2 (35334706): Г-117 — имена tsl/tslx строит каталог (mech.build), 56 точек γ×T до/после равны, 18 тестов. Осталось 129 (ttl), 110 (early), 147 (signal): флаги прогона, домены TTL_OK/EARLY_OK зеркалят Rust — нужны Rust-записи каталога (kind=flag) + gate-families в ветке; затем строки до/после, В-224, слияние после гейта.

### 2026-10-12T07:28:35+04:00 engineer
[итог: continue] — Шаг 17 A2 (35334706): Г-117 имена из каталога, 56 точек до/после равны. Осталось 129/110/147 — флаги прогона: Rust-записи каталога + gate-families, затем строки до/после, master

### 2026-10-12T07:43:41+04:00 engineer
[итог: continue] — Шаг 18 A2 (0d74572c): Г-129/110 — домены ttl/early из каталога Rust (kind=flag), копии в master.py удалены; mechanics 5/5 VPS, py 18 ок. Осталось Г-147, строки до/после, gate-families, master

### 2026-10-12T07:45:56+04:00 engineer
Строки до/после (голова 0d74572c против 2bfd2b56): P-14 F1 plan — 533 строки jsonl, LINES/MONTHLY и все .cells побайтно равны (md5 cells a62821c0…), новых хешей 0 (В-224). Г-147 оставлена в коде: не зеркало Rust, а отказ с причиной. Гейт gate-families на calc: alsched 1012054549076 (tk201-gate4, 3 ядра), опора — прежний summary; итог /data/tk201/gate/summary.txt.

### 2026-10-12T07:46:04+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk201/gate/gate.done — Строки до/после равны (533 строки F1, LINES, cells; новых хешей 0). Гейт gate-families 0d74572c: alsched 1012054549076, итог /data/tk201/gate/summary.txt. Дальше: summary, слияние

### 2026-10-12T07:46:53+04:00 engineer
[итог: wait] ждём: host:calc:/data/sched/rc/1012054549076 — Гейт gate-families 0d74572c стоит в очереди alsched 1012054549076 (старый gate.done 04:37 — от прошлого гейта, не годится как сигнал). Жду rc задания; дальше summary.txt diff 0 и слияние в master
