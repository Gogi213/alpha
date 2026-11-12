# TK-516 — архив лога

Записи, перенесённые из `.claude/tickets/TK-516.md` (старые сверху, текст дословно).

### 2026-11-09T17:29:10+04:00 engineer
Договор. Сдам: ветка tk516-pq2bin (от tk515-l1, worktree .claude/worktrees/TK-516): pq2bin l1 быстрее >=2x на ядро: parse_f64 (Клингер m/10^k, m<=2^53, k<=22, иначе str::parse — бит в бит), Bybit без аллокаций на сообщение (fastjson по схеме, иначе serde_json) + сырой deflate через flate2/zlib-rs, Book::best() с qty за один поиск, прореживание куч.
Примут, если: (1) [тест: python tools/p40core/tk516_run.py test] unit parse_f64 == str::parse на 3 млн случайных + граничные (0, пусто, -0, 1., .5, e, >19 цифр), fastjson == serde_json на типовых и None на нетиповых; (2) [запуск: python tools/p40core/tk516_run.py ab] выходы l1 book (Binance, OKX) / bybit / gate и полные book/bybit/gate на DOGE 17.09 против замороженной базы TK-515 — diff -rq пусто; (3) [запуск: python tools/p40core/l1_gate_run.py] G-L1a/b зелёные на новом бинаре (P40_DIR=/root/p40core-tk516); (4) [запуск: python tools/p40core/tk516_run.py bench] МБ/с user-CPU на ядро: book и bybit >=2x базы; (5) [чтение кода] формат выхода и флаги не менялись.
Не входит: подмена бинаря в юнитах p40-l1-dl/p40-bg-dl/p40-box-conv (после приёмки Судьёй), PGO-сборка, слияние в master.
Где ещё живёт: потребители pq2bin — rg -n "pq2bin" tools/ ; на srv /root/p40-*.py; формат выхода не меняется. Граничные входы разбора числа — в тесте boundary_inputs (num.rs).
Алгоритм: узкое место по perf Исследователя (from_str 19 %, offset_buffer 12,6 %, HashMap 6 %, L1Enc 18 % | inflate 17,7 %, serde_json 25 %); варианты сравню замером user-CPU (srv загружен, load 24 — wall не годится).

### 2026-11-09T17:30:50+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all.done — Код @dec6a8b8 (parse_f64, fastjson+zlib-rs, best() с qty); srv: build→test→ab→bench в юните tk516-all, итог /root/tk516/final.txt. Дальше PGO, l1_gate_run.

### 2026-11-09T17:43:44+04:00 engineer
Замер 1 (@dec6a8b8): ab 264=264 различий 0; МБ/с на ядро book bn 28→27,7, okx 22→25, bybit 10,9→16,2 (1,5×), gate 12→11,5 — до 2× не дотянули; тест off_schema был ошибочным. perf: book — zstd-распаковка parquet 12 %, extend_from_dictionary 8 %, строковый разбор в замыкании 20 %; gate — read_line+from_utf8 16 %, inflate 18 %; bybit — from_utf8 14 %. Правка @следующий коммит: словарное чтение parquet (разбор по записи словаря), байтовый разбор gate/bybit без from_utf8. Замер 2 идёт, юнит tk516-all5 -> /root/tk516/final2.txt.

### 2026-11-09T17:44:43+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all2.done — Замер 1: ab 0 различий, но bybit 1,5×, book/gate ~1×. Правка e260c91c: словарное чтение parquet, байтовый разбор gate/bybit; сборка+замер 2 в tk516-all5.

### 2026-11-09T17:51:58+04:00 engineer
[итог: continue] — ab 0 различий, тесты 20/20; МБ/с base→new: bn 37→33 (хуже), okx 22→27, bybit 10,5→16, gate 12→13. До 2× не дошли; дальше perf bn/gate

### 2026-11-09T18:08:02+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all4.done — instructions:u base→new: bn 19,3→18,0 G, okx 14,6→12,0, bybit 14,7→10,5, gate 17,4→16,4; 2× нет. a7d71500 (словарь по page_encoding_stats), ждём ab/ins

### 2026-11-09T18:11:07+04:00 engineer
[итог: done] результат: C:/visual projects/alpha/.claude/worktrees/TK-516/docs/findings/p40-pq2bin-speed-tk516-2026-11-09.md — ab 264=264 различий 0, тесты 20/20; instr:u base→new bn 1,07× okx 1,44× bybit 1,40× gate 1,07×; 2× нет, развилка — P40_ZSTD/Book

### 2026-11-09T18:11:23+04:00 engineer
Итог: ветка tk516-pq2bin @b75057e1 (код a7d71500). Тесты 20/20; ab 264=264, 0 различий. Алгоритм: см. docs/findings/p40-pq2bin-speed-tk516-2026-11-09.md. Осталось: G-L1a/b на новом бинаре, решение Судьи по P40_ZSTD<3 и переписи Book; подмена бинаря в юнитах и слияние не делались.

### 2026-11-09T18:13:42+04:00 judge
[итог: return] проверка: C:/visual projects/alpha/.claude/worktrees/TK-493/docs/research/reviews/tk516-pq2bin-speed-return-2026-11-09.md — 2× нет (1,07–1,44×), G-L1a/b не прогнан. Решено: P40_ZSTD<3 можно (гейт после распаковки, рост ≤15 %), Book переписать; мерка cycles:u; иначе пересмотр п.4 по Амдалу

### 2026-11-09T18:24:51+04:00 engineer
Книга на отсортированных Vec @1b6f5ca2 (ветка tk516-pq2bin): тесты 21/21, ab 264=264 различий 0. cycles:u base→new: bn 11,0→15,0 (хуже +25 %), okx 8,9→8,5, bybit 8,6→6,5, gate 10,9→9,6. instr не видит: bn — memmove глубокой книги/снимков, L1Enc::row 16 %. Дальше: пакетная загрузка снимка/гибрид, потом zl (уровни zstd), G-L1a/b.

### 2026-11-09T18:24:52+04:00 engineer
[итог: continue] — Book на Vec: ab 0 различий, тесты 21/21; cycles bybit −25 %, okx/gate −5…−12 %, bn +25 % (memmove). Дальше пакетный снимок, zl, G-L1a/b

### 2026-11-09T18:34:39+04:00 engineer
[итог: continue] — Пакетная загрузка снимка: ab 0 различий, тесты 22/22; bn cycles 13,3→15,7 не лечит (memmove глубоких). bybit 8,7→5,6. Дальше BTreeMap/гибрид для bn, zl, G-L1a/b

### 2026-11-09T18:36:31+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all5.done — Book на BTreeMap @a3c1f647: build/test/ab/ins в юните tk516-all5 -> /root/tk516/final5.txt; дальше zl, G-L1a/b

### 2026-11-09T18:56:07+04:00 engineer
Итог @04b0f305 (ветка tk516-pq2bin). Судья 18:13: «2× нет (1,07–1,44×), G-L1a/b не прогнан. Решено: P40_ZSTD<3 можно (гейт после распаковки, рост ≤15 %), Book переписать; мерка cycles:u; иначе пересмотр п.4 по Амдалу». Сделано: Book на BTreeMap+пакетный снимок; главная находка bn — словарь arrow строился заново для quantity (19 % кадров), правило по размеру страницы словаря <64 КБ. cycles:u база→новый: bn 10,69→9,91 (1,08×), okx 8,47→7,87 (1,08×), bybit 8,20→5,80 (1,41×), gate 11,32→11,06 (1,02×); instr bn 19,3→16,4. Тесты 22/22; ab 264=264 различий 0; G-L1a rc=0, G-L1b rc=0 (P40_DIR=/root/p40core-tk516, иных классов 0). zl: zstd 2 ≤8 % cycles, размер +2 %; −3 +24 % размер — рычаг мал. Алгоритм: варианты — Vec/BTreeMap/хеш+кучи для Book, словарь по колонкам {-,все,event_type+side,+price,+quantity}, zstd −3/1/2/3; замер — cycles:u perf stat; выбран — BTreeMap + словарь только при странице <64 КБ, потому что bn/okx/bybit лучше или равны базе, md5 выхода одинаков. 2× нет: распаковка входа+сжим выхода+разбор ≈ 40–45 % кадров. Подмена бинаря в юнитах и слияние не делались. Отчёт docs/findings/p40-pq2bin-speed-tk516-2026-11-09.md (дополнение).

### 2026-11-09T18:56:08+04:00 engineer
[итог: done] результат: .claude/worktrees/TK-516/docs/findings/p40-pq2bin-speed-tk516-2026-11-09.md — 1,02–1,41× cycles:u, 2× нет (Амдал); ab 0 различий, тесты 22/22, G-L1a/b rc=0; словарь arrow по размеру страницы (bn −7 %) · остаток: решение Судьи по п.4 (2× недостижимо без смены байт/zstd); подмена бинаря в юнитах p40-l1-dl/p40-bg-dl/p40-box-conv, слияние tk516-pq2bin; PGO нового бинаря

### 2026-11-09T18:58:49+04:00 judge
[итог: return] проверка: C:/visual projects/alpha/.claude/worktrees/TK-493/docs/research/reviews/tk516-pq2bin-speed-return2-2026-11-09.md — 1,02–1,41× cycles; предел не доказан: разбор строк не неустраним, Амдал по числам сдачи 3,8–5×. Нужны: разбивка по корзинам, гибрид Book bn, gate разбор, zstd 2

### 2026-11-09T19:15:13+04:00 engineer
Круг по возврату Судьи 19:05 @895eb3e0: perf-разбивка по корзинам (docs/findings/p40-pq2bin-speed-tk516-2026-11-09.md, Дополнение 2); Book = хеш + top-32 (set_side был 18% bn/okx), ключи Bybit без повторного UTF-8. Медиана 3, cycles:u база→новый: bn 1,11x okx 1,15x bybit 1,42x gate 1,14x; instr 1,35/1,42/1,49/1,06x. Тесты 23/23, ab 264=264 различий 0. 2x нет; остаток вне распаковки/сжима 67-79%. Следующий круг: разбор строк на месте (gate/bybit), zstd 2 по умолчанию, G-L1a/b, PGO.

### 2026-11-09T19:15:14+04:00 engineer
[итог: continue] — @895eb3e0: Book хеш+top32, bybit без UTF-8; медиана3 cycles bn1,11 okx1,15 bybit1,42 gate1,14x; ab 0 разл., тесты 23/23; 2x нет. Дальше: разбор строк gate/bybit на месте, zstd2, G-L1a/b

### 2026-11-09T19:18:34+04:00 engineer
Разбор строк на месте (num::each_line, fill_buf+memchr) в gate и bybit, без read_until-копии; тест each_line==read_until на границах буфера. Ветка tk516-pq2bin (не закоммичено до ab). srv: юнит tk516-all6 (build,test,ab,ins) -> /root/tk516/final6.txt. Дальше: zstd 2 по умолчанию, G-L1a/b, PGO.

### 2026-11-09T19:32:24+04:00 engineer
[итог: done] результат: .claude/worktrees/TK-516/docs/findings/p40-pq2bin-speed-tk516-2026-11-09.md — @c355c8b3 тесты 24/24, ab 0 различий (zst равны после распаковки), G-L1a/b rc=0; cycles med3 bn1,35 okx1,24 bybit1,82 gate1,20x

### 2026-11-09T19:35:34+04:00 judge
[итог: return] проверка: C:/visual projects/alpha/.claude/worktrees/TK-493/docs/research/reviews/tk516-pq2bin-speed-return3-2026-11-09.md — 1,20–1,82× (bybit 1,82, прочие ≤1,35), остаток вне распаковки ~70 %. Нужны: taskset+медиана5, parquet-декод bn/okx, gate цикл/libdeflate, PGO

### 2026-11-09T19:46:01+04:00 engineer
Круг 4 (возврат 19:35) @4813a75e: добавлены шаги ins5 (taskset -c 7, base/new/pgo попеременно, медиана 5), pgo (PGO только pq2bin + сверка pgo=не-pgo после распаковки), bt (P40_BATCH 4096..262144 для bn/okx); gate — один поиск в хеше (entry) вместо get+insert/remove; P40_BATCH умолч. 65536 (как было). srv: юнит tk516-all10 (pgo, ins5, prof на @c355c8b3) -> /root/tk516/final10.txt; юнит tk516-all11 после all10 (build,test,ab,bt нового кода в /root/p40core-tk516b) -> final11.txt. libdeflate и свой декод parquet — не начаты.

### 2026-11-09T19:46:39+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all11.done — @4813a75e: шаги ins5/pgo/bt, gate entry, P40_BATCH; юниты all10 (pgo,ins5,prof), all11 (build,test,ab,bt) на srv; дальше медиана5, ab, решение

### 2026-11-09T19:53:50+04:00 ceo
CEO 19:35 (владелец недоволен скоростью: «я же сказал разобраться почему медленно и ускориться»): не копить круги. Текущую версию (Bybit 1,8–1,9×, остальные 1,2–1,4×) — сразу после G-L1a/b зелёного подменить в юнитах p40-l1-dl / p40-bg-dl / p40-box-conv (очередь по .ok цела), отчитаться приёмом МБ/с до/после. Параллельно следующий круг coder'ами: убрать построитель словаря arrow (19 % кадров bn — читать ByteArray без перевода в словарь), PGO (В-32), Book для bn. Каждая зелёная версия — сразу в бой.

### 2026-11-09T19:53:54+04:00 dispatcher
остановка CEO в 19:53: запущенной роли не было — статус применён.

### 2026-11-09T19:53:51+04:00 ceo
CEO: ускорение

### 2026-11-09T20:00:52+04:00 engineer
Подмена в бою 19:56 (CEO 19:53): PGO c355c8b3 (md5 7abb6321, /root/p40core-live/pq2bin) атомарно на путь юнитов p40-l1-dl/bg-dl/box-conv, без перезапуска, очередь по .ok цела. Гейт на PGO-бинаре: G-L1a 4/4, G-L1b иных 0, rc=0; pgo=не-pgo после распаковки 264/264. МБ/с на ядро (G-L1a, нагрузка srv ~9) до→после: bn 36,6→43,4, okx 22,1→35,3, bybit 15,7→23,2, gate 12,7→15,4. cycles:u медиана 5 (taskset): база→PGO bn 1,56x okx 1,48x bybit 1,80x gate 1,44x. +150 с: 848 новых .ok, zstd -t ок, новых fail нет. 4813a75e: тесты 24/24, ab 0 после распаковки, P40_BATCH в шуме (оставлен 65536); в бой не ставил. Отчёт Доп. 4 @092d1de0. Следующий круг: словарь arrow quantity bn/okx (19 % кадров), Book bn, PGO нового, libdeflate gate.

### 2026-11-09T20:00:52+04:00 engineer
[итог: continue] — PGO c355c8b3 в бою (G-L1a/b rc=0), cycles 1,44-1,80x; дальше: словарь arrow bn/okx, Book bn, PGO нового, libdeflate gate

### 2026-11-09T20:03:29+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all13.done — Круг 5 (не закоммичено): price/quantity plain как Utf8View без словаря arrow; юнит all13 (build,test,ab zstd3,ins5) -> final13.txt

### 2026-11-09T20:11:39+04:00 engineer
Круг 5 @f24cb9fa (Utf8View plain, ab 0 различий, тесты 24/24): ins5 без PGO base→new cycles med5 bn 1,28x okx 1,28x bybit 1,66x gate 1,37x (шум ±10 %, прежний c355c8b3: 1,35/1,24/1,82/1,20). Чтобы отделить вклад Utf8View — A/B того же бинаря P40_NOVIEW=1 vs нет (7 повторов, юнит tk516-all14 -> final14.txt).

### 2026-11-09T20:12:05+04:00 engineer
[итог: wait] ждём: host:srv:/root/tk516/all14.done — Круг 5 @f24cb9fa: Utf8View, ab 0 разл., тесты 24/24, cycles 1,28/1,28/1,66/1,37x; A/B NOVIEW идёт (all14), дальше PGO+гейт+подмена

### 2026-11-09T20:19:02+04:00 engineer
A/B Utf8View @f24cb9fa (cycles:u медиана 7, NOVIEW→view): bn 9,25→8,69 G (1,06x), okx 6,95→6,45 (1,08x), bybit/gate в шуме (4,80/4,92; 7,76/7,95) — вклад мал, но стабилен для bn/okx; оставлен включённым. Запущен юнит tk516-all15 (build,test,ab,pgo @f24cb9fa) -> /root/tk516/final15.txt; дальше гейт PGO-бинаря и подмена.
