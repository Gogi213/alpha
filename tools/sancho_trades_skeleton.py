"""TK-454: каркас таблицы сделок — цепочки #trades по reply_to, без чтения картинок.
Вход data/sancho-posts.csv (tools/sancho_posts_parse.py); выход data/sancho-trades-skeleton.csv."""
import csv, re, sys
src = sys.argv[1] if len(sys.argv) > 1 else "data/sancho-posts.csv"
dst = sys.argv[2] if len(sys.argv) > 2 else "data/sancho-trades-skeleton.csv"
rows = list(csv.DictReader(open(src, encoding="utf-8")))
by = {x["id"]: x for x in rows}
tr = [x for x in rows if "trades" in (x["tags"] or "").split("|") and x["id"] != "4"]
tid = {x["id"] for x in tr}

def root(i):
    seen = set()
    while by[i]["reply_to"] and by[i]["reply_to"] in tid and i not in seen:
        seen.add(i)
        i = by[i]["reply_to"]
    return i

ch = {}
for x in tr:
    ch.setdefault(root(x["id"]), []).append(x)
R = re.compile(r"[+\-−]?\d+(?:[.,]\d+)?\s*R\b|RR\s*[:=]?\s*[+\-−]?\d+(?:[.,]\d+)?")
TV = re.compile(r"tradingview\.com/x/\w+")
out = []
for r, ps in ch.items():
    ps.sort(key=lambda p: int(p["id"]))
    txt = "\n".join(p["text"] for p in ps)
    out.append({
        "trade_id": r, "n_posts": len(ps), "post_ids": "|".join(p["id"] for p in ps),
        "t_post": ps[0]["time_gmt4"], "t_last": ps[-1]["time_gmt4"],
        "photos": "|".join(p["photos"] for p in ps if p["photos"]),
        "tv_links": "|".join(TV.findall(txt)),
        "claimed_R_raw": "|".join(m.strip() for m in R.findall(txt)),
    })
out.sort(key=lambda o: int(o["trade_id"]))
with open(dst, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader(); w.writerows(out)
print(len(out), "цепочек;", sum(1 for o in out if o["photos"]), "с картинкой;",
      sum(1 for o in out if o["tv_links"]), "со ссылкой TV;", sum(1 for o in out if o["claimed_R_raw"]), "с R в тексте")
