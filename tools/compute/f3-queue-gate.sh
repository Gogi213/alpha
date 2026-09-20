#!/usr/bin/env bash
# Гейт F3 этапа F (docs/plan/dev-plan-2026-09-20.md §3): `--queue-model
# risk-adverse` обязан давать те же круги, что бинарник до правки. Механизм
# сравнения — тот же, что у гейта «те же круги» прошлых задач.
#
# Запуск на счётной машине 13.140.29.171 (root), когда она СВОБОДНА:
#   pgrep -af alpha; uptime
#   scp tools/compute/f3-queue-gate.sh root@13.140.29.171:/opt/alpha-compute/bin/
#   bin/f3-queue-gate.sh bin/alpha-<новый-хеш> [bin/alpha-2ee7d02]
#
# Новый бинарник — из этого дерева (как в плане: git archive src|tar → cargo
# build --release -j 3 --bin alpha → bin/alpha-<hash>); старый по умолчанию —
# `bin/alpha-2ee7d02` (до F3). Набор — база: RTT В-68, лот от пула (22а), 3 потока,
# порог В-66 (notional $10k) + сила ≥ 100 % (В-67), 5 монет × 3 суток (09-16…18).
# Монеты проверены маркером `verify ok` на этих сутках (HYPE/DRAM/NEAR/LTC на
# счётной машине — `fail`, их брать нельзя); слабее база В-66 — сильнее набор
# «старых стен»: SYMS=… DAYS=… и флаги `--min-age-secs 2700 --side bid --stop-form pct2`
# через окружение скрипт не принимает, такой набор гоняется руками (см. отчёт F3).
#
# Почему не `md5` файла целиком: колонки растут по задачам (F3: `n_fill_by_cross` +
# `queue=`/`paths=` в шапке; F4: `fill_frac/entry_vwap/legs_filled/legs_rejected` в
# rounds и `n_rejected_postonly` в forms), поэтому сравниваются **все прежние
# колонки** обоих файлов построчно, а новые печатаются отдельно.
# Старому бинарнику `--no-post-only` не передаётся (флага у него нет), новому —
# передаётся: у F4 умолчание входа «пост-онли», гейт требует прежнего режима.
set -euo pipefail
cd /opt/alpha-compute || exit 1
NEW="${1:?новый бинарник: bin/alpha-<hash>}"
OLD="${2:-bin/alpha-2ee7d02}"
[ -x "$NEW" ] || { echo "$NEW: не исполняемый файл" >&2; exit 1; }
[ -x "$OLD" ] || { echo "$OLD: не исполняемый файл" >&2; exit 1; }
SYMS="${SYMS:-1000BONKUSDT 1000PEPEUSDT AAVEUSDT ADAUSDT ATOMUSDT}"
DAYS="${DAYS:---day 2026-09-16 --day 2026-09-17 --day 2026-09-18}"
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000"
P95="--p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
COMMON="--root root $RTT $P95 --order-qty-from-pool --threads ${THREADS:-3}"
COMMON="$COMMON --h3-mode notional --h3-usd 10000 --min-flow-pct 100"
COMMON="$COMMON --stop-form pct1 --take-form 1to1 $DAYS"
for s in $SYMS; do COMMON="$COMMON --symbol $s"; done
rm -rf b5/f3-gate-old b5/f3-gate-new
echo "== старый $OLD (флагов --queue-model/--no-post-only ещё нет)"
# shellcheck disable=SC2086
"$OLD" lob bounce-grid $COMMON --out-dir b5/f3-gate-old
echo "== новый $NEW --queue-model risk-adverse --no-post-only"
# shellcheck disable=SC2086
"$NEW" lob bounce-grid $COMMON --queue-model risk-adverse --no-post-only --out-dir b5/f3-gate-new

echo "== rounds.csv и forms.csv: прежние колонки по именам"
# Байтового дифа нет по построению: F3 добавила `queue=`/`paths=` в шапку и колонку
# `n_fill_by_cross` в forms, F4 — `fill_frac/entry_vwap/legs_filled/legs_rejected`
# в rounds и `n_rejected_postonly` в forms. Сравниваются все прежние колонки построчно.
python3 - <<'PY'
import csv

def rows(path):
    with open(path, encoding='utf-8') as f:
        r = csv.reader(line for line in f if not line.startswith('#'))
        head = next(r)
        return head, list(r)

def compare(name):
    h_old, r_old = rows(f'b5/f3-gate-old/{name}')
    h_new, r_new = rows(f'b5/f3-gate-new/{name}')
    missing = [c for c in h_old if c not in h_new]
    assert not missing, f'новый {name} потерял колонки: {missing}'
    i_old = {c: i for i, c in enumerate(h_old)}
    i_new = {c: i for i, c in enumerate(h_new)}
    assert len(r_old) == len(r_new), (name, len(r_old), len(r_new))
    bad = [
        (a[:3], {c: (a[i_old[c]], b[i_new[c]]) for c in h_old
                 if a[i_old[c]] != b[i_new[c]]})
        for a, b in zip(r_old, r_new)
        if any(a[i_old[c]] != b[i_new[c]] for c in h_old)
    ]
    assert not bad, f'{name}: прежние колонки разошлись: {bad[:2]}'
    print(f'{name}: OK, все {len(h_old)} прежних колонок совпали ({len(r_new)} строк)')
    return h_new, r_new

_, r_rounds = compare('rounds.csv')
h_forms, r_forms = compare('forms.csv')
i_new = {c: i for i, c in enumerate(h_forms)}
cross = sum(int(b[i_new['n_fill_by_cross']]) for b in r_forms)
print(f'кругов {len(r_rounds)}, форм-строк {len(r_forms)}, крестов (путь 3) у risk-adverse {cross}')
PY

echo "== md5 для протокола (шапки и новые колонки отличаются по построению)"
md5sum b5/f3-gate-old/rounds.csv b5/f3-gate-new/rounds.csv \
       b5/f3-gate-old/forms.csv b5/f3-gate-new/forms.csv
echo "== гейт F3 пройден"
