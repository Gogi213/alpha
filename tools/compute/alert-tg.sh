#!/usr/bin/env bash
# Канал алертов ночи — Telegram (владелец 20.09: «почему если что-то падает — это молчаливо?»).
# Вызов: alert-tg.sh "<текст>"; ночной скрипт зовёт его через ALERT_CMD. Токен и чат — только в
# /etc/alpha/alert.env (root:600, кладёт владелец; в репо и в логи не попадают):
#   TG_TOKEN=<токен бота от @BotFather>
#   TG_CHAT=<id чата или пользователя — написать боту и взять из getUpdates>
# Подключение в юнит: `systemctl edit alpha-grid-nightly` →
#   [Service]
#   Environment=ALERT_CMD=/opt/alpha-compute/bin/alert-tg.sh
# Без файла или без токена — молча выходит 0 (алерт остаётся в study/ALERTS.log и в failed-юните).
set -u
ENV_FILE=${ALERT_ENV:-/etc/alpha/alert.env}
[ -r "$ENV_FILE" ] || exit 0
# shellcheck disable=SC1090
. "$ENV_FILE"
[ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT:-}" ] || exit 0
text=${1:-"alpha: (пусто)"}
curl -s -m 15 -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=${text:0:3500}" >/dev/null 2>&1 || exit 0
exit 0
