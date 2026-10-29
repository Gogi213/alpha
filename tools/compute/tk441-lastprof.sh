#!/bin/bash
# TK-441 возврат 3 (30.10): два замера без правки кода счёта, копии с метками времени.
#   A) tk422-features (new6.py + TK422_NUMBA=1), срез 677 окон, 1 процесс: сколько features() уходит на rets/sub/pair_corr(+corr)/fractals/seq/hl — потолок переписывания на колонки;
#   B) controls (tk433-features.py windows), 15 процессов, два прогона подряд (второй — тёплый кэш): этапы setup / пул / слияние / запись и внутри задания load / inst_arrays / own+sf / отбор кандидатов.
#   python3 /data/sched/alsched.py submit --kind work --name tk441-lastprof --cores 15 --max-runtime 15m --est 4m --mem 40 -- bash /data/tk0441/tk441-lastprof.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0441/nbcache
C=/data/tk0436/cube3
O=/data/tk0441/lastprof
rm -rf $O; mkdir -p $O
$PY - <<'PYEOF'
O = "/data/tk0441/lastprof"
# --- A: tk422-features с таймерами функций
s = open("/data/tk0441/new6.py", encoding="utf-8").read()
hdr = (
    "import time as _t, atexit as _ae" + chr(10) +
    "_TT = {}" + chr(10) +
    "def _w(name, f):" + chr(10) +
    "    def g(*a, **k):" + chr(10) +
    "        t = _t.perf_counter()" + chr(10) +
    "        try:" + chr(10) +
    "            return f(*a, **k)" + chr(10) +
    "        finally:" + chr(10) +
    "            x = _TT.setdefault(name, [0.0, 0]); x[0] += _t.perf_counter() - t; x[1] += 1" + chr(10) +
    "    return g" + chr(10) +
    "for _n in ('rets', 'sub', 'pair_corr', 'corr', 'fractals', 'seq', 'hl', 'features', 'candles_cube', 'orient', 'trades_min_cube'):" + chr(10) +
    "    if _n in globals(): globals()[_n] = _w(_n, globals()[_n])" + chr(10) +
    "def _dump():" + chr(10) +
    "    open('" + O + "/a-tm.txt', 'w').write(chr(10).join('%9.3f %9d  %s' % (v[0], v[1], k) for k, v in sorted(_TT.items(), key=lambda x: -x[1][0])))" + chr(10) +
    "_ae.register(_dump)" + chr(10))
i = s.index("def main(")
open(O + "/a.py", "w", encoding="utf-8").write(s[:i] + hdr + chr(10) + s[i:])
# --- B: controls с метками этапов
s = open("/data/tk0436/tk433-features.py", encoding="utf-8").read()
def rep(a, b):
    global s
    assert s.count(a) == 1, a
    s = s.replace(a, b)
nl = chr(10)
rep("def proc_instr(item):", "import time as _t" + nl + "_pc = _t.perf_counter" + nl + "_T0 = [_pc()]" + nl + "def _L(m):" + nl + "    n = _pc(); sys.stderr.write('LBL %-8s %8.2f' % (m, n - _T0[0]) + chr(10)); _T0[0] = n" + nl + nl + "def proc_instr(item):")
rep("    d = load(roots, sub, sym)" + nl, "    _s = _pc()" + nl + "    d = load(roots, sub, sym)" + nl + "    _tl = _pc() - _s" + nl)
rep("    ar = inst_arrays(d, mg)" + nl, "    _s2 = _pc()" + nl + "    ar = inst_arrays(d, mg)" + nl + "    _ta = _pc() - _s2" + nl + "    _to = 0.0" + nl)
rep("        o = own_features(d, a, e, bz, nz)" + nl + "        o.update(sf_features(d, dsp, a, e, int(sg)))" + nl,
    "        _x = _pc()" + nl + "        o = own_features(d, a, e, bz, nz)" + nl + "        o.update(sf_features(d, dsp, a, e, int(sg)))" + nl + "        _to += _pc() - _x" + nl)
rep("            oc = own_features(d, s, s + dur, bz, nz)" + nl + "            oc.update(sf_features(d, dsp, s, s + dur, int(sg)))" + nl,
    "            _x = _pc()" + nl + "            oc = own_features(d, s, s + dur, bz, nz)" + nl + "            oc.update(sf_features(d, dsp, s, s + dur, int(sg)))" + nl + "            _to += _pc() - _x" + nl)
rep("    return wins, own, diag" + nl, "    _tt = _pc() - _s" + nl + "    open('" + O + "/b-tm.txt', 'a').write('%.4f %.4f %.4f %.4f %d' % (_tt, _tl, _ta, _to, len(L)) + chr(10))" + nl + "    return wins, own, diag" + nl)
rep("    items = sorted(byins.items())" + nl, "    _L('setup')" + nl + "    items = sorted(byins.items())" + nl)
rep("    for w_, o_, d_ in parts:" + nl, "    _L('pool')" + nl + "    for w_, o_, d_ in parts:" + nl)
rep('    with open(os.path.join(outd, "windows.csv")', "    _L('merge')" + nl + '    with open(os.path.join(outd, "windows.csv")')
rep("    diag = {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in diag.items()}" + nl, "    _L('write')" + nl + "    diag = {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in diag.items()}" + nl)
open(O + "/b.py", "w", encoding="utf-8").write(s)
PYEOF
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube $C --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
# B первым: прогрев куба, затем второй прогон = тёплый
for r in 1 2; do
  rm -f $O/b-tm.txt; mkdir -p $O/f$r
  s=$(date +%s.%N)
  $PY $O/b.py windows smp/legs-2026-09.csv smp/legs-2026-09.csv - - $O/f$r 2026-09 --cube $C --cut 29759040 --jobs 15 > $O/b$r.log 2> $O/b$r.err
  e=$(date +%s.%N); echo "b$r wall $(echo "$e - $s" | bc)" >> $O/time.txt
  awk '{a+=$1; b+=$2; c+=$3; d+=$4; n+=$5; k++} END {printf "b'$r' задания=%d событий=%d сумма_ядро_с=%.1f load=%.1f inst_arrays=%.1f own+sf=%.1f остаток(отбор)=%.1f\n", k, n, a, b, c, d, a-b-c-d}' $O/b-tm.txt >> $O/time.txt
done
for f in windows.csv own.csv controls-diag.json; do cmp /data/tk0433/v11f/f-2026-09/$f $O/f2/$f >/dev/null 2>&1 && echo "OK $f" >> $O/time.txt || echo "DIFF $f" >> $O/time.txt; done
s=$(date +%s.%N)
TK422_NUMBA=1 $NB $O/a.py /data/tk0436/f422prof/win.csv $O/a.csv $A > $O/a.log 2> $O/a.err
e=$(date +%s.%N); echo "a wall $(echo "$e - $s" | bc)" >> $O/time.txt
cmp /data/tk0436/f422prof/new.csv $O/a.csv >/dev/null 2>&1 && echo "OK a.csv" >> $O/time.txt || echo "DIFF a.csv" >> $O/time.txt
echo 0 > $O/done
