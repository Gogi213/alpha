# Установка формы запуска на calc (TK-241, В-7) — только в окне между проходами, чужие юниты не перезапускать

Схема (решение Судьи, reviews/tk241-unit-user-2026-10-14.md): веб `alpha-launch` — пользователь `alpha-launch`, пишет только запрос в
`/data/launch/requests`; в очередь `/data/sched` его ставит root-юнит `alpha-launch-submit` (`.path` → oneshot), проверив запрос заново.
Права и состав `/data/sched` не меняются.

С dev-машины (PowerShell, `ssh calc`; в Git Bash `calc` не резолвится):

1. Пакет: `launch/` + `registry/` (py) + `compute/hyp_stack.py` + `docs/research/spec-v227-2026-10-12.md` +
   `docs/findings/p12-cells-{2026-10-06,ext-2026-10-07}.csv` (читает cellhash_reg) → `/opt/alpha-launch/{tools/launch,tools/registry,tools/compute,docs/research,docs/findings}` (tar по ssh, владелец root); снимок Летописи `docs/registry/*.jsonl` и `files/` →
   `/data/launch/registry/` (только чтение; обновление = повторный tar, кэш сбрасывается по mtime results.jsonl);
   `ln -s /data/launch/registry /opt/alpha-launch/docs/registry` (cellhash_reg/db читают `docs/registry` от корня пакета).
2. Пользователь и каталоги: `useradd --system --no-create-home --shell /usr/sbin/nologin alpha-launch;
   install -d -m 755 -o root -g root /data/launch /data/launch/out; install -d -m 755 -o alpha-launch -g alpha-launch /data/launch/requests`.
3. Токен: `install -d -m 755 /etc/alpha-launch && head -c 32 /dev/urandom | base64 | tr -d '/+=\n' > /etc/alpha-launch/token && chown alpha-launch /etc/alpha-launch/token && chmod 400 /etc/alpha-launch/token`
   (≥ 24 знака; сервер отказывается стартовать с коротким). Токен не печатать в логи/чаты, кроме ссылки владельцу.
4. Юниты: `cp /opt/alpha-launch/tools/launch/alpha-launch{,-submit}.service /opt/alpha-launch/tools/launch/alpha-launch-submit.path /etc/systemd/system/ &&
   systemctl daemon-reload && systemctl enable --now alpha-launch alpha-launch-submit.path`.
5. Проверка снаружи: `curl -s -o /dev/null -w '%{http_code}' http://89.163.242.211:8791/<токен>/` → 200; `…:8791/` и `…:8791/неверный/` → 404;
   `curl -X POST …:8791/api/submit` → 404. Порт 8791 свободен (занято 8787, 8788).
6. Проба 1 хеш × 1 месяц через форму/запрос → `/data/launch/out/<run>/{result.json,run.json}` и задание в `alsched ps`.
7. Откат: `systemctl disable --now alpha-launch alpha-launch-submit.path && rm /etc/systemd/system/alpha-launch{,-submit}.service /etc/systemd/system/alpha-launch-submit.path && systemctl daemon-reload`
   (проход и очередь не затрагиваются).

Риски: HTTP без TLS — токен в пути виден в сети (как у alpha-board, принято); форма исполняет — белые списки (проверяются дважды: веб и root-юнит),
замок «одна постановка за раз» (пока в `requests/` лежит запрос — busy), потолок хешей, повтор того же набора = «нечего считать» (run.json → queued_months).
Веб-процесс при компрометации может писать только в `/data/launch/requests`; root-юнит принимает файлы только `L-ГГГГММДД-ЧЧММСС.json` (не ссылка, ≤ 64 КБ) и всё равно проверяет заново.
