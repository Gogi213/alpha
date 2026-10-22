#!/usr/bin/env python3
"""Пересчёт метрик плагина role-play-vibing по тикетам alpha.
Запуск из корня репозитория: python docs/findings/2026-10-17-plugin-data/recount.py
Источники: .claude/tickets/TK-*.md (лог записей `### ts role` + `[итог: X]`), .claude/dispatcher/runs.log.
Периоды (по времени первой записи в логе тикета): НОВЫЙ 16.10 12:00 - 17.10 18:00 (TK-266..293),
ПРОШЛЫЙ 14.10 00:00 - 16.10 12:00. Выход: tickets.csv, summary.txt, quotes.txt, askowner.txt рядом со скриптом."""
import re, glob, os, csv, collections, datetime as dt
OUT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.getcwd()
T = lambda s: dt.datetime.fromisoformat(s)
NEW0, NEW1 = T("2026-10-16T12:00:00+04:00"), T("2026-10-17T18:00:00+04:00")
OLD0 = T("2026-10-14T00:00:00+04:00")
runs = collections.defaultdict(list)
for l in open(f"{ROOT}/.claude/dispatcher/runs.log", encoding="utf-8"):
    p = l.split()
    if len(p) < 3 or not p[1].startswith("TK-"): continue
    kv = dict(x.split("=", 1) for x in p[3:] if "=" in x)
    c = kv.get("resolved_cost", kv.get("cost_usd", "-"))
    try: c = float(c)
    except: c = 0.0
    runs[p[1]].append((p[0], p[2], c, kv.get("status", ""), kv.get("reason", "")))
METH = re.compile(r"методик|развилк|порядок счёта|ресурс|ядер|ядра|очеред|alsched|пороги?\b|правило|протокол|смоук|вариант|выбор|PGO|pgo|ок\?", re.I)
# Ручная разметка ask-owner/blocked (прочитано дословно, см. askowner.txt): M = методика/ресурсы/порядок счёта (решает Судья/CEO),
# O = решение владельца по делу (дизайн, деньги), H = блок инструментом (хук/нет пути будить роль).
MANUAL = {
 "TK-266 2026-10-16T12:25": "M", "TK-268 2026-10-17T17:11": "M", "TK-268 2026-10-17T17:31": "M",
 "TK-271 2026-10-16T18:04": "O", "TK-271 2026-10-16T18:05": "O", "TK-272 2026-10-16T18:54": "M",
 "TK-274 2026-10-16T18:52": "M", "TK-274 2026-10-16T20:01": "M", "TK-274 2026-10-17T07:21": "M",
 "TK-278 2026-10-16T20:29": "M", "TK-278 2026-10-17T06:18": "M", "TK-279 2026-10-16T20:55": "M",
 "TK-279 2026-10-16T21:59": "M", "TK-282 2026-10-17T05:01": "H",
 "TK-241 2026-10-14T20:12": "H", "TK-241 2026-10-14T20:18": "H", "TK-253 2026-10-15T17:36": "H",
 "TK-265 2026-10-16T07:01": "M"}
rows, quotes, asks = [], [], []
for f in sorted(glob.glob(f"{ROOT}/.claude/tickets/TK-*.md")):
    tid = os.path.basename(f)[:-3]
    if not re.fullmatch(r"TK-\d+", tid): continue
    txt = open(f, encoding="utf-8").read()
    arch = f"{ROOT}/.claude/tickets/archive/{tid}-log.md"
    if os.path.exists(arch):  # старые записи лога вынесены в архив - читаем и их
        txt = txt + chr(10) + open(arch, encoding="utf-8").read()
    head = txt.split("---")[1] if txt.startswith("---") else ""
    fm = dict(re.findall(r"^(\w+):\s*(.*)$", head, re.M))
    ents = re.split(r"^### (\S+) (\S+)\n", txt, flags=re.M)
    log = sorted(((ents[i], ents[i+1], ents[i+2].strip()) for i in range(1, len(ents)-2, 3)), key=lambda x: x[0])
    if not log: continue
    t0 = T(log[0][0])
    per = "new" if NEW0 <= t0 <= NEW1 else "old" if OLD0 <= t0 < NEW0 else None
    if not per: continue
    res = [(ts, r, re.match(r"\[итог: ([\w-]+)\]", b)) for ts, r, b in log]
    res = [(ts, r, m.group(1)) for ts, r, m in res if m]
    cnt = collections.Counter(x[2] for x in res)
    judge_ret = sum(1 for ts, r, k in res if r == "judge" and k == "return")
    ANS = re.compile(r"Развилка|решена|решено|Блокер снят|Идёт к цели|Траты к цели|Перезапуск не нужен|Подавай|вариант Б|Не развилка|Q1|ответы")
    LIVE = re.compile(r"Выкладки нет|нет выкладки|\[вживую\] нет|\[вживую\] не выполнен|Нет выкладки|Вживую:|вживую:|не из головы|на calc старый|выкладка на calc")
    rets = [b for ts, r, b in log if r == "judge" and b.startswith("[итог: return]")]
    ret_answer = sum(1 for b in rets if ANS.search(b[:200]) and not LIVE.search(b[:300]))
    ret_live = sum(1 for b in rets if LIVE.search(b[:300]))
    ret_defect = len(rets) - ret_answer - ret_live
    acc = [ts for ts, r, k in res if r == "judge" and k == "accept"]
    dones = [ts for ts, r, k in res if k == "done"]
    end = T(max(dones + acc)) if (dones or acc) else None
    hours = round((end - t0).total_seconds()/3600, 2) if end else ""
    ask = [(ts, b) for ts, r, b in log if re.match(r"\[итог: (ask-owner|blocked)\]", b)]
    kinds = collections.Counter()
    for ts, b in ask:
        k = MANUAL.get(f"{tid} {ts[:16]}", "?"); kinds[k] += 1
        asks.append(f"{tid} {ts} kind={k} {b[:300]}")
    rr = runs.get(tid, [])
    cost = sum(x[2] for x in rr)
    rbyrole = collections.Counter(x[1] for x in rr)
    REP = re.compile(r"Повтор|уже принят|уже влит|Дела нет|Работы не осталось|нового PR нет|Дубль|Уже закрыт")
    rep = [(ts, r, b[:200]) for ts, r, b in log if re.match(r"\[итог: (accept|done)\]", b) and REP.search(b)]
    dup_acc = len(rep)
    for ts, r, b in rep: quotes.append(f"REPEAT {tid} {ts} {r}: {b}")
    wk = sum(1 for ts, r, b in log if r == "watch")
    ov = sum(1 for ts, r, b in log if r == "надзиратель")
    rows.append(dict(ticket=tid, period=per, owner=fm.get("owner", ""), status=fm.get("status", ""), created=log[0][0],
        runs=len(rr), runs_failed=sum(1 for x in rr if x[3] != "ok"), judge_returns=judge_ret, ret_answer=ret_answer, ret_nolive=ret_live, ret_defect=ret_defect, accepts=len(acc),
        repeat_signals=dup_acc, ask_owner=cnt["ask-owner"], blocked=cnt["blocked"], ask_or_blocked=len(ask),
        ask_M_method=kinds["M"], ask_O_owner=kinds["O"], ask_H_tooling=kinds["H"], ask_unclassified=kinds["?"], hours_to_done=hours, cost_usd=round(cost, 2), watch_entries=wk, overseer_entries=ov))
    for ts, r, b in log:
        if re.search(r"Повтор|уже принят|уже влит|Дела нет|нет пути|не для роли|снова ложн|ложн|сирота|orphan|ДЕФЕКТ|не работает|владелец нашёл|ошибк", b, re.I):
            quotes.append(f"{tid} {ts} {r}: {b[:400]}")
with open(f"{OUT}/tickets.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, rows[0].keys()); w.writeheader(); w.writerows(rows)
open(f"{OUT}/quotes.txt", "w", encoding="utf-8").write("\n\n".join(quotes))
open(f"{OUT}/askowner.txt", "w", encoding="utf-8").write("\n\n".join(asks))
def agg(p):
    R = [r for r in rows if r["period"] == p]; n = len(R)
    s = lambda k: sum(r[k] for r in R)
    hs = sorted(r["hours_to_done"] for r in R if r["hours_to_done"] != "")
    return (f"{p}: тикетов {n}; с возвратом Судьи {sum(1 for r in R if r['judge_returns'])} ({100*sum(1 for r in R if r['judge_returns'])/n:.0f}%); "
        f"возвратов {s('judge_returns')} ({s('judge_returns')/n:.2f}/тикет; из них ответ на развилку {s('ret_answer')}, нет выкладки/живой проверки {s('ret_nolive')}, дефект/недостача {s('ret_defect')}; тикетов с дефект-возвратом {sum(1 for r in R if r['ret_defect'])}); повторных (без работы) done/accept {s('repeat_signals')}; запусков {s('runs')} ({s('runs')/n:.1f}/тикет), "
        f"сбойных {s('runs_failed')}; ask-owner {s('ask_owner')}, blocked {s('blocked')}, из них M методика/ресурсы {s('ask_M_method')}, O владельцу по делу {s('ask_O_owner')}, H инструмент {s('ask_H_tooling')}, не размечено {s('ask_unclassified')}; "
        f"медиана часов до done {hs[len(hs)//2] if hs else '-'}; траты ${s('cost_usd'):.0f} (${s('cost_usd')/n:.1f}/тикет); watch-записей {s('watch_entries')}, надзиратель {s('overseer_entries')}")
open(f"{OUT}/summary.txt", "w", encoding="utf-8").write(agg("new") + "\n" + agg("old") + "\n")
print(open(f"{OUT}/summary.txt", encoding="utf-8").read())

# Сигналы диспетчера CEO (только чтение): двойные за 3 минуты и метки по периодам
D = f"{ROOT}/.claude/dispatcher/"
wk = collections.defaultdict(list); tot = collections.Counter(); per = collections.Counter()
def period(a): return "new" if NEW0 <= a <= NEW1 else "old" if OLD0 <= a < NEW0 else None
for l in open(D + "ceo-wake" + ".log", encoding="utf-8"):
    p = l.split()
    if len(p) >= 3:
        a = T(p[0]); pr = period(a)
        if pr: tot[pr] += 1; wk[(p[1], p[2])].append(a)
for ts in wk.values():
    for a, b in zip(ts, ts[1:]):
        if (b - a).total_seconds() <= 180: per[period(a)] += 1
inb = collections.Counter()
for l in open(D + "ceo-" + "inbox.md", encoding="utf-8"):
    m = re.match(r"- (\S+) (\S+) \[([\w-]+)\]", l)
    if m and period(T(m.group(1))): inb[(period(T(m.group(1))), m.group(3))] += 1
with open(f"{OUT}/summary.txt", "a", encoding="utf-8") as fh:
    fh.write(f"сигналов CEO new {tot['new']} (двойных за 3 мин {per['new']}), old {tot['old']} (двойных {per['old']})\n")
    for (pr, k), v in sorted(inb.items()): fh.write(f"метка {pr} [{k}]: {v}\n")
print(open(f"{OUT}/summary.txt", encoding="utf-8").read())

# только тикеты Инженера (в прошлом периоде Исследователя не было)
with open(f"{OUT}/summary.txt", "a", encoding="utf-8") as fh:
    for p in ("new", "old"):
        R = [r for r in rows if r["period"] == p and r["owner"] == "engineer"]
        n = len(R); rt = sum(r["judge_returns"] for r in R)
        fh.write(f"только Инженер {p}: тикетов {n}, возвратов {rt} ({rt/n:.2f}/тикет), с возвратом {sum(1 for r in R if r['judge_returns'])} "
                 f"({100*sum(1 for r in R if r['judge_returns'])/n:.0f}%), траты ${sum(r['cost_usd'] for r in R):.0f} (${sum(r['cost_usd'] for r in R)/n:.1f}/тикет)\n")
