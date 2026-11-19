# TK-627 — журнал уборки, фаза 2: ветки git, worktree, треды bb

Дата: 2026-11-20 (GMT+4). Исполнитель — тред `thr_9cnuxqpmm5`, окружение `env_225wzqkjxa`. Основание: опись `docs/plan/TK-627-cleanup-inventory.md`, слова владельца: «мне надо сильно почистить память проекта и привести в порядок» / «и весь проект в целом».

## Итог

| Что | Число |
|---|---|
| Ветки класса A проверены повторно (`git merge-base --is-ancestor`) | 126 из 126 (слиты в master: 82, строгие предки несмёрженной ветки: 44) |
| Новых тегов `archive/<ветка>` создано и отправлено в origin | 110 (ещё 16 тегов уже стояли на кончиках; на origin все 126 проверены по SHA) |
| Локальных веток удалено | 126 |
| Веток на origin удалено | 24 |
| Окружений bb, треды в архив (`bb environment archive-threads`) | 86 окружений, 98 тредов |
| Worktree без окружения bb снято `git worktree remove` | 2 |
| Записей worktree в git: было → стало | 105 → 17 к концу уборки (включая основной каталог; потом тредами TK-628/TK-629 добавлено ещё 2) |
| Локальных веток: было → стало | 153 → 28 к концу уборки (24 несмёрженных листа, включая `g022-prep-3` и `okx-ws-prep`; `master`; 3 удержанные — `tk-595-…-thr_bkekr49hnm` и две ветки этого и инвентаризационного тредов; дальше другие треды (TK-628, TK-629…) создают новые ветки, в счёт не входят) |
| Теги `archive/*` | 233 → 344 (+110 мои, +1 тег TK-624 координатора); ни один не удалён и не сдвинут |

## Не тронуто

- 24 несмёрженных листа (код коллекторов G-022 и стопки правок) и `g022-prep-3`, `okx-ws-prep`; их worktree и треды.
- Ветка `tk-627-project-cleanup-inventory-v1-thr_vekyqfkx7f` (тред `thr_vekyqfkx7f`, `env_ivwwmzaw2k`), ветка этого треда (`thr_9cnuxqpmm5`, `env_225wzqkjxa`), координатор `thr_ty6ae9r46y` (`env_befckaszm7`, каталог проекта).
- `docs/`, память, приказы/решения/AGENTS.md, srv, Storage Box, сырые данные.
- Ветка `tk-595-p42-retitration-v2-thr_bkekr49hnm` (tip `faa15327c`, слита в master), тред `thr_bkekr49hnm`, `env_3ges9yxf36`: в worktree **незакоммиченные изменения отслеживаемых файлов** (`docs/findings/p42-bookdepth-g1-2026-11-18.md`, `tools/p42/bookdepth_gate_g1.py`) и скрипты/выводы в `data/` (106 КБ). Удержана целиком — нужен ответ, что с ними.
- Решение по TK-624: слита в master `271aab9fa` координатором; по его слову (сообщение 20.11) архивированы `thr_kq9w7za6pc`, `thr_intmscd9df`, `thr_f85kyyjc5f`, `thr_c9wp2z322n`, `thr_3z63t77bgz`, `thr_usjcn3925x`, `thr_28tg85dpwp`.

## Сохранённые файлы из worktree (не потеряно ничего игнорируемого)

Перед снятием worktree проверено `git status --porcelain --ignored` у всех 89. Кроме `__pycache__`/`.pytest_cache`, в 14 worktree были игнорируемые файлы `data/` (скрипты, логи, промежуточные выводы, 41 МБ всего). Скопированы с проверкой `diff -rq` (без расхождений) в `data/worktree-saved-2026-11-20/<тред>-1/data/…` основного каталога (`data/` в gitignore, каталог не в git). Два неотслеживаемых `tools/srv-wait.sh` (`thr_49wt62v3p2`, `thr_rnebp6ffzy`) побайтно равны версии в master.

## Оставшиеся пустые каталоги (bb не смог удалить — EBUSY)

Окружения в `teardown: failed`, каталог `…\worktrees\<тред>-1\alpha` пуст (0 байт), записи в git нет (`git worktree prune` выполнен), `rmdir` тоже отвечает «Device or resource busy» — держит процесс. Ничего не удалено вручную.

| Каталог | Размер | Окружение bb | Ветка |
|---|---|---|---|
| `thr_3bjhhskuys-1/alpha` | 0 | env_ucaq5fdzu9 | tk-534-arb-holding-v4-thr_3bjhhskuys |
| `thr_5uavq9hdb5-1/alpha` | 0 | env_wsiyd79sa4 | g-022-arb-dense-v3-thr_5uavq9hdb5 |
| `thr_agv5sma6yj-1/alpha` | 0 | env_tgyfhf2e4t | tk-599-p42-retitration-full-v1-thr_agv5sma6yj |
| `thr_ba5pwg96p8-1/alpha` | 0 | env_zsrh6ggf77 | tk-534-arb-holding-v5-thr_ba5pwg96p8 |
| `thr_ed6wtxjeet-1/alpha` | 0 | env_p3kbqktzc7 | tk-581-srv-incident-2106-v1-thr_ed6wtxjeet |
| `thr_f85kyyjc5f-1/alpha` | 0 | env_uvi5rpzatw | tk-624-p42-page-where-we-are-v1-thr_f85kyyjc5f |
| `thr_intmscd9df-1/alpha` | 0 | env_pajn4a8uxi | tk-624-p42-page-where-we-are-v1-thr_intmscd9df |
| `thr_mbbbvmacm7-1/alpha` | 0 | env_7eqpt9ewax | tk-588-p42-signal-features-v1-thr_mbbbvmacm7 |
| `thr_qan9vx925u-1/alpha` | 0 | env_tqkg3tpkmp | g-022-evaa-wave-v1-thr_qan9vx925u |
| `thr_vy6t5dkf2z-1/alpha` | 0 | env_37i59zvgd8 | tk-585-p42-entry-by-book-v1-thr_vy6t5dkf2z |

Из них 8 упали ещё до этой уборки (попытки 5, «Needs recovery»), 2 — тред-окружения TK-624 после архивации (`env_pajn4a8uxi`, `env_uvi5rpzatw`). Восстановление: найти держащий процесс, затем `bb environment cleanup <env> --recover`.

## Ветки класса A: 126, по одной

Для каждой: повторная проверка, тег `archive/<ветка>` на origin (SHA сверен), удаление локальной ветки (`git branch -D`; у слитых ветка предок master, у предков — потомок указан), удаление origin-ветки при совпадении кончика. Колонки: класс (M слита, A предок), ветка без `bb/`, SHA, основание, тег, локальная, origin, окружение bb, треды окружения.

| кл | ветка | SHA | основание | тег | локальная | origin | env | треды |
|---|---|---|---|---|---|---|---|---|
| M | 42-breakout-run-v1-thr_hbcbi5s4rv | 52fdc57cc | слита в master | archive/bb/42-breakout-run-v1-thr_hbcbi5s4rv (новый) | удалена | — | — | — |
| M | 42-fresh-level-v1-thr_c964v74are | a5b509e8e | слита в master | archive/bb/42-fresh-level-v1-thr_c964v74are (новый) | удалена | удалена | — | — |
| M | 42-pb-next-minute-v1-thr_kvrthfukam | 575ea7824 | слита в master | archive/bb/42-pb-next-minute-v1-thr_kvrthfukam (новый) | удалена | — | — | — |
| M | 42-report-fix-v1-thr_x339cs9jz3 | b70069d7e | слита в master | archive/bb/42-report-fix-v1-thr_x339cs9jz3 (новый) | удалена | удалена | — | — |
| M | 42-rerun-12m-ticks-v1-thr_fefbsq7ncj | e0539507a | слита в master | archive/bb/42-rerun-12m-ticks-v1-thr_fefbsq7ncj (новый) | удалена | — | — | — |
| M | 42-stats-outsiders-v1-thr_ewewq28522 | 4abcc2d6f | слита в master | archive/bb/42-stats-outsiders-v1-thr_ewewq28522 (новый) | удалена | удалена | — | — |
| M | 42-take-grid-v1-thr_c964v74are | 280d21944 | слита в master | archive/bb/42-take-grid-v1-thr_c964v74are (новый) | удалена | удалена | — | — |
| M | 42-trade-charts-v1-thr_qnqvwwb3dx | 35f91fcdd | слита в master | archive/bb/42-trade-charts-v1-thr_qnqvwwb3dx (новый) | удалена | удалена | — | — |
| M | 42-v2-finish-v1-thr_7cy3ypv2kx | 8c8a34872 | слита в master | archive/bb/42-v2-finish-v1-thr_7cy3ypv2kx (новый) | удалена | удалена | — | — |
| M | g-022-arb-cross-exit-v1-thr_4w7rkbygc9 | cf78a7f91 | слита в master | archive/bb/g-022-arb-cross-exit-v1-thr_4w7rkbygc9 (новый) | удалена | удалена | env_52y6nkxp62 | thr_4w7rkbygc9 |
| M | g-022-arb-cross-exit-v1-thr_wpirpczpcx | b03c02e8c | слита в master | archive/bb/g-022-arb-cross-exit-v1-thr_wpirpczpcx (новый) | удалена | — | env_5ms5ewruyh | thr_wpirpczpcx |
| M | g-022-arb-holding-v1-thr_f3hh3ufbyk | 5b1f99621 | слита в master | archive/bb/g-022-arb-holding-v1-thr_f3hh3ufbyk (новый) | удалена | — | env_t9vbvgr5vc | thr_f3hh3ufbyk |
| M | g-022-arb-tab-v1-thr_ucmyezaicu | f52b43bf6 | слита в master | archive/bb/g-022-arb-tab-v1-thr_ucmyezaicu (новый) | удалена | — | — | — |
| M | g-022-arb-tokyo-latency-v1-thr_h7jt9ii889 | 2242bdfb4 | слита в master | archive/bb/g-022-arb-tokyo-latency-v1-thr_h7jt9ii889 (новый) | удалена | удалена | env_7gtp5ab5ga | thr_h7jt9ii889 |
| M | g-022-arb-tokyo-latency-v1-thr_xvgmkxqfk3 | b62069c2a | слита в master | archive/bb/g-022-arb-tokyo-latency-v1-thr_xvgmkxqfk3 (новый) | удалена | удалена | env_68dzfrg88b | thr_xvgmkxqfk3 |
| M | g-022-independent-client-ab-v1-thr_6kk25ds92h | 2b01df2d8 | слита в master | archive/bb/g-022-independent-client-ab-v1-thr_6kk25ds92h (новый) | удалена | — | env_my69gghghj | thr_6kk25ds92h |
| M | g-022-prep-bitget-htx-bingx-v1-thr_g6w5izsjsn | d3809b197 | слита в master | archive/bb/g-022-prep-bitget-htx-bingx-v1-thr_g6w5izsjsn (новый) | удалена | — | — | — |
| M | master-thr_idcqtsjp6v | 18a9100bb | слита в master | archive/bb/master-thr_idcqtsjp6v (новый) | удалена | — | — | — |
| M | none-klines-1m-v1-thr_546wag5yes | f6cd3a61a | слита в master | archive/bb/none-klines-1m-v1-thr_546wag5yes (новый) | удалена | — | — | — |
| M | tk-528-thr_3mwjykp458 | 475b7f643 | слита в master | archive/tools-compute-2026-11 (был) | удалена | — | — | — |
| M | tk-534-arb-holding-v1-thr_3krp5qxkr5 | c9b585b7d | слита в master | archive/bb/tk-534-arb-holding-v1-thr_3krp5qxkr5 (новый) | удалена | удалена | env_kkwhez8jhk | thr_3krp5qxkr5 |
| M | tk-534-arb-holding-v2-thr_gm5dzhs7ur | 8e80e7949 | слита в master | archive/bb/tk-534-arb-holding-v2-thr_gm5dzhs7ur (новый) | удалена | — | env_mx8mphaxs5 | thr_gm5dzhs7ur |
| M | tk-534-arb-holding-v2-thr_jhixmpakyz | 97e6131fe | слита в master | archive/bb/tk-534-arb-holding-v2-thr_jhixmpakyz (новый) | удалена | — | env_d93misyrup | thr_jhixmpakyz |
| M | tk-534-arb-holding-v3-thr_ymhiqc62pu | b826ce976 | слита в master | archive/bb/tk-534-arb-holding-v3-thr_ymhiqc62pu (новый) | удалена | — | env_rufwm8te2i | thr_ymhiqc62pu |
| M | tk-534-arb-holding-v4-thr_3bjhhskuys | 781458b24 | слита в master | archive/bb/tk-534-arb-holding-v4-thr_3bjhhskuys (новый) | удалена | — | env_ucaq5fdzu9 | thr_3bjhhskuys |
| M | tk-534-arb-holding-v5-thr_ba5pwg96p8 | 098b6673c | слита в master | archive/bb/tk-534-arb-holding-v5-thr_ba5pwg96p8 (новый) | удалена | — | env_zsrh6ggf77 | thr_ba5pwg96p8 |
| M | tk-534-arb-holding-v6-thr_5f8pj62h9w | 4b8cf7770 | слита в master | archive/bb/tk-534-arb-holding-v6-thr_5f8pj62h9w (новый) | удалена | — | env_qqm3y27wpi | thr_5f8pj62h9w |
| M | tk-534-arb-holding-v7-thr_ghg8fwbtub | ef83da151 | слита в master | archive/bb/tk-534-arb-holding-v7-thr_ghg8fwbtub (новый) | удалена | — | env_bfy9sirevg | thr_ghg8fwbtub |
| M | tk-534-stay-protocol-v1-thr_nj8qssrw58 | 62fd10e95 | слита в master | archive/bb/tk-534-stay-protocol-v1-thr_nj8qssrw58 (новый) | удалена | — | env_4g6wymrexe | thr_nj8qssrw58 |
| M | tk-535-independent-client-ab-v1-thr_hb7e5fjsf4 | d0a5de899 | слита в master | archive/bb/tk-535-independent-client-ab-v1-thr_hb7e5fjsf4 (новый) | удалена | — | env_bkwvqtjem9 | thr_hb7e5fjsf4 |
| M | tk-535-independent-client-ab-v2-thr_eys7bmufjm | fd7263169 | слита в master | archive/bb/tk-535-independent-client-ab-v2-thr_eys7bmufjm (новый) | удалена | — | env_r4cugh5kba | thr_eys7bmufjm |
| M | tk-585-p42-entry-by-book-v1-thr_hajy8aytkf | 5df607323 | слита в master | archive/bb/tk-585-p42-entry-by-book-v1-thr_hajy8aytkf (новый) | удалена | — | env_pn7845zk5h | thr_hajy8aytkf |
| M | tk-585-p42-entry-by-book-v1-thr_vy6t5dkf2z | 26471237d | слита в master | archive/bb/tk-585-p42-entry-by-book-v1-thr_vy6t5dkf2z (новый) | удалена | — | env_37i59zvgd8 | thr_vy6t5dkf2z |
| M | tk-585-p42-entry-by-book-v2-thr_49wt62v3p2 | 9abd06ab4 | слита в master | archive/bb/tk-585-p42-entry-by-book-v2-thr_49wt62v3p2 (новый) | удалена | — | env_migci28eib | thr_49wt62v3p2 |
| M | tk-585-p42-entry-by-book-v3-thr_tbynwqnkd9 | c3bab9cb1 | слита в master | archive/bb/tk-585-p42-entry-by-book-v3-thr_tbynwqnkd9 (новый) | удалена | — | env_8ipgbtggc9 | thr_tbynwqnkd9 |
| M | tk-586-srv-disk-v1-thr_r7ahw3mipj | 03a5925ed | слита в master | archive/bb/tk-586-srv-disk-v1-thr_r7ahw3mipj (новый) | удалена | — | env_afdnahpc26 | thr_r7ahw3mipj |
| M | tk-587-p42-level-cases-v1-thr_rxr42g43y2 | bd1ccafe6 | слита в master | archive/bb/tk-587-p42-level-cases-v1-thr_rxr42g43y2 (новый) | удалена | — | env_ee8ffwa87x | thr_rxr42g43y2 |
| M | tk-588-p42-signal-features-v1-thr_mbbbvmacm7 | d0c5e4f01 | слита в master | archive/bb/tk-588-p42-signal-features-v1-thr_mbbbvmacm7 (новый) | удалена | — | env_7eqpt9ewax | thr_mbbbvmacm7 |
| M | tk-588-p42-signal-features-v1-thr_uzmhnjsich | 94d261dc8 | слита в master | archive/bb/tk-588-p42-signal-features-v1-thr_uzmhnjsich (новый) | удалена | — | env_aw6vdrudb4 | thr_uzmhnjsich |
| M | tk-588-p42-signal-features-v2-thr_7yqvs7xzz9 | 3d3a548a8 | слита в master | archive/bb/tk-588-p42-signal-features-v2-thr_7yqvs7xzz9 (новый) | удалена | — | env_h5zeijhmzg | thr_7yqvs7xzz9 |
| M | tk-589-p42-charts-fix-v1-thr_ngbxsw5smv | fd95583f5 | слита в master | archive/bb/tk-589-p42-charts-fix-v1-thr_ngbxsw5smv (новый) | удалена | — | env_jw28gheqn5 | thr_ngbxsw5smv |
| M | tk-590-p42-features-method-critic-v1-thr_u4qyqtpqgj | 27956bb09 | слита в master | archive/bb/tk-590-p42-features-method-critic-v1-thr_u4qyqtpqgj (новый) | удалена | — | env_zjxtzw2js9 | thr_u4qyqtpqgj |
| M | tk-591-p42-stops-exits-v1-thr_dpx3s74v6t | 748581404 | слита в master | archive/bb/tk-591-p42-stops-exits-v1-thr_dpx3s74v6t (новый) | удалена | — | env_7at287gtc9 | thr_dpx3s74v6t |
| M | tk-591-p42-stops-exits-v2-thr_aimtwtccyw | 6232a3147 | слита в master | archive/bb/tk-591-p42-stops-exits-v2-thr_aimtwtccyw (новый) | удалена | — | env_qck9avadk2 | thr_aimtwtccyw |
| M | tk-591-p42-stops-exits-v3-thr_g2k7tqsj4a | 28125f771 | слита в master | archive/tk-591-p42-stops-exits-v3-thr_g2k7tqsj4a (был) | удалена | удалена | env_uv7jd37hvg | thr_g2k7tqsj4a |
| M | tk-591-p42-stops-exits-v3-thr_h8tne77hms | 8c5ac8fca | слита в master | archive/bb/tk-591-p42-stops-exits-v3-thr_h8tne77hms (новый) | удалена | — | env_ybh8yiaw2p | thr_h8tne77hms |
| M | tk-592-p42-pairs-core-catboost-v1-thr_h3gvzizazc | 8d5c5b1e4 | слита в master | archive/bb/tk-592-p42-pairs-core-catboost-v1-thr_h3gvzizazc (новый) | удалена | — | env_3icpfpwfdk | thr_h3gvzizazc |
| M | tk-592-p42-pairs-core-catboost-v2-thr_8fe3dw3a9t | 8d5c5b1e4 | слита в master | archive/bb/tk-592-p42-pairs-core-catboost-v2-thr_8fe3dw3a9t (новый) | удалена | — | env_shmeuntwcf | thr_8fe3dw3a9t |
| M | tk-592-p42-pairs-core-catboost-v2-thr_z7mtq7uwbm | eaa450bcb | слита в master | archive/bb/tk-592-p42-pairs-core-catboost-v2-thr_z7mtq7uwbm (новый) | удалена | удалена | env_ewq99ayhjs | thr_z7mtq7uwbm |
| M | tk-592-p42-pairs-core-catboost-v3-thr_bjis8v5kfi | f2a50280e | слита в master | archive/tk-592-p42-pairs-core-catboost-v3-thr_bjis8v5kfi (был) | удалена | удалена | env_8bxi9vpkbk | thr_bjis8v5kfi |
| M | tk-594-p42-flow-deriv-features-v1-thr_gbepkykk5x | 0b9d0e895 | слита в master | archive/bb/tk-594-p42-flow-deriv-features-v1-thr_gbepkykk5x (новый) | удалена | — | env_g872cgehzh | thr_gbepkykk5x |
| M | tk-594-p42-flow-deriv-features-v2-thr_kwcv3dujnd | 755d6e90b | слита в master | archive/tk-594-p42-flow-deriv-features-v2-thr_kwcv3dujnd (был) | удалена | удалена | env_nd4ftufzxj | thr_kwcv3dujnd |
| M | tk-595-p42-retitration-v1-thr_2vu5n3p62e | 6b64add98 | слита в master | archive/bb/tk-595-p42-retitration-v1-thr_2vu5n3p62e (новый) | удалена | — | env_xeyj3in4qx | thr_2vu5n3p62e |
| M | tk-595-p42-retitration-v1-thr_7ijaib76qx | 8c5ac8fca | слита в master | archive/bb/tk-595-p42-retitration-v1-thr_7ijaib76qx (новый) | удалена | — | env_naagdwtxzt | thr_7ijaib76qx |
| M | tk-595-p42-retitration-v3-thr_cefmq8q2vb | 08fc1518d | слита в master | archive/tk-595-p42-retitration-v3-thr_cefmq8q2vb (был) | удалена | удалена | env_yfijqgyxt7 | thr_cefmq8q2vb |
| M | tk-596-p42-es15-confirm-v1-thr_nt7x2wcswt | 65e8c26c7 | слита в master | archive/tk-596-p42-es15-confirm-v1-thr_nt7x2wcswt (был) | удалена | — | env_su9hsmn9ur | thr_nt7x2wcswt |
| M | tk-597-p42-merge-master-v1-thr_djrs57pkez | 1410d8027 | слита в master | archive/bb/tk-597-p42-merge-master-v1-thr_djrs57pkez (новый) | удалена | — | env_63nbyjwjex | thr_djrs57pkez |
| M | tk-599-p42-retitration-full-plan-v1-thr_hzimsce4kp | 131be896a | слита в master | archive/bb/tk-599-p42-retitration-full-plan-v1-thr_hzimsce4kp (новый) | удалена | — | env_erszpx9kqg | thr_hzimsce4kp |
| M | tk-599-p42-retitration-full-v1-thr_agv5sma6yj | 2044d03c7 | слита в master | archive/bb/tk-599-p42-retitration-full-v1-thr_agv5sma6yj (новый) | удалена | — | env_tgyfhf2e4t | thr_agv5sma6yj |
| M | tk-609-rust-max-v6-thr_etsm3usqdr | f07acd2a1 | слита в master | archive/tk609-rust-max-v6 (был) | удалена | — | env_yxprfa2p2i | thr_etsm3usqdr, thr_q59m58iarz, thr_qaugp94utt |
| M | tk-615-p42-land-master-v1-thr_3ctzptpbiu | 7263138c1 | слита в master | archive/bb/tk-615-p42-land-master-v1-thr_3ctzptpbiu (новый) | удалена | — | env_77bgfb5vrr | thr_3ctzptpbiu |
| M | tk-615-p42-land-master-v1-thr_mcskpthexb | 01ddc205e | слита в master | archive/bb/tk-615-p42-land-master-v1-thr_mcskpthexb (новый) | удалена | — | env_bhv7tgfg53 | thr_mcskpthexb |
| M | tk-616-p42-base-forward-maker-v1-thr_8uarvfeku6 | 27ec35772 | слита в master | archive/bb/tk-616-p42-base-forward-maker-v1-thr_8uarvfeku6 (был) | удалена | — | env_a2mj3tmqv7 | thr_8uarvfeku6, thr_igxub84dra, thr_nnb2d47g2c, thr_u6nw7gpa2i |
| M | tk-616-p42-base-forward-maker-v1-thr_j9ria2mazq | 60f41819d | слита в master | archive/bb/tk-616-p42-base-forward-maker-v1-thr_j9ria2mazq (новый) | удалена | — | env_33uaudbth7 | thr_j9ria2mazq |
| M | tk-617-619-622-p42-docs-land-v1-thr_627iyammpe | 5750d45bf | слита в master | archive/bb/tk-617-619-622-p42-docs-land-v1-thr_627iyammpe (новый) | удалена | — | env_4ngg94gihd | thr_627iyammpe |
| M | tk-617-p42-long-autopsy-v1-thr_shyq9p8xhe | 8a5834832 | слита в master | archive/bb/tk-617-p42-long-autopsy-v1-thr_shyq9p8xhe (был) | удалена | — | env_iatc3tvpsy | thr_2r2n32nuf6, thr_686gi5mb9q, thr_shyq9p8xhe |
| M | tk-618-p42-short-boost-v1-thr_bqxqnin8rb | 573818e81 | слита в master | archive/bb/tk-618-p42-short-boost-v1-thr_bqxqnin8rb (новый) | удалена | — | env_fiave6bwcz | thr_bqxqnin8rb |
| M | tk-618-p42-short-boost-v1-thr_neus83yvwx | 16f75d8fe | слита в master | archive/bb/tk-618-p42-short-boost-v1-thr_neus83yvwx (был) | удалена | — | env_bpmewdm4f9 | thr_neus83yvwx, thr_y6ziwrqhb4 |
| M | tk-619-p42-sanity-audit-v1-thr_3rw88e5nip | b94d76be4 | слита в master | archive/bb/tk-619-p42-sanity-audit-v1-thr_3rw88e5nip (был) | удалена | удалена | env_h8z38f9enj | thr_3rw88e5nip |
| M | tk-620-p42-stops-redesign-v1-thr_dj5gadnpnu | 4e448ffa3 | слита в master | archive/bb/tk-620-p42-stops-redesign-v1-thr_dj5gadnpnu (новый) | удалена | — | env_6qgibpnwep | thr_dj5gadnpnu |
| M | tk-621-p42-honest-universe-v1-thr_8d2u3d3yyt | 56d022d51 | слита в master | archive/bb/tk-621-p42-honest-universe-v1-thr_8d2u3d3yyt (новый) | удалена | — | env_758pxrwbik | thr_8d2u3d3yyt |
| M | tk-621-p42-honest-universe-v1-thr_ui4qd24eu3 | 6caf36efd | слита в master | archive/bb/tk-621-p42-honest-universe-v1-thr_ui4qd24eu3 (был) | удалена | — | env_r7dmn6ket6 | thr_ui4qd24eu3 |
| M | tk-622-p42-kill-zones-v1-thr_jxi9e4xi33 | f4dc0aceb | слита в master | archive/bb/tk-622-p42-kill-zones-v1-thr_jxi9e4xi33 (был) | удалена | — | env_32yip4bynf | thr_jxi9e4xi33 |
| M | tk-623-ci-red-master-v1-thr_6ahjhc94bq | 644aa5408 | слита в master | archive/bb/tk-623-ci-red-master-v1-thr_6ahjhc94bq (был) | удалена | удалена | env_xwa948df46 | thr_6ahjhc94bq |
| M | tk-623-ci-red-master-v1-thr_bfegkn6tb8 | 594675bf1 | слита в master | archive/bb/tk-623-ci-red-master-v1-thr_bfegkn6tb8 (новый) | удалена | — | env_52esswbcj6 | thr_bfegkn6tb8 |
| M | tk-625-p42-successful-breakouts-v1-thr_379nd3chp9 | 68b8a9336 | слита в master | archive/bb/tk-625-p42-successful-breakouts-v1-thr_379nd3chp9 (был) | удалена | — | env_2uu3qa87dp | thr_28tg85dpwp, thr_379nd3chp9, thr_jbdqae2gft |
| M | tk-625-p42-successful-breakouts-v1-thr_3z63t77bgz | 57147b350 | слита в master | archive/bb/tk-625-p42-successful-breakouts-v1-thr_3z63t77bgz (новый) | удалена | — | env_b7qu6cewiz | thr_3z63t77bgz |
| M | tk-626-p42-forward-refreeze-v1-thr_c9wp2z322n | 24ecc2f20 | слита в master | archive/bb/tk-626-p42-forward-refreeze-v1-thr_c9wp2z322n (новый) | удалена | — | env_j7t3cawjae | thr_c9wp2z322n |
| M | tk-626-p42-forward-refreeze-v1-thr_i3s8n4rgn2 | 93fe97139 | слита в master | archive/bb/tk-626-p42-forward-refreeze-v1-thr_i3s8n4rgn2 (был) | удалена | удалена | env_rztxvy9urq | thr_i3s8n4rgn2, thr_usjcn3925x |
| M | tools-compute-thr_p2ik57yyt2 | ee5d5797b | слита в master | archive/bb/tools-compute-thr_p2ik57yyt2 (новый) | удалена | — | — | — |
| A | g-022-arb-dense-v1-thr_pgaww747vc | 87803f0bf | предок g-022-arb-dense-v2-thr_6zrt96bu39 | archive/bb/g-022-arb-dense-v1-thr_pgaww747vc (новый) | удалена | — | — | — |
| A | g-022-arb-dense-v2-thr_6zrt96bu39 | d44e8e076 | предок g-022-arb-dense-v3-thr_5uavq9hdb5 | archive/bb/g-022-arb-dense-v2-thr_6zrt96bu39 (новый) | удалена | — | — | — |
| A | g-022-arb-dense-v3-thr_5uavq9hdb5 | 44417c071 | предок g-022-arb-dense-v4-thr_jumu3a4269 | archive/bb/g-022-arb-dense-v3-thr_5uavq9hdb5 (новый) | удалена | — | env_wsiyd79sa4 | thr_5uavq9hdb5 |
| A | g-022-arb-dense-v4-thr_jumu3a4269 | c5e30e9ac | предок g-022-arb-settings-deploy-v1-thr_4wbtcmkmcj | archive/bb/g-022-arb-dense-v4-thr_jumu3a4269 (новый) | удалена | — | — | — |
| A | g-022-arb-settings-deploy-v1-thr_4wbtcmkmcj | 2ff87cd2a | предок g-022-arb-settings-v2-thr_gtzn6edace | archive/bb/g-022-arb-settings-deploy-v1-thr_4wbtcmkmcj (новый) | удалена | — | env_j8na8s9yus | thr_4wbtcmkmcj |
| A | g-022-arb-settings-v1-thr_84rp8hztmk | 69c7be8d3 | предок g-022-arb-settings-deploy-v1-thr_4wbtcmkmcj | archive/bb/g-022-arb-settings-v1-thr_84rp8hztmk (новый) | удалена | — | env_s53cgausrt | thr_84rp8hztmk |
| A | g-022-arb-shadcn-v1-thr_ssuncg5kqy | 6fdd13cd4 | предок g-022-arb-dense-v1-thr_pgaww747vc | archive/bb/g-022-arb-shadcn-v1-thr_ssuncg5kqy (новый) | удалена | — | — | — |
| A | g-022-arb-swings-v1-thr_apqy89fbvz | 417649016 | предок g-022-arb-swings-v2-thr_82umt8bwgc | archive/bb/g-022-arb-swings-v1-thr_apqy89fbvz (новый) | удалена | — | — | — |
| A | g-022-arb-swings-v2-thr_82umt8bwgc | a2d7842f6 | предок g-022-evaa-wave-v1-thr_qan9vx925u | archive/bb/g-022-arb-swings-v2-thr_82umt8bwgc (новый) | удалена | — | — | — |
| A | g-022-basis-split-v1-thr_wvjbsnnpzs | 5e6332146 | предок g-022-arb-swings-v1-thr_apqy89fbvz | archive/bb/g-022-basis-split-v1-thr_wvjbsnnpzs (новый) | удалена | — | — | — |
| A | g-022-bingx-collector-v1-thr_ejqnws5xkv | ad89ad82e | предок g-022-bingx-fix-v1-thr_q4fez54qkv | archive/bb/g-022-bingx-collector-v1-thr_ejqnws5xkv (новый) | удалена | удалена | — | — |
| A | g-022-bingx-trades-v1-thr_328bg9i64i | e05ce4de8 | предок g-022-bingx-trades-v2-thr_dijr8v5mjs | archive/bb/g-022-bingx-trades-v1-thr_328bg9i64i (новый) | удалена | — | — | — |
| A | g-022-collect-followups-deploy-v1-thr_fayd2ss7im | 0d9f1cddb | предок g-022-collect-next-deploy-v1-thr_jrpirdzud2 | archive/bb/g-022-collect-followups-deploy-v1-thr_fayd2ss7im (новый) | удалена | — | env_imfp32qthg | thr_fayd2ss7im |
| A | g-022-collect-followups-review-v2-thr_fv2p7sgmr2 | 065ac7e2e | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-followups-review-v2-thr_fv2p7sgmr2 (новый) | удалена | — | env_q9ekasbsvi | thr_fv2p7sgmr2 |
| A | g-022-collect-followups-v1-thr_fv2p7sgmr2 | ebd415052 | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-followups-v1-thr_fv2p7sgmr2 (новый) | удалена | — | — | — |
| A | g-022-collect-followups-v1-thr_ifxhk34kt4 | 66062689b | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-followups-v1-thr_ifxhk34kt4 (новый) | удалена | — | env_r2gyntecmn | thr_ifxhk34kt4 |
| A | g-022-collect-followups-v2-thr_3scdpt5jec | 3835be7be | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-followups-v2-thr_3scdpt5jec (новый) | удалена | — | env_y24wcbcr3b | thr_3scdpt5jec |
| A | g-022-collect-next-deploy-v1-thr_jrpirdzud2 | 2d873b007 | предок g-022-srv-incident-deploy-v1-thr_riyqujcyvb | archive/bb/g-022-collect-next-deploy-v1-thr_jrpirdzud2 (новый) | удалена | — | env_ympskpzmbg | thr_jrpirdzud2 |
| A | g-022-collect-next-v1-thr_byvdac2e6n | 5d57dadc0 | предок g-022-collect-next-deploy-v1-thr_jrpirdzud2 | archive/bb/g-022-collect-next-v1-thr_byvdac2e6n (новый) | удалена | — | env_uvtb3396hi | thr_byvdac2e6n |
| A | g-022-collect-next-v2-thr_9gpk7azqi5 | 38d14b34f | предок g-022-collect-next-deploy-v1-thr_jrpirdzud2 | archive/bb/g-022-collect-next-v2-thr_9gpk7azqi5 (новый) | удалена | — | env_7wrkz7dyu5 | thr_9gpk7azqi5 |
| A | g-022-collect-pool-deploy-v1-thr_j8tvv4dfa9 | 893ba665d | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-pool-deploy-v1-thr_j8tvv4dfa9 (новый) | удалена | — | env_u32bw8nssb | thr_j8tvv4dfa9 |
| A | g-022-collect-pool-fix-v1-thr_p3xe532pjz | 7009bcf4c | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-pool-fix-v1-thr_p3xe532pjz (новый) | удалена | — | env_ga9nz4ca7s | thr_p3xe532pjz |
| A | g-022-collect-pool-fix-v1-thr_uxjg8z3us4 | 0e87cddec | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collect-pool-fix-v1-thr_uxjg8z3us4 (новый) | удалена | — | env_ahykiq3kf9 | thr_uxjg8z3us4 |
| A | g-022-collector-bars-v1-thr_52a7u5awue | 9fe8d8f87 | предок g-022-bingx-fix-v1-thr_q4fez54qkv | archive/bb/g-022-collector-bars-v1-thr_52a7u5awue (новый) | удалена | удалена | — | — |
| A | g-022-collector-netfix-v1-thr_vtbnt6m43x | 2411dd717 | предок g-022-bingx-fix-v1-thr_q4fez54qkv | archive/bb/g-022-collector-netfix-v1-thr_vtbnt6m43x (новый) | удалена | удалена | — | — |
| A | g-022-collector-perf-v1-thr_gwcp2875s8 | f867b442c | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-collector-perf-v1-thr_gwcp2875s8 (новый) | удалена | удалена | — | — |
| A | g-022-gate-collector-v1-thr_2r84hvf7gy | cfce94f62 | предок g-022-collect-followups-deploy-v1-thr_fayd2ss7im | archive/bb/g-022-gate-collector-v1-thr_2r84hvf7gy (новый) | удалена | — | — | — |
| A | g-022-health-red-v1-thr_3ew6scr4fb | b291c8f1a | предок g-022-health-red-v1-thr_jddhyeakdg | archive/bb/g-022-health-red-v1-thr_3ew6scr4fb (новый) | удалена | — | env_manyjucx3x | thr_3ew6scr4fb |
| A | g-022-okx-collector-v1-thr_u5qxitzkz5 | 33e909f7d | предок g-022-bingx-collector-v1-thr_ejqnws5xkv | archive/bb/g-022-okx-collector-v1-thr_u5qxitzkz5 (новый) | удалена | удалена | — | — |
| A | g-022-skhynix-look-v1-thr_5xi7vejsga | 23bf03ac1 | предок g-022-maker-grid-v1-thr_33mfczmz98 | archive/bb/g-022-skhynix-look-v1-thr_5xi7vejsga (новый) | удалена | — | — | — |
| A | g-022-srv-incident-deploy-v1-thr_riyqujcyvb | 7ecceabb4 | предок tk-580-bingx-load-sensitivity-v1-thr_dhxrwev9v5 | archive/bb/g-022-srv-incident-deploy-v1-thr_riyqujcyvb (новый) | удалена | — | env_wvkakftvd7 | thr_riyqujcyvb |
| A | g-022-srv-incident-v1-thr_5jvjjknrr3 | 872ac16d0 | предок g-022-srv-incident-deploy-v1-thr_riyqujcyvb | archive/bb/g-022-srv-incident-v1-thr_5jvjjknrr3 (новый) | удалена | — | env_njj5ze9i35 | thr_5jvjjknrr3 |
| A | g-022-srv-incident-v1-thr_ga9p85wehs | b8d4f043f | предок g-022-srv-incident-deploy-v1-thr_riyqujcyvb | archive/bb/g-022-srv-incident-v1-thr_ga9p85wehs (новый) | удалена | — | env_q5p5z3fufr | thr_ga9p85wehs |
| A | tk-578-prod-live-check-v1-thr_s7w2vie7q7 | 93d8bc76b | предок tk-579-late-bad-failed-units-v1-thr_3snwkfe4pm | archive/bb/tk-578-prod-live-check-v1-thr_s7w2vie7q7 (новый) | удалена | — | env_2swr5tt4zs | thr_s7w2vie7q7 |
| A | tk-580-bingx-load-sensitivity-v1-thr_dhxrwev9v5 | cb7690a03 | предок tk-580-bingx-load-sensitivity-v2-thr_g44nr7j2ye | archive/bb/tk-580-bingx-load-sensitivity-v1-thr_dhxrwev9v5 (новый) | удалена | — | env_fsk2upefhs | thr_dhxrwev9v5 |
| A | tk-580-bingx-load-sensitivity-v1-thr_rnebp6ffzy | 57e6cbf28 | предок tk-580-bingx-load-sensitivity-v1-thr_dhxrwev9v5 | archive/bb/tk-580-bingx-load-sensitivity-v1-thr_rnebp6ffzy (новый) | удалена | — | env_4mpqimhj6a | thr_rnebp6ffzy |
| A | tk-580-bingx-load-sensitivity-v2-thr_g44nr7j2ye | 9d5bbf270 | предок tk-583-bingx-gzip-deploy-v1-thr_6f3ywqxw52 | archive/bb/tk-580-bingx-load-sensitivity-v2-thr_g44nr7j2ye (новый) | удалена | — | env_7mb8xjswyr | thr_g44nr7j2ye |
| A | tk-581-srv-incident-2106-v1-thr_ed6wtxjeet | 87076d135 | предок tk-581-srv-incident-2106-v2-thr_44a9dgudsz | archive/bb/tk-581-srv-incident-2106-v1-thr_ed6wtxjeet (новый) | удалена | — | env_p3kbqktzc7 | thr_ed6wtxjeet |
| A | tk-581-srv-incident-2106-v2-thr_44a9dgudsz | 19139ddff | предок tk-582-binance-shard-stops-v1-thr_fq6jy9a3dx | archive/bb/tk-581-srv-incident-2106-v2-thr_44a9dgudsz (новый) | удалена | — | env_ydbwvy23gz | thr_44a9dgudsz |
| A | tk-582-dark-shard-accept-v1-thr_fq6jy9a3dx | 81ab6045b | предок tk-582-dark-shard-accept-v1-thr_j3f83ydesq | archive/bb/tk-582-dark-shard-accept-v1-thr_fq6jy9a3dx (новый) | удалена | — | — | — |
| A | tk-582-dark-shard-accept-v1-thr_j3f83ydesq | bc5b474d1 | предок tk-582-dark-shard-accept-v3-thr_f2dsgjjrvn | archive/bb/tk-582-dark-shard-accept-v1-thr_j3f83ydesq (новый) | удалена | — | env_2q4hdvhf5a | thr_j3f83ydesq |
| A | tk-582-dark-shard-accept-v2-thr_v994pq2rvs | ecd782594 | предок tk-582-dark-shard-accept-v1-thr_j3f83ydesq | archive/bb/tk-582-dark-shard-accept-v2-thr_v994pq2rvs (новый) | удалена | — | env_5wcunhhnxy | thr_v994pq2rvs |
| A | tk-583-bingx-gzip-deploy-v1-thr_6f3ywqxw52 | bca885d06 | предок tk-584-symbols-drop-after-restart-v1-thr_gniu84c8ud | archive/bb/tk-583-bingx-gzip-deploy-v1-thr_6f3ywqxw52 (новый) | удалена | — | env_xk34d3sigk | thr_6f3ywqxw52 |
| A | g022-turbo | a32ef34f1 | предок g-022-bingx-collector-v1-thr_ejqnws5xkv | archive/g022-turbo (новый) | удалена | удалена | — | — |
| M | tk-624-p42-page-where-we-are-v1-thr_f85kyyjc5f | 3a9ee0b22 | слита в master | archive/bb/tk-624-p42-page-where-we-are-v1-thr_f85kyyjc5f (новый) | удалена | — | env_uvi5rpzatw | thr_f85kyyjc5f |
| M | tk-624-p42-page-where-we-are-v1-thr_intmscd9df | 0314ddbd9 | слита в master | archive/bb/tk-624-p42-page-where-we-are-v1-thr_intmscd9df (был) | удалена | — | env_pajn4a8uxi | thr_intmscd9df, thr_kq9w7za6pc |

## Действия по порядку

1. Повторная классификация (`git branch --merged master`, предок несмёрженной ветки с другим кончиком): 83 слитых + 44 предка; из слитых исключены свои (`thr_9cnuxqpmm5`, `thr_vekyqfkx7f`); позже по слову координатора добавлены две ветки TK-624 (слиты `271aab9fa`).
2. Теги: `git tag archive/<ветка> <SHA>` (лёгкие, без `-f`), `git push origin refs/tags/…`; проверка `git ls-remote --tags` — 0 расхождений по SHA. Существующие теги не двигались.
3. 38 веток без worktree: локальное и origin-удаление.
4. 88 worktree: проверка статуса тредов (все idle/error), `git status --ignored` (см. выше), `bb environment archive-threads <env>` → окружения ушли в `retiring` (отсрочка 5 минут), bb снял worktree; 2 worktree без окружения bb (`g-022-collector-bars-v1-thr_52a7u5awue`, `tk-582-dark-shard-accept-v1-thr_fq6jy9a3dx` каталог `tk582-fix`) сняты `git worktree remove` без `--force`.
5. `git worktree prune`, затем удалены остальные 88 веток (локальные + 13 на origin).

Окружений в архиве с id: env_2q4hdvhf5a, env_2swr5tt4zs, env_2uu3qa87dp, env_32yip4bynf, env_33uaudbth7, env_3icpfpwfdk, env_4g6wymrexe, env_4mpqimhj6a, env_4ngg94gihd, env_52esswbcj6, env_52y6nkxp62, env_5ms5ewruyh, env_5wcunhhnxy, env_63nbyjwjex, env_68dzfrg88b, env_6qgibpnwep, env_758pxrwbik, env_77bgfb5vrr, env_7at287gtc9, env_7gtp5ab5ga, env_7mb8xjswyr, env_7wrkz7dyu5, env_8bxi9vpkbk, env_8ipgbtggc9, env_a2mj3tmqv7, env_afdnahpc26, env_ahykiq3kf9, env_aw6vdrudb4, env_b7qu6cewiz, env_bfy9sirevg, env_bhv7tgfg53, env_bkwvqtjem9, env_bpmewdm4f9, env_d93misyrup, env_ee8ffwa87x, env_erszpx9kqg, env_ewq99ayhjs, env_fiave6bwcz, env_fsk2upefhs, env_g872cgehzh, env_ga9nz4ca7s, env_h5zeijhmzg, env_h8z38f9enj, env_iatc3tvpsy, env_imfp32qthg, env_j7t3cawjae, env_j8na8s9yus, env_jw28gheqn5, env_kkwhez8jhk, env_manyjucx3x, env_migci28eib, env_mx8mphaxs5, env_my69gghghj, env_naagdwtxzt, env_nd4ftufzxj, env_njj5ze9i35, env_pajn4a8uxi, env_pn7845zk5h, env_q5p5z3fufr, env_q9ekasbsvi, env_qck9avadk2, env_qqm3y27wpi, env_r2gyntecmn, env_r4cugh5kba, env_r7dmn6ket6, env_rufwm8te2i, env_rztxvy9urq, env_s53cgausrt, env_shmeuntwcf, env_su9hsmn9ur, env_t9vbvgr5vc, env_u32bw8nssb, env_uv7jd37hvg, env_uvi5rpzatw, env_uvtb3396hi, env_wvkakftvd7, env_xeyj3in4qx, env_xk34d3sigk, env_xwa948df46, env_y24wcbcr3b, env_ybh8yiaw2p, env_ydbwvy23gz, env_yfijqgyxt7, env_ympskpzmbg, env_yxprfa2p2i, env_zjxtzw2js9.
