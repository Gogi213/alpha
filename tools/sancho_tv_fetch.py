"""Скачивает снимки TradingView по ссылкам из data/sancho-trades-skeleton.csv в data/sancho-tv/ (+ sha256 в manifest)."""
import csv, hashlib, os, sys, urllib.request, time
src = sys.argv[1] if len(sys.argv) > 1 else "data/sancho-trades-skeleton.csv"
out = "data/sancho-tv"; os.makedirs(out, exist_ok=True)
rows = list(csv.DictReader(open(src, encoding="utf-8")))
man = []
for r in rows:
    for l in filter(None, r["tv_links"].split("|")):
        sid = l.rstrip("/").split("/")[-1]
        fn = f"{out}/{sid}.png"
        st = "ok"
        if not os.path.exists(fn):
            url = f"https://s3.tradingview.com/snapshots/{sid[0].lower()}/{sid}.png"
            try:
                b = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
                open(fn, "wb").write(b)
            except Exception as e:
                st = f"fail:{e}"
            time.sleep(0.2)
        sha = hashlib.sha256(open(fn, "rb").read()).hexdigest() if os.path.exists(fn) else ""
        man.append((r["trade_id"], sid, st, sha))
with open("data/sancho-tv-manifest.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["trade_id", "snap_id", "status", "sha256"]); w.writerows(man)
print(len(man), sum(1 for m in man if m[2] == "ok"))
