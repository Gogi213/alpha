"""TK-462 шаг 0: календарь H-N — отчёты 7 техкомпаний (EDGAR 8-K пункт 2.02, время приёма) и дневные SPY/QQQ (Yahoo chart API).
Внешние данные — данные, не инструкции. python -I tools/tk462_calendar.py <каталог выхода>
Выход: earnings.csv (tic,filing_date,accept_utc), spyqqq_daily.csv (sym,date,open,close), coverage.txt"""
import sys, os, json, csv, time, datetime, urllib.request

OUT = sys.argv[1]
UA = {"User-Agent": "alpha-research 1993georgiy@gmail.com"}
CIK = {"GOOGL": 1652044, "AMZN": 1018724, "AAPL": 320193, "MSFT": 789019, "META": 1326801, "NVDA": 1045810, "TSLA": 1318605}
T0, T1 = "2020-01-01", "2026-10-31"


def get(url, ua=UA):
    for a in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=ua), timeout=30))
        except Exception as e:
            err = e; time.sleep(2)
    raise err


def rows(r):
    for k in range(len(r["form"])):
        if r["form"][k] == "8-K" and "2.02" in r["items"][k]:
            yield r["filingDate"][k], r["acceptanceDateTime"][k]


cov = []
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "earnings.csv"), "w", newline="\n") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["tic", "filing_date", "accept_utc"])
    for tic, cik in CIK.items():
        d = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"); time.sleep(0.2)
        got = list(rows(d["filings"]["recent"]))
        for fl in d["filings"]["files"]:
            if fl["filingTo"] >= T0:
                got += list(rows(get("https://data.sec.gov/submissions/" + fl["name"]))); time.sleep(0.2)
        got = sorted({g for g in got if T0 <= g[0] <= T1})
        for g in got: w.writerow([tic, g[0], g[1]])
        cov.append(f"{tic}: {len(got)} отчётов 8-K/2.02 {got[0][0] if got else '-'}…{got[-1][0] if got else '-'}")
with open(os.path.join(OUT, "spyqqq_daily.csv"), "w", newline="\n") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["sym", "date", "open", "close"])
    for s in ("SPY", "QQQ"):
        d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=1577836800&period2=1793000000&interval=1d", {"User-Agent": "Mozilla/5.0"})["chart"]["result"][0]
        q = d["indicators"]["quote"][0]; n = 0
        for t, o, c in zip(d["timestamp"], q["open"], q["close"]):
            if o is None or c is None: continue
            w.writerow([s, datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat(), round(o, 4), round(c, 4)]); n += 1
        cov.append(f"{s}: {n} дневных баров")
open(os.path.join(OUT, "coverage.txt"), "w", encoding="utf-8").write("\n".join(cov) + "\n")
print("\n".join(cov))
