#!/usr/bin/env bash
# Гейт F3 этапа F (docs/plan/dev-plan-2026-09-20.md §3): `--queue-model
# risk-adverse` обязан давать те же круги, что бинарник до правки. Механизм
# сравнения — тот же, что у гейта «те же круги» прошлых задач.
#
# Запуск на счётной машине 13.140.29.171 (root), когда она СВОБОДНА:
#   pgrep -af alpha; uptime            # 20.09 15:40Z нагрузка 8.8 при 4 ядрах —
#                                      # шёл чужой approach-scan, гейт отложен
#   cp tools/compute/f3-queue-gate.sh /opt/alpha-compute/bin/ && chmod +x …
#   bin/f3-queue-gate.sh bin/alpha-<новый-хеш> [bin/alpha-2ee7d02]
#
# Новый бинарник — из этого дерева (как в плане: git archive src|tar → cargo
# build --release -j 3 --bin alpha → bin/alpha-<hash>); старый по умолчанию —
# `bin/alpha-2ee7d02` (до правки F3; на 20.09 14:39Z на него смотрел симлинк
# `alpha`). Набор — база: RTT В-68, лот от пула (22а), 3 потока, порог В-66
# (notional $10k) + сила ≥ 100 % (В-67), 5 монет × 3 суток (09-16…18).
#
# Почему не `md5` файла целиком: F3 добавила колонку `n_fill_by_cross` в
# `forms.csv` (перед `signals_by_hour`) и `queue=`/`paths=` в шапку — по
# построению файлы отличаются шапкой и одной колонкой. Сравниваются: круги
# (`rounds.csv` телом без `#`-шапки — у него F3 колонок не добавляла) и все
# прежние колонки `forms.csv` по именам; счётчик `n_fill_by_cross` печатается
# отдельно (у risk-adverse он не обязан быть нулём: путь (3) есть и у
# `NoPartialFillExchange`).
set -euo pipefail
cd /opt/alpha-compute || exit 1
NEW="${1:?новый бинарник: bin/alpha-<hash>}"
OLD="${2:-bin/alpha-2ee7d02}"
[ -x "$NEW" ] || { echo "$NEW: не исполняемый файл" >&2; exit 1; }
[ -x "$OLD" ] || { echo "$OLD: не исполняемый файл" >&2; exit 1; }
SYMS="${SYMS:-HYPE DRAM NEAR LTC ADA}"
DAYS="${DAYS:---day 2026-09-16 --day 2026-09-17 --day 2026-09-18}"
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000"
P95="--p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
COMMON="--root root $RTT $P95 --order-qty-from-pool --threads ${THREADS:-3}"
COMMON="$COMMON --h3-mode notional --h3-usd 10000 --min-flow-pct 100"
COMMON="$COMMON --stop-form pct1 --take-form 1to1 $DAYS"
for s in $SYMS; do COMMON="$COMMON --symbol $s"; done
rm -rf b5/f3-gate-old b5/f3-gate-new
echo "== старый $OLD (флага --queue-model ещё нет)"
# shellcheck disable=SC2086
"$OLD" lob bounce-grid $COMMON --out-dir b5/f3-gate-old
echo "== новый $NEW --queue-model risk-adverse --no-post-only"
# shellcheck disable=SC2086
"$NEW" lob bounce-grid $COMMON --queue-model risk-adverse --no-post-only --out-dir b5/f3-gate-new

echo "== rounds.csv: тело без шапки, байт в байт"
if diff <(grep -v '^#' b5/f3-gate-old/rounds.csv) \
        <(grep -v '^#' b5/f3-gate-new/rounds.csv) > /tmp/f3-rounds.diff; then
  echo "ROUNDS: OK, строк $(( $(wc -l < b5/f3-gate-new/rounds.csv) - 1 ))"
else
  echo "ROUNDS: РАСХОЖДЕНИЕ — /tmp/f3-rounds.diff"; head -5 /tmp/f3-rounds.diff; exit 1
fi

echo "== forms.csv: прежние колонки по именам"
python3 - <<'PY'
import csv

def rows(path):
    with open(path, encoding='utf-8') as f:
        r = csv.reader(line for line in f if not line.startswith('#'))
        head = next(r)
        return head, list(r)

h_old, r_old = rows('b5/f3-gate-old/forms.csv')
h_new, r_new = rows('b5/f3-gate-new/forms.csv')
missing = [c for c in h_old if c not in h_new]
assert not missing, f'новый forms.csv потерял колонки: {missing}'
i_old = {c: i for i, c in enumerate(h_old)}
i_new = {c: i for i, c in enumerate(h_new)}
assert len(r_old) == len(r_new), (len(r_old), len(r_new))
bad = [
    (a[:3], {c: (a[i_old[c]], b[i_new[c]]) for c in h_old
             if a[i_old[c]] != b[i_new[c]]})
    for a, b in zip(r_old, r_new)
    if any(a[i_old[c]] != b[i_new[c]] for c in h_old)
]
assert not bad, f'прежние колонки разошлись: {bad[:2]}'
cross = sum(int(b[i_new['n_fill_by_cross']]) for b in r_new)
print(f'FORMS: OK, прежние колонки совпали ({len(r_new)} строк); '
      f'крестов (путь 3) у risk-adverse {cross}')
PY

echo "== md5 для протокола (у forms.csv шапка и колонка отличаются по построению)"
md5sum b5/f3-gate-old/rounds.csv b5/f3-gate-new/rounds.csv \
       b5/f3-gate-old/forms.csv b5/f3-gate-new/forms.csv
echo "== гейт F3 пройден"
