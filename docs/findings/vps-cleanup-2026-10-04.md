# VPS София: чистка диска (TK-047), 2026-10-04

## Шаг 1 (сделан, восстановимое)
df до: 73G, свободно 0 (100 %); после: занято 54G, свободно 16G (78 %). Удалено в /opt/alpha-compute: 22 `target-*`
(кроме `target-wave2`, `target-wave2tk44k1`), 19 `wave2-src*` (кроме `wave2-src`, `wave2-srctk44k1`), 19 `wave2*.tgz`
(кроме `wave2.tgz`, `wave2tk44k1.tgz`); apt clean; journalctl vacuum 50M. watcher/watcher-staging active, 3000/3001 = 302.

## Шаг 2: опись остального alpha (пока не тронуто)
Сверка — по паре «имя файла + размер в байтах» (список с VPS против всех файлов /data/alpha и /data/tk037 сервера
счёта 89.163.242.211); хеши не считались — перед удалением Судья решает, нужна ли побайтная проверка (sha256) на выборке.

| путь | размер | что | есть на сервере счёта (имя+размер) | предложение |
|---|---|---|---|---|
| /opt/alpha-archive/stage (e-jan..e-sep) | 7.4G | бинлоги архива Bybit по месяцам, 340 файлов | 283 из 340; **57 нет** (напр. ATOMUSDT-2026-02-20…05-20.binlog) | оставить 57; 283 — удалить после sha256-проверки |
| /opt/alpha-archive-tk021/dup-reimport | 3.6G | дубли переимпорта TK-021, 141 файл | 118 из 141; **23 нет** | то же |
| /opt/alpha-archive-tk021 (log, bin, stage) | ~0.1G | логи verify, бинарники | — | оставить (мелочь) |
| /home/deck/alpha/epochs | 13G (e-jul 11G) | перенос со Steam Deck: результаты старых счётов (forms, signals, rounds, manifest, .done, verify-*.status, .cellstmp-*.log) + бинлоги | 16 732 из 58 532 файлов; 41 800 нет (в основном forms/signals/rounds — производные результаты) | **решает CEO/Судья**: производные результаты — единственная копия, если их нет в docs/findings; не удалять без «да» |
| /opt/alpha-compute/jall | 1.2G | выходы бэктеста jall | не сверялось | кандидат, нужна ссылка на протокол |
| /opt/alpha-compute/tk025gate | 686M | гейт TK-025 (out2) | не сверялось | кандидат после закрытия TK-025 |
| /opt/alpha-compute/bin | 570M | бинарники и скрипты alpha | — | оставить (ими пользуются задания) |
| /opt/alpha-compute/src | 444M | старое дерево + target (435M) | — | кандидат: удалить `src/target` |
| /opt/alpha-compute/tk021, /root/tk021 | 365M + 299M | гейты/сверки TK-021 | не сверялось | кандидат, TK-021 закрыт |

## Вопросы CEO (не alpha или неясно)
/root: `polymarket_hft` 4.1G, `pmhft-lab-rerun` 1.1G, `pmhft-normalized-v2-current` 1.1G, `pmhft-normalized-v2-server`
142M, `parquet-audit-venv` 168M, `pin99-may-import` 217M, `.rustup` 601M, `.cargo` 371M, `bench-*`, `_wave*`
(похоже на другой проект — не трогаю). Это ещё до 8.5G.

## Шаг 3 (после Судьи)
Failed-юниты alpha-jall-vpsread2/3, alpha-p02jul-laneA/B — reset-failed и убрать файлы; /mnt/sb (ro sshfs на ящик)
отмонтировать, автомонтирование в /etc/fstab не найдено (проверить systemd .mount).

## Итог (инженер, 04.10 ~14:00)
df до 73G/0 свободно (100 %), после занято 45G, **свободно 25G (65 %)**. Удалено сверх шага 1:
- 401 файл (283 в /opt/alpha-archive/stage, 118 в /opt/alpha-archive-tk021/dup-reimport) — sha256 каждого на VPS и
  на 89.163.242.211 совпал побайтно с файлом того же имени; списки: `data/tk047/{vps.sha,ded.sha,delete.txt,keep.txt}`
  (скрипт удаления `/opt/alpha-compute/tk047/rm2.sh` с литеральными путями только внутри разрешённых каталогов);
- /opt/alpha-compute/src/target (435M).
Оставлены 80 файлов без побайтной копии (57 stage + 23 dup-reimport), /home/deck/alpha/epochs, /root/*, jall, tk025gate, tk021.
Шаг 3: reset-failed для alpha-jall-vpsread2/3, alpha-p02jul-laneA/B (транзиентные, файлов юнитов нет); /mnt/sb
отмонтирован (автомонтирования в fstab/systemd нет); alpha-archive-box.timer уже disabled; alpha-collector.service
на VPS inactive, но enabled — не трогал (вопрос CEO).
watcher/watcher-staging active, 127.0.0.1:3000/3001 = 302 до и после. vps-check не гонял (сборки живут в target-wave2*).
