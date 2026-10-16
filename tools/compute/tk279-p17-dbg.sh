#!/usr/bin/env bash
# TK-279 п.5 отладка: смоук Г-135 дал 0 сигналов во всех 45 клетках при живом swept_lots в касаниях — три набора на postsweep10:
#   dbg_all (без swept_min), dbg_s1 (swept_min=1), dbg_s0 (swept_min=0); журнал jrn-s5 (новые подходы), 1 сутки 2026-03-16, PGO r2.
[ -f /data/tk0279/pgo.ok ] || { echo "PGO не готов"; exit 75; }
W=/data/tk0279; PL=$W/dbgplan; R=$W/dbg; S=/data/p17s5b-none
rm -rf "$PL" "$R"; mkdir -p "$PL" "$R"
grep -v '^--set' $W/p17s5b/plan/L-2026-03.args > "$PL/L-2026-03.args"
: > "$PL/L.args"; : > "$PL/L.cells"
b=age=2700,side=bid,btc4h_max=-44.55
{ echo "--set dbg_all:$b"; echo "--set dbg_s1:$b,swept_min=1"; echo "--set dbg_s0:$b,swept_min=0"; } >> "$PL/L-2026-03.args"
printf 'postsweep10-pct2-tr1x1-14400-ttl1800 dbg_all\npostsweep10-pct2-tr1x1-14400-ttl1800 dbg_s1\npostsweep10-pct2-tr1x1-14400-ttl1800 dbg_s0\n' > "$PL/L-2026-03.cells"
echo "mar 2026-03-16" > $W/dbg-days.txt
export REASON="TK-279 отладка Г-135 (0 сигналов)" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=1 GT=${GT:-3} PLANSRC=$PL JRN=$W/jrn-s5 ALT_JRN=
bash $W/p17-count.sh "$R" $W/dbg-days.txt
