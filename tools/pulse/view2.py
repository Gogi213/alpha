"""Раздел `view2` status.json по контракту `VIEW2.md`: процессы (тикеты + «без хозяина») → шаги, машины, вопросы, лента.

`make(H, …)` зовёт collect.py; H — модуль collect (хелперы: tk_norm, wait_text, short_title, fmt_num, eta_text, news_time…).
Шаги — из плана (`.claude/pulse/plans/<ТК>.json`, пишет `plan.py`); плана нет — один шаг из текущего состояния (как v1).
Ход с серверов (`/data/progress`): `step_n` → шаг n, без него — шаг в состоянии «идёт». Вопросы — `.claude/pulse/questions`.
"""
from __future__ import annotations

import re
from datetime import timedelta

import pulsedata as P

TAGS = {"pc": {"tag": "ПК", "name": "этот ПК", "color": "purple"}, "vps": {"tag": "VPS", "name": "VPS София", "color": "teal"},
        "calc": {"tag": "СЧЁТ", "name": "сервер счёта", "color": "blue"},
        "col": {"tag": "КОЛ", "name": "коллектор", "color": "gray"}, "you": {"tag": "ВЫ", "name": "вы", "color": "amber"}}
MORDER = ("pc", "vps", "calc", "col")
V2 = {"collector": "col"}  # id машины v1 → v2
ROLE_LC = {"engineer": "инженер", "researcher": "исследователь", "judge": "судья", "ceo": "CEO"}
EMPTY_FOR = {"text": "", "on": None}


def _mid(m: str) -> str:
    return V2.get(m, m)


def _step(n, title, who, on, forr, state, **kw) -> dict:
    d = {"n": n, "title": title, "who": who, "on": on, "for": forr, "state": state, "pct": None, "detail": None,
         "eta_min": None, "started": None, "finished": None, "question": None}
    d.update(kw)
    return d


def _pstate(steps: list) -> str:
    st = {s["state"] for s in steps}
    for k in ("bad", "wait", "run"):
        if k in st:
            return k
    return "done" if st == {"done"} else "todo"


def _step_now(steps: list) -> int:
    for pick in (("run", "wait"), ("bad",), ("todo",)):
        for s in steps:
            if s["state"] in pick:
                return s["n"]
    return steps[-1]["n"] if steps else 0


def make(H, *, plain, tickets, live, machines, jobs_all, events, view, disp_ok, watch_ok, now) -> dict:
    now_dt = P.now_dt()
    mach = {_mid(m["id"]): m for m in machines if m["id"] != "deck"}
    warns = {_mid(m["id"]): m.get("warns") or [] for m in view["machines"]}
    jobs = {}
    for k, jl in jobs_all.items():
        for j in jl:
            jobs.setdefault(H.tk_norm(k) or k, []).append(j)

    plans = {}
    for p in sorted(P.PLANS_DIR.glob("*.json")):
        d = H.read_json_cached(p, settle_s=1.5)
        if isinstance(d, dict) and isinstance(d.get("steps"), list):
            plans[p.stem] = d
    allq = [q for q in (H.read_json_cached(p, settle_s=1.5) for p in sorted(P.QUESTIONS_DIR.glob("q-*.json")))
            if isinstance(q, dict) and q.get("id")]
    allq.sort(key=lambda q: q.get("since") or "")
    openq = [q for q in allq if not q.get("answered_at")]
    qopen_ids = {q["id"] for q in openq}

    def mins_since(iso):
        d = P.parse(iso)
        return max(0, int((now_dt - d).total_seconds() // 60)) if d else None

    def when(iso):
        d = P.parse(iso)
        if not d:
            return ""
        d = d.astimezone(P.TZ)
        return d.strftime("%H:%M") if d.date() == now_dt.date() else d.strftime("%d.%m %H:%M")

    def job_fields(j) -> dict:
        unit = str(plain["units"].get(j["unit"]) or j["unit"]).strip()
        done = j["done"] >= j["total"]
        return {"pct": 100 if done else j["pct"], "detail": f"{H.fmt_num(j['done'])} из {H.fmt_num(j['total'])} {unit}".strip(),
                "eta_min": None if done else j.get("eta_min")}

    def ptitle(tid):
        t = (plain["tasks"].get(tid) or {}).get("title")
        tk = tickets.get(tid)
        return t or (H.short_title(tk.header.get("title", ""), 60) if tk else "") or tid

    def step_text(tid, s):
        st = (plain["tasks"].get(tid) or {}).get("steps")
        return str((st or {}).get(s) or s) if isinstance(st, dict) else s

    # --- шаги из плана
    def plan_steps(tid, plan) -> list:
        steps = []
        for i, s in enumerate(plan["steps"], 1):
            q = s.get("question")
            steps.append(_step(i, s.get("title", ""), s.get("who", ""), s.get("on", "pc"), s.get("for") or "",
                               s.get("state", "todo"), detail=s.get("detail"), started=P.hhmm(s.get("started_at")),
                               finished=P.hhmm(s.get("finished_at")), question=q if q in qopen_ids else None))
        run_i = next((i for i, s in enumerate(steps) if s["state"] == "run"), None)
        for j in jobs.get(tid, []):
            try:
                i = int(j.get("step_n")) - 1
            except (TypeError, ValueError):
                i = run_i
            if i is None or not 0 <= i < len(steps) or steps[i]["pct"] is not None:
                continue
            steps[i].update(job_fields(j))
        return steps

    # --- шаги без плана: одно состояние сейчас
    def synth_steps(tid, t) -> tuple[list, list]:
        steps, mids = [], []
        n = 0

        def add(title, who, on, state, **kw):
            nonlocal n
            n += 1
            steps.append(_step(n, title, who, on, "", state, **kw))
            if on in mach:
                mids.append(on)

        for j in jobs.get(tid, []):
            f = job_fields(j)
            add(step_text(tid, j["step"]) or j["job"], "автомат", _mid(j["machine"]), "done" if f["pct"] == 100 else "run", **f)
        if not steps:
            for m in machines:
                lk = m.get("lock") or {}
                if lk.get("busy") and lk.get("ticket") == tid:
                    add("сборка кода", "автомат", _mid(m["id"]), "run")
            for m in machines:
                if m["id"] in ("pc", "deck") or steps:
                    continue
                if any(H.tk_norm(g["name"], prefix=True) == tid for g in m.get("procs", [])):
                    add("идёт счёт", "автомат", _mid(m["id"]), "run")
        if not steps and tid in live:
            r = live[tid]
            add(H.ROLE_DO.get(r["role"], r["role"]), ROLE_LC.get(r["role"], r["role"]), "pc", "run",
                started=(now_dt - timedelta(minutes=r["minutes"])).strftime("%H:%M"))
        if not steps:
            wt = H.wait_text(t)
            owner = t.status == "needs_owner"
            add(wt, "вы" if owner else ("CEO" if "CEO" in wt else "автомат"), "you" if owner else "pc",
                "wait" if t.status in ("waiting", "needs_owner") else "todo")
        return steps, mids

    # --- процессы-тикеты
    procs = []
    for tid, t in tickets.items():
        if t.status in ("done", "stopped"):
            continue
        plan = plans.get(tid)
        if not (plan or tid in live or jobs.get(tid) or t.status in ("todo", "in_progress", "in_review", "needs_owner")
                or (t.status == "waiting" and now - H.t_updated(t, now) < H.WAITING_MAX_AGE_S)):
            continue
        if plan:
            steps = plan_steps(tid, plan)
            flow, forr, title = plan.get("flow") or [], plan.get("for") or EMPTY_FOR, plan.get("title") or ptitle(tid)
        else:
            steps, mids = synth_steps(tid, t)
            flow = [{"text": "идёт на", "on": m} for m in dict.fromkeys(mids)] or [{"text": "в очереди на", "on": "pc"}]
            forr, title = EMPTY_FOR, ptitle(tid)
        procs.append({"id": tid, "title": title, "flow": flow, "for": forr, "steps": steps})

    # --- «без хозяина»: по одному на машину
    for mid in ("vps", "calc", "col"):
        ws = warns.get(mid) or []
        if not ws:
            continue
        pid = f"orphans-{mid}"
        items = "; ".join(re.sub(r"\s*—\s*без задачи,?\s*", ", ", w) for w in ws)
        steps = [_step(1, "без хозяина", "автомат", mid, f"{len(ws)} шт.", "bad", detail=items)]
        qs = [q for q in allq if q.get("process") == pid]
        if qs:
            q = qs[-1]
            ans = q.get("answered_at")
            steps.append(_step(2, "вопрос владельцу", "вы", "you", "остановить?", "done" if ans else "wait",
                               detail=f"ответ: {q.get('answer_label')}" if ans else None, started=P.hhmm(q.get("since")),
                               finished=P.hhmm(ans) if ans else None, question=None if ans else q["id"]))
            steps.append(_step(3, "остановить", "CEO" if ans else "автомат", mid, mach[mid]["name"] if mid in mach else mid,
                               "todo", detail="исполнит CEO" if ans else None))
        procs.append({"id": pid, "title": f"Старые задачи без хозяина ×{len(ws)}" if len(ws) > 1 else "Старая задача без хозяина",
                      "flow": [{"text": "висят на", "on": mid}], "for": {"text": "кто запустил — неизвестно", "on": None},
                      "steps": steps})

    # --- итог по процессам
    def sortkey(p):
        return (p["state"] == "done", p["id"].startswith("orphans-"), p["id"])

    for p in procs:
        st = p["steps"]
        run = next((s for s in st if s["state"] == "run"), None)
        pq = [q for q in openq if q.get("process") == p["id"]]
        p.update(state=_pstate(st), step_now=_step_now(st), steps_total=len(st),
                 eta_min=run["eta_min"] if run else None,
                 wait_min=max((mins_since(q.get("since")) or 0 for q in pq), default=None) if pq else None)
    procs.sort(key=sortkey)
    for i, p in enumerate(procs, 1):
        p["n"] = i

    # --- вопросы (неотвеченные)
    questions = [{"id": q["id"], "n": i, "process": q.get("process"), "from": q.get("from"), "on": q.get("on"),
                  "text": q.get("text"), "options": q.get("options") or [], "since": when(q.get("since")),
                  "wait_min": mins_since(q.get("since"))} for i, q in enumerate(openq, 1)]

    # --- машины
    mviews = []
    for mid in MORDER:
        m = mach.get(mid)
        if not m:
            continue
        runs = [(p, s) for p in procs if not p["id"].startswith("orphans-") for s in p["steps"]
                if s["on"] == mid and s["state"] == "run"]
        load = list(dict.fromkeys(f"{s['who']} · {p['title']}" for p, s in runs))
        n_or = len(warns.get(mid) or [])
        if n_or:
            load.append(f"без хозяина ×{n_or}")
        units = m.get("units") or []
        off = mid == "col" and bool(units) and any(u["state"] != "active" for u in units)
        state = "down" if m["state"] == "down" else ("off" if off else "ok")
        if not load:
            load = [str(plain["machines"].get("collector") or "выключен") if off else "свободен" if mid == "pc" else "свободна"]
        if state == "down":
            now_ = {"state": "bad", "text": "нет связи"}
        elif any(q.get("on") == mid for q in openq):
            now_ = {"state": "wait", "text": "ждёт вас"}
        elif runs:
            p, s = runs[0]
            eta = f" {H.eta_text(s['eta_min']).replace('ещё ', '')}" if s.get("eta_min") is not None else ""
            now_ = {"state": "run", "text": s["title"] + eta}
        elif off:
            now_ = {"state": "off", "text": "задач нет"}
        else:
            now_ = {"state": "idle", "text": "простаивает"}
        mviews.append({"id": mid, "state": state, "load": "; ".join(load), "now": now_, "orphans": n_or, "cpu": m.get("cpu"),
                       "mem": m.get("mem"), "disk_mb_s": m.get("disk_mb_s")})

    # --- лента: Haiku-строки v1 + шаги плана + вопросы; по возрастанию времени, последние 8
    feed = []

    def on_at(tid, ts):  # где это было: шаг плана, чьё окно покрывает время, иначе ПК
        for s in (plans.get(tid) or {}).get("steps", []):
            a, b = P.parse(s.get("started_at")), P.parse(s.get("finished_at")) or now_dt
            if a and ts and a <= ts <= b and s.get("on") in mach:
                return [s["on"]]
        return ["pc"]

    for n in plain.get("news", []):
        ts = P.parse(n.get("ts"))
        feed.append((ts.timestamp() if ts else 0, {"time": n["time"], "state": "done", "on": on_at(n.get("ticket"), ts), "to": None,
                                                  "text": n["text"]}))
    for p in procs:
        for s in (plans.get(p["id"]) or {}).get("steps", []):
            f = P.parse(s.get("finished_at"))
            if s.get("state") == "done" and f and (now_dt - f).total_seconds() < 86400:
                feed.append((f.timestamp(), {"time": H.news_time(f), "state": "done", "on": [s["on"]] if s["on"] in mach else ["pc"], "to": None,
                                             "text": s["title"]}))
    for q in allq:
        a = P.parse(q.get("since"))
        if a:
            feed.append((a.timestamp(), {"time": H.news_time(a), "state": "wait", "on": [q.get("on", "pc")], "to": "you",
                                         "text": H.clip(f"вопрос: {q['text']}", 70)}))
        b = P.parse(q.get("answered_at"))
        if b:
            feed.append((b.timestamp(), {"time": H.news_time(b), "state": "done", "on": [q.get("on", "pc")], "to": None,
                                         "text": f"ответ владельца: {q.get('answer_label')}"}))
    feed.sort(key=lambda x: x[0])

    # --- заголовок, счётчики
    problems = [f"нет связи: {m['name']}" for m in machines if m["id"] != "deck" and m["state"] == "down"]
    if not disp_ok:
        problems.append("диспетчер стоит")
    elif not watch_ok:
        problems.append("сторож стоит")
    cnt = {k: 0 for k in ("done", "run", "wait", "todo", "bad")}
    for p in procs:
        for s in p["steps"]:
            cnt[s["state"]] = cnt.get(s["state"], 0) + 1
    if problems:
        head = {"state": "bad", "text": "; ".join(problems)}
    elif questions:
        head = {"state": "wait", "text": f"ждёт вас: {len(questions)}"}
    else:
        head = {"state": "ok", "text": plain.get("headline") or ("всё идёт" if cnt["run"] else "сейчас ничего не идёт")}
    return {"time": now_dt.strftime("%H:%M"), "headline": head, "counters": cnt, "tags": TAGS, "machines": mviews,
            "processes": procs, "questions": questions, "feed": [f for _, f in feed[-8:]]}
