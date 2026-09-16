---
title: VPS OVH Singapore 139.99.91.22 — второй сервер (доступ, ресурсы, латентность)
date: 2026-09-15
type: skill
salience: 2
last_access: 2026-09-15
tags: [server, ovh, vps, ssh, bybit, latency]
---

# VPS OVH `vps-cab27b0c.vps.ovh.ca` (139.99.91.22)

Зачем страница: вход сюда стоит один раз настроить и больше не выяснять (первый заход стоил
замка на машине — см. «Грабли»). Боевой коллектор живёт отдельно, на старом сервере
`13.140.29.171` (см. `projects/active/alpha.md`).

## Что это

- Сервис в OVH: `vps-cab27b0c.vps.ovh.ca`, регион **`os-sgp2`** (Сингапур), IPv4 `139.99.91.22`,
  IPv6 `2402:1f00:8000:800::23d9`, ОС **Ubuntu 26.04 LTS** (ядро 7.0.0-28), TZ `UTC`.
- Ресурсы: **2 vCPU** (Haswell, без TSX), **3.7 ГБ RAM**, диск **38 ГБ** (свободно 36), `OpenStack Nova`.
- Есть: `curl`, `python3`, `rsync`, `git`, `systemctl`. **Нет `rustc`/`cargo` и `~/.cargo`** —
  прод без toolchain вообще (проверено 2026-09-15). Поэтому бинарник **собирается не здесь**, а на
  старом `13.140.29.171` (там toolchain 1.93.1 и готовый `target`), см. «Сборка и раскатка».
- `ubuntu` с `NOPASSWD` sudo (проверено `sudo -n true`).
- **Вход по SSH-ключу настроен**: `ssh -i ~/.ssh/id_rsa ubuntu@139.99.91.22` (ключ
  `id_rsa` — тот же, что на tokyo/singapore из `~/.ssh/config`). Пароль для входа не нужен.

## Замер против старого сервера (2026-09-15, curl)

| | новый OVH Сингапур | старый `13.140.29.171` |
|---|---|---|
| `api.bybit.com` TCP connect | **3–4 мс** | 26–35 мс |
| `api.bybit.com` полный ответ | **25–30 мс** | 566–606 мс |
| `stream.bybit.com` TCP connect | **2.6 мс** | ~30 мс (замер кривой) |
| vCPU / RAM / свободно | 2 / 3.7 ГБ / 36 ГБ | 4 / 7.9 ГБ / 39 ГБ |

`stream.bybit.com` резолвится в CloudFront (`d2mo22rbksh9yz.cloudfront.net`), у сингапурской
машины edge ближе. Вывод: по латентности до Bybit сингапурская VPS **лучше** старой; по железу
старая вдвое мощнее. Хранилище у обеих ≈ 18–20 суток при 2 ГБ/сутки.

## Управление через CLI в OVHcloud Shell

- `ovhcloud vps list` → имя сервиса; `ovhcloud vps get-console-url <service>` → KVM-консоль.
- `ovhcloud vps reboot <service> --wait` — флага rescue у CLI нет, rescue только из Manager.
- `ovhcloud vps reinstall <service> --image-selector --public-ssh-key "<ключ>" --wait` —
  переустановка с предустановленным ключом (`--ssh-key <имя>` — если ключ заведён в аккаунте).
- `ovhcloud vps set-password <service>` **на этом VPS не работает**: API отвечает
  `403 Client::Forbidden: "This function is not available on your VPS"`.

## Грабли (стоили часа)

1. OVH отдаёт первичный пароль **с флагом «истёк»**: sshd после аутентификации не даёт ни
   shell, ни exec — только `WARNING: Your password has expired. Password change required but no
   TTY available.`. Со стороны клиента это выглядит как «доступа нет».
2. **Ключ не спасает от истёкшего пароля**: `ubuntu` заходит по ключу, но PAM на фазе account
   всё равно требует смены — сессия не открывается. Разблокировка — довести `passwd` до конца
   через PTY-сессию (`invoke_shell()`), prompt-последовательность:
   `Current password:` → `New password:` → `Retype new password:` → `passwd: password updated
   successfully`, после чего sshd рвёт соединение (это нормально, переподключаться).
3. **Не терять сгенерированный пароль.** Первый заход сломал доступ именно так: новый пароль
   был сгенерирован внутри процесса и напечатан нигде — обрыв сессии его унёс, и машина осталась
   запертой (панель показывает старый, он уже не подходит). Правило владельца: значение
   держать в оболочке (`$env:`) и/или сразу отдавать владельцу; в файлы и память не писать.
   В этом окружении каждый вызов PowerShell — новый процесс, поэтому «положить в env и забыть»
   работает только внутри одного вызова: генерацию и применение делать **одним** вызовом.
4. Пароль от пароля отличается: `passwd` на неверный текущий отвечает
   `Authentication token manipulation error` / `password unchanged`, а не «неверный пароль».
5. Скрипты захода лежат в `.tmp-ssh/` (каталог под `.gitignore`): `probe_key.py` — ресурсы и
   доступность Bybit, `fix_expiry.py` — смена пароля через PTY по ключу, `latency.py` — замеры.
   Пароли в них не хранятся: берутся из `DSH_VPS_OLD_PW` / `DSH_VPS_NEW_PW`.

## Развёрнуто 2026-09-15

- Диск: у VPS **два диска** — `sda` 40 ГБ (система, `/`) и **`sdb` 50 ГБ неразмеченный**.
  `sdb` отформатирован в ext4 (`alpha-data`, UUID `c126a2b7-8ea1-4aec-915d-4e5413321336`) и
  смонтирован в **`/opt/alpha`** (47 ГБ свободно), запись в `/etc/fstab` (бэкап `fstab.bak-*`).
- Перенесено с боевого сервера через эту машину (`~/.ssh/id_rsa` на обоих концах):
  `/opt/alpha/alpha-collector` (11 535 240 Б, md5 `e1d721a9018d8f4628e7799ff961fdd8`),
  `/opt/alpha/src` (исходники, **байт-в-байт равны репозиторию**: 133 файла `src/`,
  `Cargo.toml`/`Cargo.lock` совпадают, пересборка не нужна), `/opt/alpha/root/instruments.csv`
  (серверный топ-30, md5 `1672790c…`), юнит `/etc/systemd/system/alpha-collector.service`.
- Прогон 100 монет (`/opt/alpha/root100`, окно 600 с, пул из `lob pick --window-secs 600 --top 100`,
  файл помечен `debug`): **CPU 12.21 % ядра**, RSS 9.4 → 38.4 МБ, 5 потоков, parse p99 107.5 мкс,
  очередь p99 194.6 мкс, 7 307 890 записей, 34.9 МБ, 2.49 Б/запись, 0 gaps/reconnects/resyncs.
  Бюджет `PLAN.md` 6.1 (< 5 %) превышен в 2.4 раза, но 12 % одного ядра из двух — машина простаивает.
- Диск (измерено по фактическим монетам): топ-100 → **5.02 ГБ/сутки (9 дней)**, боевой топ-50 →
  2.95 ГБ/сутки (16 дней), серверный топ-30 → 2.33 ГБ/сутки (20 дней). Доля топ-5 монет — 24.9 %
  байтов, топ-10 — 36.9 % (AKEUSDT один даёт 0.36 ГБ/сутки).
- В `lob session` **нет ручек глубины/уровней** — только выбор пула (`--pool-instruments` /
  `--all-instruments`). Значит рычаги: размер пула, ротация/выгрузка, смена кодека
  (формат заморожен В-49 — только предрегистрацией) и профилирование CPU.
- **Прод включён**: юнит `alpha-collector.service` активен с **2026-09-15T18:36:40Z** (до этого —
  два потока с 17:13:53Z) на объединённом бинарнике T45-ревизия + T46; пул **100 монет**
  (часовой боевой отбор топ-105 минус глобальный топ-5 по обороту BTC/ETH/XRP/SOL/ZEC).
  Пишутся **два потока**: основной `<SYM>-<день>.binlog` (`.50`) и `deep/<SYM>-<день>.binlog`
  (`.200`, тот же v3). Старые бинарники лежат рядом: `alpha-collector-prev` (откат),
  `alpha-collector-dual`, `alpha-collector-200`.
- **Объём и запас**: ≈**7 ГБ/сутки** (быстрый ~4.2 + глубокий ~2.9, глубина **+69 %** к быстрому);
  46 ГБ свободных ⇒ **≈6 суток**. **Ротации/выгрузки нет** — это первое, что упрётся.
- **Архивация**: `lob archive --path <файл.binlog> --level 19` (или `--out`, `--delete-original`) →
  `*.binlog.zst`, один zstd-поток: на боевом v3 — **81.3 %** оригинала, на переписанном — 90.3 %.
  Читается всеми командами; удаление оригинала только по флагу и при `verify-*.status == ok`.
  Контейнер принимает **только v3** (старые v2-сутки — сначала `binlog-stats --rewrite-out`).
- До этого на старом сервере `13.140.29.171` **погашен** файлом `stop` (В-41): 342 465 743 записи /
  1.13 ГБ за 20.2 ч на топ-30. Разбор — `docs/findings/deploy-2026-09-15-ovh-singapore.md`,
  дрейф потоков — `docs/findings/stream-drift-2026-09-15.md`.

## Сборка и раскатка бинарника (2026-09-16: собираем на боевом, системный диск)

Действующий путь (проверен 2026-09-16). Toolchain живёт **на боевом** `139.99.91.22`, но **на
системном диске `sda`** (`/`, 34 ГБ свободно) — диск данных `sdb` (`/opt/alpha`) под запись не занимаем.

1. **Компилятор C** (на сервере его не было): `sudo -n apt-get install -y build-essential` — один раз.
2. **rustup** в `/home/ubuntu/.cargo`:
   `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain none`,
   затем `rustup toolchain install 1.93.1` (версия из `rust-toolchain.toml`).
3. **Исходники**: локально `tar czf src.tgz src examples Cargo.toml Cargo.lock rust-toolchain.toml` (0.8 МБ)
   → SFTP в `/home/ubuntu/` → распаковать в `/home/ubuntu/alpha-build` → `nice -n 10 cargo build --release`.
   Первая сборка ≈15 мин на 2 ядрах параллельно с записью (прод не задет), дальше инкрементально.
4. **Занято на `sda`**: `.cargo` 180 МБ + `target` 423 МБ; бинарник `target/release/alpha` (11.7 МБ,
   md5 первой сборки `630d466a47f45795420b6d12298a919d`).
5. **Установка**: залить в `/home/ubuntu/`, затем **обязательно через `sudo`** (`/opt/alpha` — root, без
   sudo `cp` падает с `Permission denied`): `cp -a alpha-collector alpha-collector-prev-<метка>` →
   `install -m 755 <новый> alpha-collector` → `systemctl restart alpha-collector`; сверить `md5sum`.
6. **Проверка**: `systemctl is-active` + `NRestarts=0`, появились части `-pN.binlog`, `wc -l gaps.csv`
   (пусто = разрывов нет). `journalctl` под `ubuntu` не читается («No entries» + hint) — не ошибка службы.

Откат: `alpha-collector-prev-rpi` (до RPI-правки), `alpha-collector-prev` (до T45+T46),
`alpha-collector-dual`, `alpha-collector-200`.

**Что не сработало и почему (чтобы не повторять):** кросс-сборка под Linux на рабочей машине —
все зависимости собираются, но финальная линковка падает из-за пробела в пути проекта
(`C:\visual projects\alpha`): цепочка cc-rs → clang → lld режет путь. Ни свои `.cmd`/exe-обёртки,
ни `cargo-zigbuild`, ни короткий путь `C:\VISUAL~1\alpha`, ни отдельный `--target-dir` без пробелов
не помогли. Плюс песочница DSH не даёт писать в `~/.rustup`/`~/.cargo` без развёрнутого доступа.
**История (до В-54, не использовать):** порядок был такой.

1. Локально: `tar czf src.tgz src examples Cargo.toml Cargo.lock rust-toolchain.toml` (≈0.8 МБ).
2. На `13.140.29.171` (`root`, ключ `id_rsa`): распаковать в `/opt/alpha/src46` (там `target` 422 МБ —
   сборка инкрементальная, 39 с), `export PATH=/root/.cargo/bin:$PATH`,
   `nice -n 10 cargo build --release`; забрать `target/release/alpha` SFTP-ом (11.7 МБ).
3. На боевой (`ubuntu` + ключ): залить во `/home/ubuntu/`, затем **обязательно через `sudo`**
   (`/opt/alpha` принадлежит root, без sudo — `Permission denied` на первом же `cp`):
   `cp -a alpha-collector alpha-collector-prev-<метка>` → `install -m 755 <новый> alpha-collector` →
   `systemctl restart alpha-collector`; сверить `md5sum` с собранным.
4. Проверка: `systemctl is-active` + `NRestarts=0`, появились новые части `-pN.binlog`, `wc -l gaps.csv`
   (пусто = разрывов нет). `journalctl` под `ubuntu` не читается («No entries» + hint про группы) —
   это не ошибка службы.

Откат: старые бинарники рядом, `alpha-collector-prev-rpi` (до RPI-правки), `alpha-collector-prev`
(до T45+T46), `alpha-collector-dual`, `alpha-collector-200`.

## Сеть и пинги (замер 2026-09-15, новая VPS)

- **Bybit мигрирует торговые серверы из AWS Singapore в AWS Tokyo — миграция во второй половине
  ноября 2026, сейчас (сентябрь) мэтчинг ещё в Сингапуре** (поправка владельца к моему чтению
  объявления). Поэтому сингапурская VPS сейчас стоит там, где надо; переезжать — после того как
  Bybit переедет, вместе с ним. Для коллектора разницы нет в любом случае (в записи биржевые
  таймстампы), для живого бота с ордерами — придётся следовать за мэтчингом.
- Замеры: `ping api.bybit.com` (эдж CloudFront Сингапур) — **1.58 мс avg, mdev 0.02, 0% потерь**;
  `stream.bybit.com` — 1.49 мс; IPv6 полный запрос 1.2 мс против 2.5 мс connect по IPv4.
- Состояние машины: ядро 7.0.0-28-generic, `preempt=full`, cpufreq нет (виртуалка), NIC `ens3`
  virtio с одной очередью (rx-0/tx-0), offload'ы включены, `irqbalance` выключен, **chrony активен**,
  `unattended-upgrades` **активен**. sysctl: `busy_poll=0`, `busy_read=0`, `netdev_max_backlog=1000`,
  `tcp_congestion_control=cubic`, `tcp_fastopen=1`, `tcp_slow_start_after_idle=1`, qdisc `fq_codel`,
  буферы сокетов по 4 МБ.
- Что можно покрутить (и замерить A/B на 10-мин/100-монетном тесте, baseline: CPU 12.21 %,
  parse p99 107.5 мкс, очередь p99 194.6 мкс): `busy_poll/busy_read=50`, `bbr`,
  `tcp_slow_start_after_idle=0`, `netdev_max_backlog=25000`, `tcp_fastopen=3`; выключить
  `unattended-upgrades`; в юните сейчас **`Nice=10` (понижение приоритета)** — на выделенной машине
  логичнее `Nice=-5` + `CPUAffinity`; IRQ virtio-net прибить к ядру. В коде `TCP_NODELAY` на
  WS-сокете не выставляется (`src/bybit/conn.rs` — только `MaybeTlsStream`), `busy_poll` на сокет нет.

### Результаты A/B (одинаковый прогон: 100 монет, 600 с, пул `root100`)

| прогон | конфиг | записей | CPU на млн записей | parse p99 | queue p99 |
|---|---|---|---|---|---|
| A | дефолт (`cubic`, `busy_poll=0`) | 7.31 млн | 9.99 с | 107.5 мкс | 194.6 мкс |
| B | A + `busy_poll/busy_read=50` | 9.26 млн | **14.51 с (+45 %)** | 73.7 | 213.0 |
| C | A + `bbr`/`backlog=25000`/`fastopen=3`/`slow_start_after_idle=0`/apt-таймеры off, `busy_poll=0` | 8.73 млн | 9.47 с | 57.9 | 151.6 |
| D | C + `taskset -c 1 nice -n -5` | 8.03 млн | 9.32 с | 88.1 | **231.4** |

**Выводы:** `busy_poll` **отклонён** (+45 % CPU за p99, который и так ниже бюджета); sysctl-набор
**оставлен** (≈−5 % CPU на запись, `/etc/sysctl.d/99-alpha-latency.conf`); привязка к ядру и
повышение приоритета **отклонены** (CPU не изменился, p99 хуже). p99 между прогонами шумят
(57.9–107.5 мкс) — сравнивать по CPU на запись, а не по p99. Юнит оставляем как есть (`Nice=10`).

**Профиль** (`perf-root100c.data`, `perf record -F 499 -g`): ядро и сисколлы ~25 %
(`finish_task_switch` 7.0 %, `_raw_spin_unlock_irqrestore` 5.8 %, `iowrite16` 3.1 %, `recv`/`syscall`,
`handle_softirqs`), libc `rep stos` (**memset 9.1 %** — вызывающего perf не символизирует даже с
`libc6-dbg`), приложение: `Book::apply` 5.1 %, serde_json ~3 %, `parse_e9` 1.5 %,
`sink::write_market_event` 1.0 %, `ZSTD_compressBlock_fast` 0.6 %, `encode_frame_payload` 0.4 %.
Приложение в сумме ~17 % ⇒ рычаг не в мелких правках, а в **сисколлах и пробуждениях**
(крупнее батчи кадров, меньше переключений I/O-поток ↔ поток решений) и в memset; это правки кода
с тестами и ревью — отдельный тикет, не «покрутить на сервере».

## Не сделано (варианты гигиены)

- вход по паролю не отключён (`PasswordAuthentication` в sshd_config);
- `unattended-upgrades`/firewall (ufw) не настраивались;
- `rustup` на проде не поставлен и не нужен: сборка идёт на старом сервере, сюда заливается готовый
  бинарник (см. «Сборка и раскатка»); последняя раскатка — 2026-09-15T22:47:56Z (RPI-правка, В-53).
