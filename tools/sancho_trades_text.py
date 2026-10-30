"""TK-454: извлечение текстовых полей по цепочкам #trades (правила по словам, без домысла; цитаты дословно).
Запуск: python tools/sancho_trades_text.py data/sancho-posts.csv docs/findings/sancho-trades-text-<дата>.csv"""
import csv, re, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
by = {x["id"]: x for x in rows}
tr = [x for x in rows if "trades" in (x["tags"] or "").split("|") and x["id"] != "4"]
tid = {x["id"] for x in tr}
def root(i):
    s = set()
    while by[i]["reply_to"] and by[i]["reply_to"] in tid and i not in s:
        s.add(i); i = by[i]["reply_to"]
    return i
ch = {}
for x in tr: ch.setdefault(root(x["id"]), []).append(x)
TVR = re.compile(r"https?://\S*tradingview\.com/x/\w+/?")
RR = re.compile(r"(?:RR|R:R)\s*[:=]?\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*R\b|\bRR\s*1\s*[кk]\s*\d+|1\s*к\s*\d+")
RISK = re.compile(r"[^.\n]*(?:риск|депозит|капитал)[^.\n]*%[^.\n]*|[^.\n]*%[^.\n]*(?:риск|депозит|капитал)[^.\n]*", re.I)
def q(rx, t): return " ¦ ".join(dict.fromkeys(m.group(0).strip() for m in rx.finditer(t)))
out = []
for r, ps in ch.items():
    ps.sort(key=lambda p: int(p["id"]))
    t = "\n".join(TVR.sub("[TV]", p["text"]) for p in ps)
    low = t.lower()
    n_inst = len(re.findall(r"^\s*(?:\d️⃣|\d\.)\s", t, re.M)) + len(re.findall(r"^\s*(?:\d️⃣)", t, re.M)) * 0
    closed = bool(re.search(r"закрыт|закрытие|отработ|профит|profit|зафиксирована сделка", low))
    part = bool(re.search(r"фиксир|частично|част[ьи] (?:от )?позиции|d+% (?:от )?позиции", low))
    be = bool(re.search(r"безубы|безуб", low))
    stop = bool(re.search(r"\bстоп(?!\s*(?:в|перен|лосс перен))|стоп-лосс|выбил|ликвид", low))
    cur = bool(re.search(r"текущая позиция|сейчас|upd|начало отработки|первичная отработка", low))
    plan = bool(re.search(r"ожидаю|потенциальн|план|жду|ждать|рассмотр", low))
    rr = q(RR, t)
    if n_inst >= 2: cl = "несколько сделок в цепочке: исход не разделён"
    elif be and (part or cur): cl = "частичная фиксация/БУ"
    elif be: cl = "БУ"
    elif part: cl = "частичная фиксация"
    elif closed and rr: cl = "закрыта/отработка с заявленным RR"
    elif closed: cl = "закрыта/отработка без RR"
    elif cur: cl = "открыта (текущая позиция)"
    elif plan: cl = "план/комментарий"
    else: cl = "не названо"
    if stop and cl == "не названо": cl = "упомянут стоп: проверить вручную"
    out.append({"trade_id": r, "post_ids": "|".join(p["id"] for p in ps), "n_posts": len(ps),
                "t_post_gmt4": ps[0]["time_gmt4"][:16], "t_last_gmt4": ps[-1]["time_gmt4"][:16],
                "has_img": int(any(p["photos"] for p in ps)), "n_tv": len(re.findall(r"\[TV\]", t)),
                "class_claim_rule": cl, "claim_rr_quote": rr, "words": ",".join(k for k, v in (("закрыт", closed), ("фикс", part), ("БУ", be), ("стоп", stop), ("текущ", cur), ("план", plan)) if v),
                "risk_quote": q(RISK, t)[:300], "n_instr_list": n_inst})
w = csv.DictWriter(open(sys.argv[2], "w", newline="", encoding="utf-8"), fieldnames=list(out[0]))
w.writeheader(); w.writerows(out)
from collections import Counter
print(len(out), Counter(o["class_claim_rule"] for o in out), sum(1 for o in out if o["claim_rr_quote"]), sum(1 for o in out if o["risk_quote"]))
