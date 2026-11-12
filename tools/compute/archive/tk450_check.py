#!/usr/bin/env python3
"""TK-450: проверка сдачи свечей Binance USDⓈ-M 1m. usage: python tools/compute/tk450_check.py  (из репо; по ssh на calc)

1) число .csv.gz == строк map.csv с символом; 2) verify.csv: все ok=1; 3) сумма минут/пропусков coverage.csv есть в отчёте;
4) строки BTC/ICP/PUMP/1000PEPE в .csv.gz == минутам coverage.csv, t_open_ms строго возрастает.
То же, что tk450-check.sh, но без bash на стороне запускающего (на Windows диспетчера bash = WSL).
"""
import pathlib
import subprocess
import sys

REP = pathlib.Path(__file__).resolve().parents[2] / "docs/findings/binance-klines-1m-2026-10-30.md"
SYMS = ["BTCUSDT", "ICPUSDT", "PUMPUSDT", "1000PEPEUSDT"]

REMOTE = r"""cd /srv/alpha-sda/candles-1m/binance-um
echo "files=$(ls *.csv.gz | wc -l)"
awk -F, 'NR>1 && $2!=""{n++} END{print "mapsym=" n}' map.csv
awk -F, 'NR>1{t++; if($4+0!=1)b++} END{print "verify_rows=" t; print "verify_bad=" b+0}' verify.csv
awk -F, 'NR>1{m+=$4;g+=$5} END{print "minutes=" m; print "gaps=" g}' coverage.csv
for s in %s; do
  awk -F, -v s=$s '$2==s{print "cov_" s "=" $4}' coverage.csv
  zcat $s.csv.gz | awk -F, -v s=$s 'NR>1{n++; if(n>1 && $1+0<=p)b++; p=$1+0} END{print "rows_" s "=" n; print "order_bad_" s "=" b+0}'
done
""" % " ".join(SYMS)


def sp(n):
    return f"{int(n):,}".replace(",", " ")


def main():
    # байты, а не text=True: на Windows текстовый режим даёт CRLF и ломает bash на calc
    r = subprocess.run(["ssh", "calc", "bash", "-s"], input=REMOTE.encode(), capture_output=True)
    out, err = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        print(f"FAIL ssh calc: rc {r.returncode}\n{err.strip()}")
        print("TK-450 CHECK: FAIL")
        return 1
    print(out.strip())
    v = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    rep = REP.read_text(encoding="utf-8")
    fail = 0

    def ok(msg):
        print("ok  ", msg)

    def bad(msg):
        nonlocal fail
        fail = 1
        print("FAIL", msg)

    if v.get("files") == v.get("mapsym"):
        ok(f"csv.gz {v.get('files')} == монет с символом в map.csv")
    else:
        bad(f"csv.gz {v.get('files')} != map {v.get('mapsym')}")
    if v.get("verify_bad") == "0":
        ok(f"verify.csv: {v.get('verify_rows')} zip, расхождений 0")
    else:
        bad(f"verify.csv расхождений {v.get('verify_bad')}")
    for key, name in (("minutes", "минут"), ("gaps", "пропусков")):
        val = v.get(key)
        if val and sp(val) in rep:
            ok(f"{name} {val} ({sp(val)}) есть в отчёте")
        else:
            bad(f"{name} {val} нет в отчёте")
    for s in SYMS:
        rows, cov, ob = v.get(f"rows_{s}"), v.get(f"cov_{s}"), v.get(f"order_bad_{s}")
        if rows is not None and rows == cov and ob == "0":
            ok(f"{s}: строк {rows} == coverage, порядок ок")
        else:
            bad(f"{s}: строк {rows} vs coverage {cov}, нарушений порядка {ob}")
    print("TK-450 CHECK: PASS" if not fail else "TK-450 CHECK: FAIL")
    return fail


if __name__ == "__main__":
    sys.exit(main())
