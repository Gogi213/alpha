# Установка формы запуска на calc (TK-241, В-7) — только в окне между проходами, чужие юниты не перезапускать

С dev-машины (PowerShell, `ssh calc`; в Git Bash `calc` не резолвится):

1. Пакет: `launch/` + `registry/` (py) + `compute/hyp_stack.py` + `docs/research/spec-v227-2026-10-12.md` +
   `docs/findings/p12-cells-{2026-10-06,ext-2026-10-07}.csv` (читает cellhash_reg) → `/opt/alpha-launch/{tools/launch,tools/registry,tools/compute,docs/research,docs/findings}` (tar по ssh); снимок Летописи `docs/registry/*.jsonl` и `files/` →
   `/data/launch/registry/` (только чтение; обновление = повторный rsync/tar, кэш сбрасывается по mtime results.jsonl);
   `mkdir -p /data/launch/out`; `ln -s /data/launch/registry /opt/alpha-launch/docs/registry` (cellhash_reg/db читают `docs/registry` от корня пакета).
2. Токен: `install -d -m 700 /etc/alpha-launch && head -c 32 /dev/urandom | base64 | tr -d '/+=\n' > /etc/alpha-launch/token && chmod 600 /etc/alpha-launch/token`
   (≥ 24 знака; сервер отказывается стартовать с коротким). Токен не печатать в логи/чаты, кроме ссылки владельцу.
3. Юнит: `cp /opt/alpha-launch/tools/launch/alpha-launch.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now alpha-launch`.
4. Проверка снаружи: `curl -s -o /dev/null -w '%{http_code}' http://89.163.242.211:8791/<токен>/` → 200; `…:8791/` и `…:8791/неверный/` → 404;
   `curl -X POST …:8791/api/submit` → 404. Порт 8791 свободен (занято 8787, 8788).
5. Откат: `systemctl disable --now alpha-launch && rm /etc/systemd/system/alpha-launch.service && systemctl daemon-reload` (проход и очередь не затрагиваются).

Риски: HTTP без TLS — токен в пути виден в сети (как у alpha-board, принято); форма исполняет — белые списки, замок «одна постановка за раз»,
потолок хешей, повтор того же набора = «нечего считать» (run.json → queued_months). Юнит от root (запись в чужую очередь /data/sched) — развилка Судье.
