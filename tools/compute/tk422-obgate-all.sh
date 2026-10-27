# TK-422 п.3 возврата: минутные срезы стакана Gate по ВСЕМ часам gate-fut (TK-415), а не только по часам событий TK-414.
# На calc под alsched. Готовые срезы (22 инструмента сентября) пропускаются (tk414-obmin-gate.py: «есть»), выход тот же каталог.
cd /data/tk0393/tk414
rm -f obgate/all.rc
python3 - <<'PY' > obgate/need-all.log 2>&1 || { echo $? > obgate/all.rc; exit 1; }
import os, re
root = "/data/tk415/ob/gate/gate-fut"
n = skip = 0
with open("obgate/need-all.tsv", "w") as w:
    for s in sorted(os.listdir(root)):
        for f in sorted(os.listdir(root + "/" + s)):
            m = re.match(r"^(.+)-(\d{10})\.csv\.gz$", f)
            if not m:
                continue
            if os.path.exists("obgate/min/%s/%s.csv" % (s, m.group(2))):
                skip += 1
                continue
            w.write("%s/%s/%s\t%s/%s.csv\n" % (root, s, f, s, m.group(2)))
            n += 1
print("часов к нарезке", n, "уже есть", skip)
PY
python3 tk414-obmin-gate.py obgate/need-all.tsv obgate/min 15 > obgate/obmin-all.log 2>&1
echo $? > obgate/all.rc
