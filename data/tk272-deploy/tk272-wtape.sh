#!/usr/bin/env bash
# TK-272 (Г-112, 16 окон W, Судья 17.10 23:43; В-170): ОДИН проход multi-W (alpha-tk272d-pgo --tape-log-ws, колонки tape_press_lots_w<W>; гейт
# 41 md5 + колонки W=5…60 побитно, 18.10) по сделкам B1 всех 251 суток -> /data/p12r3a/wm16/<сутки>; пороги Г-112 v11 = v10 + чтение колонки
# tape_press_lots_w<W> (старые W — те же ленты) -> /data/p12r3a/thr11; v10 побитно внутри v11 (кроме новых W: ключи e112_W<новое>_*).
# e116 (cxl) для новых W не считается (в multi-W нет). rc: 1 правка v11 · 3 сутки сбой/неполно · 14 пороги v11 · 15 v10 не побитно
# env: D=3 (суток параллельно) GT=1 (потоков на сутки)
set -uo pipefail
T=/data/tk0272; cd $T; D=${D:-3}; export GT=${GT:-1}
WS=9,11,12,14,19,22,26,38,45,52,75,90,105,165,210,255
export TL=wm16 BIN=/opt/alpha-compute/bin/alpha-tk272d-pgo
sed 's#O=/data/p12r3a/w$TL/$d#O=/data/p12r3a/wm16/$d#; s#--tape-log $TL#--tape-log-ws '$WS'#' /data/tk0115/tk120-thr-w-day.sh > $T/wtape-day.sh
grep -q 'tape-log-ws '$WS $T/wtape-day.sh && grep -q 'O=/data/p12r3a/wm16/' $T/wtape-day.sh || exit 1
awk -F, 'NR>1{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0272/wtape-day.sh "$0" "$1"'
n=$(cat /data/p12r3a/wm16/*/fail.txt 2>/dev/null | wc -l); d=$(ls /data/p12r3a/wm16/*/done 2>/dev/null | wc -l)
echo "wm16: done=$d fail=$n" > $T/thr11w.out
[ "$n" = 0 ] && [ "$d" = 251 ] || exit 3
python3 - <<'P' || exit 1
s = open("/data/tk0272/p12-r2b-thresholds-v10.py", encoding="utf-8").read()
a = '''def read_lots(root, mo):   # (symbol, t0_ms) -> (tape_press_lots, cxl_lots) месяца mo'''
b = '''def read_lots(root, mo, wcol=None):   # (symbol, t0_ms) -> (tape_press_lots, cxl_lots) месяца mo; wcol: колонка multi-W tape_press_lots_w<W> (cxl нет -> None)'''
c = '''                out.setdefault((r["symbol"], int(r["t0_ns"]) // 1000000), (float(r["tape_press_lots"]), float(r["cxl_lots"])))'''
d = '''                out.setdefault((r["symbol"], int(r["t0_ns"]) // 1000000), (float(r["tape_press_lots"]), float(r["cxl_lots"])) if wcol is None else (float(r[wcol]), None))'''
e = '''    ex = {w: read_lots(root, mo) for w, root in EXTRA.items()}'''
f = '''    ex = {w: read_lots(root, mo, f"tape_press_lots_w{w}" if root.rstrip("/").endswith("/wm16") else None) for w, root in EXTRA.items()}'''
g = '''                            d[f"cxl{w}"].append(ex[w][k][1] / cand[0][1])'''
h = '''                            if ex[w][k][1] is not None:
                                d[f"cxl{w}"].append(ex[w][k][1] / cand[0][1])'''
for x, y in ((a, b), (c, d), (e, f), (g, h)):
    assert s.count(x) == 1, x
    s = s.replace(x, y)
open("/data/tk0272/p12-r2b-thresholds-v11.py", "w", encoding="utf-8").write(s)
P
A=(); for w in 5 6 8 10 15 60 120 300; do A+=("$w=/data/p12r3a/w$w"); done
for w in ${WS//,/ }; do A+=("$w=/data/p12r3a/wm16"); done
python3 $T/p12-r2b-thresholds-v11.py /data/p12r3a/b1keys-cap0.csv /data/tk0115/delta/w /data/tk064/pool /data/p12r3a/thr11 "${A[@]}" || exit 14
python3 - <<'P' >> $T/thr11w.out 2>&1 || exit 15
import json
a, b = json.load(open("/data/p12r3a/thr10/summary.json"))["summary"], json.load(open("/data/p12r3a/thr11/summary.json"))["summary"]
bm = {r["month"]: r for r in b}
bad = [(r["month"], k) for r in a for k, v in r.items() if k != "ex_missing" and bm[r["month"]].get(k) != v]
print("v10 значений", sum(len(r) for r in a), "расхождений", len(bad), bad[:3])
nw = [k for k in b[0] if "_W" in k and any(f"_W{w}_" in k for w in (9, 11, 12, 14, 19, 22, 26, 38, 45, 52, 75, 90, 105, 165, 210, 255))]
print("ключей новых W", len(nw), "ex_missing по месяцам", [r["ex_missing"] for r in b])
raise SystemExit(1 if bad else 0)
P
echo ok >> $T/thr11w.out
