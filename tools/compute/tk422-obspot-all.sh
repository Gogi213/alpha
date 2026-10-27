# TK-422 возврат v3 п.2: минутные срезы стакана СПОТА — Gate (gate-spot, 226 инстр.) и Bybit (bybit-spot, 17), все часы/сутки TK-415.
# На calc под alsched. Выходы: obgate-spot/min, ob200-spot/min (отдельно от фьючерсных — ключ стакана = биржа×рынок×инструмент).
cd /data/tk0393/tk414
rm -f obgate/spot.rc
python3 - <<'PY' > obgate/need-spot.log 2>&1 || { echo $? > obgate/spot.rc; exit 1; }
import os, re
root = "/data/tk415/ob/gate/gate-spot"
n = skip = 0
with open("obgate/need-spot.tsv", "w") as w:
    for s in sorted(os.listdir(root)):
        for f in sorted(os.listdir(root + "/" + s)):
            m = re.match(r"^(.+)-(\d{10})\.csv\.gz$", f)
            if not m:
                continue
            if os.path.exists("obgate-spot/min/%s/%s.csv" % (s, m.group(2))):
                skip += 1
                continue
            w.write("%s/%s/%s\t%s/%s.csv\n" % (root, s, f, s, m.group(2)))
            n += 1
print("часов к нарезке", n, "уже есть", skip)
PY
python3 tk414-obmin.py /data/tk415/ob/bybit/bybit-spot ob200-spot/min 15 > obgate/obmin-bybit-spot.log 2>&1 || { echo $? > obgate/spot.rc; exit 1; }
python3 tk414-obmin-gate.py obgate/need-spot.tsv obgate-spot/min 15 > obgate/obmin-spot.log 2>&1
r=$?; echo $r > obgate/spot.rc
[ $r = 0 ] && bash /data/tk0422/run9.sh
