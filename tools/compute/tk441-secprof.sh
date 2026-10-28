#!/bin/bash
# TK-441: время tk422-features по блокам features() (метки на строках "    # --- "), UAIUSDT (334 окна), 1 процесс, копия кода с _tk(); выход байт-в-байт должен совпасть с эталоном.
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 15m --name tk441-secprof -- bash /data/tk0441/tk441-secprof.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0441/secprof
rm -rf $O; mkdir -p $O
cp /data/tk0439/f422prof2/win.csv $O/win.csv
$PY - <<'PYEOF'
import re
src = open("/data/tk0439/tk422-features-seq.py", encoding="utf-8").read().split("\n")
out = []
inf = False
hdr = ("import time as _t, atexit as _ae\n_TT = {}\n_TL = [None, None]\n"
       "def _tk(l):\n    n = _t.perf_counter()\n    if _TL[1] is not None:\n        _TT[_TL[0]] = _TT.get(_TL[0], 0) + n - _TL[1]\n    _TL[0], _TL[1] = l, n\n"
       "def _dump(): _tk('end'); open('/data/tk0441/secprof/sec.txt', 'w').write(chr(10).join('%8.3f  %s' % (v, k) for k, v in sorted(_TT.items(), key=lambda x: -x[1])))\n"
       "_ae.register(_dump)\n")
done_hdr = False
for ln in src:
    if not done_hdr and ln.startswith("import ") :
        pass
    if ln.startswith("def features("):
        inf = True
        out.append(ln)
        out.append("    _tk('start')")
        continue
    if inf and ln.startswith("def "):
        inf = False
    if inf and ln.startswith("    # --- "):
        out.append("    _tk(%r)" % ln.strip()[:60])
    if inf and ln.startswith("    return F"):
        out.append("    _tk('return')")
    out.append(ln)
txt = "\n".join(out)
i = txt.index("\n_cc = {}")
txt = txt[:i] + "\n" + hdr + txt[i:]
open("/data/tk0441/secprof/f.py", "w", encoding="utf-8").write(txt)
PYEOF
s=$(date +%s.%N)
$PY $O/f.py $O/win.csv $O/new.csv --procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/new.log 2> $O/new.err
e=$(date +%s.%N)
echo "wall $(echo "$e - $s" | bc)" > $O/time.txt
cmp $O/new.csv /data/tk0439/f422ab2/new.csv >/dev/null 2>&1 && echo SAME >> $O/time.txt || echo DIFF >> $O/time.txt
echo 0 > $O/done
