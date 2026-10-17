"""TK-268 ш.8: правка Летописи (launch.py checked_metrics по n для канона без $, metrics_why + поле mwhy/metrics_dash; page.html подсказка «—» с причиной).
Берёт готовые функции из рабочей копии tk268 и применяет те же замены к копии calc (другая база — tk278). python tk268-r2a-apply.py <эталон tools/launch> <целевой tools/launch>"""
import sys, re
src, dst = sys.argv[1], sys.argv[2]
rd = lambda p: open(p, encoding="utf-8", newline="").read()
S = rd(src + "/launch.py"); a = S.index("def checked_metrics(mt, got):"); b = S.index("def summary(p):")
new_fn = S[a:b]
L = rd(dst + "/launch.py"); a = L.index("def checked_metrics(mt, got):"); b = L.index("\ndef ", a + 5) + 1
L = L[:a] + new_fn + L[b:]
def rep(s, o, n):
    assert s.count(o) == 1, o[:60]
    return s.replace(o, n)
L = rep(L, '''    mets = hash_metrics_file()
    sieve = {}''', '''    mets = hash_metrics_file()
    whys = hash_why_file()
    sieve = {}''')
L = rep(L, '''        mt = checked_metrics(mets.get(r["hash"]), got)
        rt = routes.get(r["hash"], "cells")
''', '''        mt = checked_metrics(mets.get(r["hash"]), got)
        rt = routes.get(r["hash"], "cells")
        off = OFF_LABELS.get(rt) if st == "none" and rt not in COUNTABLE else None
''')
L = rep(L, '''                    "off": OFF_LABELS.get(rt) if st == "none" and rt not in COUNTABLE else None,''', '''                    "off": off,''')
L = rep(L, '''"tail_d": mt and mt["tail_d"], "worst_d": mt and mt["worst_d"]})
    return {"months": months, "rows": out, "nhash": len({x["hash"] for x in out}),
            "metrics_n": sum(1 for x in out if x["sharpe"] is not None or x["winrate"] is not None)}''', '''"tail_d": mt and mt["tail_d"], "worst_d": mt and mt["worst_d"],
                    "mwhy": None if mt else whys.get(r["hash"]) or metrics_why(mets.get(r["hash"]), got, off)})
    return {"months": months, "rows": out, "nhash": len({x["hash"] for x in out}),
            "metrics_n": sum(1 for x in out if x["sharpe"] is not None or x["winrate"] is not None),
            "metrics_dash": sum(1 for x in out if x["mwhy"])}''')
open(dst + "/launch.py", "w", encoding="utf-8", newline="").write(L)
P = rd(dst + "/page.html")
P = rep(P, "if(k==='hp')t=' title=\"период '+b.overhigh_d+' дн · хвост '+b.tail_d+' дн\"'}", "if(v===null||v===undefined){if(b.mwhy)t=' title=\"'+esc(b.mwhy)+'\"'}else if(k==='hp')t=' title=\"период '+b.overhigh_d+' дн · хвост '+b.tail_d+' дн\"'}")
P = rep(P, "есть у '+LMN+' из '+LR.length+', «—» = closes нет или не сошлись с Σ $'", "есть у '+LMN+' из '+LR.length+', ещё '+LMD+' — «—» с причиной в подсказке (closes нет или не сошлись с каноном)'")
P = rep(P, "LMN=0,LNH=0,", "LMN=0,LMD=0,LNH=0,")
P = rep(P, "LMN=d.metrics_n||0;", "LMN=d.metrics_n||0;LMD=d.metrics_dash||0;")
open(dst + "/page.html", "w", encoding="utf-8", newline="").write(P)
print("ok")
