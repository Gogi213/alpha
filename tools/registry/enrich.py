"""Пост-обработка строк автозаписи: тикет из пути, класс прогона в статус, период и пул из команды и входов."""
import re

DATE = re.compile(r"20\d\d-\d\d-\d\d")
TK = re.compile(r"/data/tk(\d{3})\b|\btk(\d{3})[-/]")
SERVICE = re.compile(r"/(stand|gate|cost|move2?|ins)\.sh\b|\bstand\.sh\b")
NAMED = re.compile(r"(?<![/\w])tk(\d{3})-")
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTH_END = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 2, 11: 30, 12: 31}  # окт — конец пула 02.10
POOL_VERDICT = "/data/tk044/final3/verdict.csv"


def enrich_row(r):
    """Дополняет пустые поля строки benchrun-auto; возвращает True, если что-то изменилось."""
    if r.get("source") != "benchrun-auto":
        return False
    cfg = r.get("config") if isinstance(r.get("config"), dict) else {}
    txt = " ".join(str(x) for x in (r.get("what"), cfg.get("cmdline"), cfg.get("cwd")) if x)
    ch = False

    def put(k, v):
        nonlocal ch
        if v and r.get(k) != v:
            r[k] = v; ch = True
    nm = NAMED.search(r.get("what") or "")
    if nm:
        put("ticket", f"TK-{nm.group(1)}")
        if r.get("status") in ("боевой", "замер", "проба"):
            r["status"], r["status_why"] = "производство", "прогон под тикетом по имени выхода/юнита (tkNNN-…), не замер скорости"; ch = True
    if not r.get("ticket"):
        m = TK.search(txt)
        put("ticket", f"TK-{m.group(1) or m.group(2)}" if m else ("TK-068" if "/data/registry" in txt else ""))
    if r.get("status") == "боевой":
        if r.get("kind") == "speed":
            st, why = "замер", "волна/стенд на скорость (benchrun wave)"
        elif SERVICE.search(txt):
            st, why = "проба", "стенд/гейт/служебный скрипт, не производство"
        else:
            st, why = "производство", "счёт/досчёт под тикетом, результат в каталоге тикета"
        r["status"], r["status_why"] = st, why; ch = True
    dates = sorted(set(DATE.findall(r.get("what") or "")))
    if dates:
        put("period_from", dates[0]); put("period_to", dates[-1])
    mo = [MONTHS[x] for x in str((cfg.get("env") or {}).get("MONTHS", "")).split() if x in MONTHS]
    if mo and not dates:
        put("period_from", f"2026-{min(mo):02d}-01"); put("period_to", f"2026-{max(mo):02d}-{MONTH_END[max(mo)]:02d}")
    if POOL_VERDICT in (cfg.get("inputs") or {}):
        put("data_pool", "v171b"); put("gate", "verdict.csv TK-044 (final3)")
    return ch
