#!/usr/bin/env bash
# TK-315 П-17 F2 Г-81: проба меры ворот для НОВЫХ окон W (F2: 16,17,18,19,22,24,26,28,34 с), та же процедура, что tk279-g81-probe.sh
# (8 W F1, 251 сутки, 6,7 ч на 3 ядрах): клетки cxlprobe<W> на множестве B1, entry_gate=cxl<W>qprobe (порог недостижим — ход B1 прежний),
# gate-probe.csv на сутки; PGO r2, --tape-log 300 (пачка cxl). Дальше: p17-g81-thresholds.py (QS расширен под F2) → пороги Q_m(q,W).
#   tk315-g81-probe.sh   env: NX (=ядра, умолч. 15), GT (1), WS (окна), R (выход, /data/tk0315/p9), DAYS (/data/tk0279/days9.txt)
[ -f /data/tk0279/pgo.ok ] || { echo "PGO не готов (нет /data/tk0279/pgo.ok) - пропуск, подать заново"; exit 75; }
export REASON="TK-315 П-17 F2 проба меры Г-81 (9 новых W), pgo" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=${NX:-15} GT=${GT:-1}
R=${R:-/data/tk0315/p9}; DAYS=${DAYS:-/data/tk0279/days9.txt}; WS=${WS:-"16 17 18 19 22 24 26 28 34"}; S=$SCRIPTS; SRC=/data/tk0231/plan
mkdir -p "$R/plan" "$R/out"; cp "$DAYS" "$R/days.txt"
# дельта: сутки без done убиты (OOM) — claim остался бы и day-cells.sh молча пропустил (exit 0); готовые не трогаем
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
: > "$R/plan/L.cells"; : > "$R/plan/L.args"
for ym in $(awk '{print substr($2,1,7)}' "$R/days.txt" | sort -u); do
  : > "$R/plan/L-$ym.cells"; { echo "--entry-form ladder3x0..0.0409sw2"; echo "--entry-ttl-secs 1800"; echo "--exit-form chase1510"; } > "$R/plan/L-$ym.args"
  for w in $WS; do
    echo "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800-chase1510 cxlprobe$w" >> "$R/plan/L-$ym.cells"
    echo "--set cxlprobe$w:age=2700,side=bid,btc4h_max=-44.55,entry_gate=cxl${w}qprobe" >> "$R/plan/L-$ym.args"
  done
  grep -vE '^--(set|exit-form|entry-form|entry-ttl-secs|early-exit-secs)' "$SRC/p14f1.args" | sort -u >> "$R/plan/L-$ym.args"
done
export PLAN=L P=$R/plan O=$R/out TP=1
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$S" < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
