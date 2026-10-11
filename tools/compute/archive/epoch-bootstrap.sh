#!/usr/bin/env bash
# Общий бутстрап эпохи (epoch-run.sh ветка archive, crash-stress.sh; план 2026-09-22/23): каталоги,
# симлинк bin на основной дом (её сутки не смешиваются с записью), пул instruments.csv, заглушка
# session.json (импорт архива его не пишет, а `bounce-grid` без него каталог сессией не считает —
# F10 падала на всех сутках, 23.09), копия журнала испытаний (DSR эпохи считает и прежние испытания,
# и её собственные), минутные BTC/ETH для режима с суток до первых (окно 4 ч захватывает прошлые сутки).
#   epoch-bootstrap.sh <дом эпохи> <дом-источник> <первые сутки> <последние сутки>
set -uo pipefail
HOME_E="${1:?дом эпохи}"
BASE="${2:?дом-источник}"
FIRST_DAY="${3:?первые сутки}"
LAST_DAY="${4:?последние сутки}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_env.sh
source "$SELF_DIR/_env.sh"

mkdir -p "$HOME_E"/{root,study/regime,b5}
[ -e "$HOME_E/bin" ] || ln -s "$BASE/bin" "$HOME_E/bin"
[ -f "$HOME_E/root/instruments.csv" ] || cp "$BASE/root/instruments.csv" "$HOME_E/root/"
[ -f "$HOME_E/root/session.json" ] || echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > "$HOME_E/root/session.json"
mkdir -p "$HOME_E/$(dirname "$RUNS_JOURNAL")"
[ -f "$HOME_E/$RUNS_JOURNAL" ] || cp "$BASE/$RUNS_JOURNAL" "$HOME_E/$RUNS_JOURNAL"
since=$(date -u -d "$FIRST_DAY -1 day" +%F)
python3 "$BASE/bin/ref-klines.py" --out-dir "$HOME_E/study/regime" --since "$since" --until "$LAST_DAY"
