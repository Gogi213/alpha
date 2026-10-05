"""Раздел `view2` status.json по контракту `VIEW2.md`: процессы (тикеты + «без хозяина») → шаги, машины, вопросы, лента.

`make(H, …)` зовёт collect.py; H — модуль collect (хелперы: tk_norm, wait_text, short_title, fmt_num, eta_text, news_time…).
Шаги — из плана (`.claude/pulse/plans/<ТК>.json`, пишет `plan.py`); плана нет — один шаг из текущего состояния (как v1).
Ход с серверов (`/data/progress`): `step_n` → шаг n, без него — шаг в состоянии «идёт». Вопросы — `.claude/pulse/questions`.
Волны: шаг — `after` (по умолчанию предыдущий) → `wave = 1 + max(wave(after))`; процесс — строка `depends:` в шапке тикета.
Состояния шага: run делается · review проверяется · repair чинится · wait ждёт вас · bad проблема · done · todo.
Общий прогресс (`progress`), «осталось» вилкой по критической цепочке, «прошло» — рабочее время; строка-итог процесса — Haiku.
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
ACTIVE = ("run", "review", "repair")  # «в работе»: делается / проверяется / чинится
STATE_RU = {"done": "готово", "run": "делается", "review": "проверяется", "repair": "чинится", "wait": "ждёт решения владельца",
            "bad": "проблема", "todo": "впереди"}
COUNT_KEYS = ("done", "run", "review", "repair", "wait", "todo", "bad")
EXECUTORS = ("инженер", "исследователь")  # чьи «делается»-шаги после возврата Судьи становятся «чинится»
ETA_LO, ETA_HI = 0.75, 1.4   # вилка «осталось»: доли суммы eta_min шагов критической цепочки
ETA_MIN_MEASURES = 2         # меньше замеров (шагов цепочки с eta_min) — вилку не показываем
WORK_GAP_MIN = 45            # «прошло»: промежуток между событиями длиннее — засчитывается как столько минут
AUTO_TITLE_LEN = 60
BOARD_RE = re.compile(r"табло|шкал|дашборд|страниц", re.I)
FEED_AGE_S = 21600


def _mid(m: str) -> str:
    return V2.get(m, m)


HUMAN_WORDS = 6
_TECH = re.compile(r"https?://\S+|host:\S+|\S*/\S+|(?<!\w)--?[A-Za-z][\w-]*|\bmd5\b|\bTK-?\d+\S*|\b[ВвTt]-\d+|\bветк\w*\s+\S+|\bИтог\w*", re.I)


def human(text: str, words: int = HUMAN_WORDS) -> str:
    """Текст шага для клетки табло: без скобок, путей, хэшей, номеров тикетов и флагов; не больше `words` слов."""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text or "")
    s = _TECH.sub(" ", s)
    s = re.split(r"[:;]|\s—\s", s.strip(" :;—-"), maxsplit=1)[0] if len(s.split()) > words else s
    out = []
    for w in s.split():
        w = w.strip(" ,.;:—-+*=")
        if not w or (re.search(r"\d", w) and re.search(r"[A-Za-zА-Яа-я]", w)) or re.fullmatch(r"[0-9a-f]{7,}", w):
            continue
        out.append(w)
    return " ".join(out[:words]) or "работа идёт"


def _step(n, title, who, on, forr, state, **kw) -> dict:
    d = {"n": n, "title": title, "who": who, "on": on, "for": forr, "state": state, "pct": None, "detail": None,
         "eta_min": None, "started": None, "finished": None, "question": None, "after": None, "wave": 1}
    d.update(kw)
    return d


def step_waves(steps: list) -> None:
    """`after` (None — предыдущий шаг; пусто — стартует сразу; ссылки только на более ранние) → `after` и `wave` шага."""
    for i, s in enumerate(steps, 1):
        raw = s.get("after")
        if raw is None:
            raw = [i - 1] if i > 1 else []
        a = sorted({x for x in raw if isinstance(x, int) and not isinstance(x, bool) and 1 <= x < i})
        s["after"] = a
        s["wave"] = 1 + max((steps[x - 1]["wave"] for x in a), default=0)


def proc_waves(ids: list, deps: dict) -> dict:
    """Волна процесса = 1 + максимум волн процессов из `depends`; цикл не роняет (ребро внутрь цикла не считается)."""
    memo: dict = {}

    def w(i, stack=()):
        if i in memo:
            return memo[i]
        if i in stack:
            return 1
        v = 1 + max((w(d, stack + (i,)) for d in deps.get(i, []) if d != i and d in ids), default=0)
        memo[i] = v
        return v

    return {i: w(i) for i in ids}


def plural(n: int, forms: tuple) -> str:
    if 11 <= n % 100 <= 14:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def wave_groups(procs: list) -> list:
    """Группы обзора: «ВОЛНА 1 · 2 процесса параллельно», «ВОЛНА 2 · ждёт волну 1», в конце — «БЕЗ ХОЗЯИНА»."""
    groups: dict = {}
    orph = []
    for p in procs:
        (orph if p["wave"] is None else groups.setdefault(p["wave"], [])).append(p)
    out = []
    for w in sorted(groups):
        ps = groups[w]
        st = "done" if all(p["state"] == "done" for p in ps) else "todo" if all(p["state"] == "todo" for p in ps) else "run"
        parts = [f"ВОЛНА {w}"]
        if len(ps) > 1:
            parts.append(f"{len(ps)} {plural(len(ps), ('процесс', 'процесса', 'процессов'))} параллельно")
        out.append({"n": w, "label": parts, "state": st, "procs": [p["id"] for p in ps]})
    for g in out:
        if g["state"] == "todo":
            before = [h["n"] for h in out if h["n"] < g["n"] and h["state"] != "done"]
            if before:
                g["label"].append(f"ждёт волну {max(before)}")
        elif g["state"] == "done":
            g["label"].append("готова")
        g["label"] = " · ".join(g["label"])
    if orph:
        out.append({"n": None, "label": "БЕЗ ХОЗЯИНА", "state": "bad", "procs": [p["id"] for p in orph]})
    return out


def step_weight(s: dict) -> float:
    if s["state"] == "done":
        return 1.0
    return max(0.0, min(1.0, s["pct"] / 100)) if s.get("pct") is not None else 0.0


def critical_eta(work: list):
    """Критическая цепочка шагов (самый длинный путь по eta_min через шаги и зависимости процессов) → вилка «осталось».
    None — замеров (шагов цепочки с eta_min) меньше ETA_MIN_MEASURES."""
    best: dict = {}
    by_id = {p["id"]: p for p in work}
    sinks = {p["id"]: [s["n"] for s in p["steps"] if not any(s["n"] in x["after"] for x in p["steps"])] for p in work}
    for p in sorted(work, key=lambda p: (p["wave"] or 0, p["n"])):
        entry = [(d, n) for d in p.get("depends") or [] if d in by_id for n in sinks.get(d, [])]
        for s in p["steps"]:
            preds = [(p["id"], a) for a in s["after"]] or entry
            w = float(s["eta_min"]) if s["state"] != "done" and s.get("eta_min") is not None else 0.0
            bp = max(preds, key=lambda k: best[k][0], default=None)
            best[(p["id"], s["n"])] = ((best[bp][0] if bp else 0.0) + w, bp)
    if not best:
        return None
    k = max(best, key=lambda k: best[k][0])
    path = []
    while k:
        path.append(k)
        k = best[k][1]
    path.reverse()
    pick = [(pid, n) for pid, n in path if by_id[pid]["steps"][n - 1]["state"] != "done"]
    meas = [(pid, n) for pid, n in pick if by_id[pid]["steps"][n - 1].get("eta_min") is not None]
    if len(meas) < ETA_MIN_MEASURES:
        return None
    tot = sum(float(by_id[pid]["steps"][n - 1]["eta_min"]) for pid, n in meas)
    return {"lo_min": max(1, round(ETA_LO * tot)), "hi_min": max(1, round(ETA_HI * tot)), "measured": len(meas),
            "chain": [{"process": pid, "n": n} for pid, n in pick]}


def work_minutes(stamps: list, now_ts: float, gap: float = WORK_GAP_MIN):
    """Рабочее время: промежутки между событиями > gap мин засчитываются как gap (и последний — до «сейчас»)."""
    ts = sorted(set(stamps))
    if not ts:
        return None
    tot = sum(min(gap, (b - a) / 60) for a, b in zip(ts, ts[1:]))
    return int(round(tot + min(gap, max(0.0, (now_ts - ts[-1]) / 60))))


def progress_block(work: list, stamps: list, now_ts: float):
    """Общая полоса: вес шага 1, у шага с pct — частично; процессы «без хозяина» не в счёте (это не работа к цели)."""
    total = sum(len(p["steps"]) for p in work)
    if not total:
        return None
    done = sum(step_weight(s) for p in work for s in p["steps"])
    pct = int(100 * done / total + 0.5)
    if done < total:
        pct = min(pct, 99)
    return {"pct": pct, "done": round(done, 2), "total": total, "eta": critical_eta(work),
            "spent_min": work_minutes(stamps, now_ts)}


def judge_returned(H, t) -> bool:
    """Последняя запись Судьи — «вернуть/возврат», а тикет с тех пор на ревью не сдан снова (status != in_review) и не закрыт."""
    if t is None or t.status in ("in_review", "done"):
        return False
    last = next((e for e in reversed(H.all_entries(t)) if H._role_key(e.author) == "judge"), None)
    return bool(last and re.search(r"вернуть|возврат", last.text, re.I))


def adjust_states(steps: list, t, returned: bool) -> None:
    """Три активных состояния из «идёт»: шаг судьи → проверяется; тикет in_review → проверяется; после возврата Судьи
    шаг исполнителя → чинится. Явные review/repair из плана остаются как есть."""
    in_review = t is not None and t.status == "in_review"
    for s in steps:
        if s["state"] != "run":
            continue
        if s["who"] == "судья" or (in_review and s["who"] not in ("автомат", "вы")):
            s["state"] = "review"
        elif returned and s["who"] in EXECUTORS:
            s["state"] = "repair"
    if in_review and not any(s["state"] in ACTIVE + ("wait", "bad") for s in steps):
        todo = [s for s in steps if s["state"] == "todo"]
        pick = next((s for s in todo if s["who"] == "судья"), todo[0] if todo else None)
        if pick:
            pick["state"] = "review"


def _pstate(steps: list) -> str:
    st = {s["state"] for s in steps}
    for k in ("bad", "wait", "repair", "review", "run"):
        if k in st:
            return k
    return "done" if st == {"done"} else "todo"


def _step_now(steps: list) -> int:
    for pick in (ACTIVE + ("wait",), ("bad",), ("todo",)):
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
            af = s.get("after")
            steps.append(_step(i, s.get("title", ""), s.get("who", ""), s.get("on", "pc"), s.get("for") or "",
                               s.get("state", "todo"), detail=s.get("detail"), started=P.hhmm(s.get("started_at")),
                               finished=P.hhmm(s.get("finished_at")), question=q if q in qopen_ids else None,
                               after=af if isinstance(af, list) else None))
        run_i = next((i for i, s in enumerate(steps) if s["state"] in ACTIVE), None)
        for j in jobs.get(tid, []):
            try:
                i = int(j.get("step_n")) - 1
            except (TypeError, ValueError):
                i = run_i
            if i is None or not 0 <= i < len(steps) or steps[i]["pct"] is not None:
                continue
            steps[i].update(job_fields(j))
        return steps

    def wait_human(t) -> tuple[str, str]:
        """Что ждём (по-людски) и на какой машине: wait_for host:calc|vps:… → эта машина, иначе ПК."""
        wf = (t.header.get("wait_for") or "").strip().lower()
        m = re.match(r"(?:host:)?(calc|vps)\b", wf)
        if t.status == "needs_owner":
            return "ждёт вашего слова", "you"
        if t.next_role == "ceo" or wf.startswith(("ceo", "mention")) or (t.status == "waiting" and not wf):
            return "ждёт решения CEO", "pc"
        if m:
            return ("ждёт конца счёта", "calc") if m.group(1) == "calc" else ("ждёт конца сборки", "vps")
        if wf.startswith("ticket:"):
            return "ждёт другую задачу", "pc"
        if t.status == "in_review":
            return "ждёт проверки Судьи", "pc"
        if t.next_role or t.status in ("todo", "in_progress"):
            return "в очереди", "pc"
        return human(H.wait_text(t)), "pc"

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
            add(human(step_text(tid, j["step"])).replace("работа идёт", "идёт счёт"), "автомат", _mid(j["machine"]), "done" if f["pct"] == 100 else "run", **f)
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
        if not steps and t.status == "in_review":
            add(H.ROLE_DO["judge"], "судья", "pc", "run")
        if not steps:
            wt, wmid = wait_human(t)
            owner = t.status == "needs_owner"
            add(wt, "вы" if owner else ("CEO" if "CEO" in wt else "автомат"), "you" if owner else wmid,
                "wait" if t.status in ("waiting", "needs_owner") else "todo")
        return steps, mids

    def fact(e, limit: int = 56) -> str:  # заголовок записи: без скобок, путей, хэшей; резка по границе слова/фразы
        s = re.sub(r"[\s–—,-]*@@[\s@–—,-]*", " ", re.sub(r"\([^)]*\)?", "@@", entry_title(e)))
        s = re.sub(r"(?<![\w])[0-9a-f]{7,40}(?![\w])|коммит|п\.\d+", " ", _TECH.sub(" ", s))
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"\s*[–—-]\s*(?=[,.;:])|\s+[–—-]\s*$", "", s)
        s = re.sub(r"\s+([,.:;])", lambda m: m.group(1), s).strip(" ,.;:—–-…·")
        if len(s) > limit:
            cut = s[: limit + 1]
            k = max((cut.rfind(x) for x in (", ", "; ", ": ", " — ", " – ")), default=-1)
            if k < 24:  # заголовок до первого двоеточия, если он короткий, но осмысленный
                k = next((i for i in (s.find(": "), s.find(" — ")) if 14 <= i <= limit), k)
            k = k if k >= 14 else -1
            cut = cut[:k] if k >= 14 else cut[: cut.rfind(" ")] if " " in cut else cut
            s = cut.rstrip(" ,.;:—–-·") + ("" if k >= 14 else "…")
        s = re.sub(r"(?:\s+(?:на|по|с|и|в|от|до|для|юнит|–|-))+$", "", s)
        return s.strip(" ,.;:—–-…·") or "запись роли"

    # --- записи ролей из лога тикета
    def role_entries(t) -> list:
        return [e for e in H.all_entries(t) if H._role_key(e.author) in ROLE_LC]

    def entry_title(e) -> str:
        txt = H.clean_text(e.text)
        txt = re.sub(r"^(?:Инженер|Исследователь|Судья|CEO)\s*(?:\([^)]*\)|\d{1,2}:\d{2})?[\s.:—-]*", "", txt)
        txt = re.sub(r"^\W*Итог(?:\s+шага\s*\([^)]*\))?\W*(?:[0-9a-f]{7,40}\W+)?", "", txt, flags=re.I)
        return H.first_phrase(txt or H.clean_text(e.text), 200)

    # задание с чужим (закрытым/неизвестным) тикетом в /data/progress → открытый тикет, что его запустил (wait_for / лог)
    open_ids = [i for i, t in tickets.items() if t.status not in ("done", "stopped")]
    for k in list(jobs):
        if k in open_ids:
            continue
        for j in list(jobs[k]):
            host = next((i for i in open_ids if j["job"] in (tickets[i].header.get("wait_for") or "")), None) or next(
                (i for i in open_ids if any(j["job"] in e.text for e in tickets[i].log[-6:])), None)
            if host:
                jobs[k].remove(j)
                jobs.setdefault(host, []).append(j)

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
            for i, s in enumerate(steps, 1):
                s["n"] = i
            flow =[{"text": "идёт на", "on": m} for m in dict.fromkeys(mids)] or [{"text": "в очереди на", "on": "pc"}]
            forr, title = EMPTY_FOR, ptitle(tid)
        adjust_states(steps, t, judge_returned(H, t))
        procs.append({"id": tid, "title": title, "flow": flow, "for": forr, "steps": steps, "has_plan": bool(plan),
                      "plan": bool(plan), "role": (t.header.get("owner") or "").strip()})

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
                      "steps": steps, "has_plan": False})

    # --- волны шагов; волны процессов (depends в шапке тикета); итог по процессам
    for p in procs:
        step_waves(p["steps"])
    real = [p["id"] for p in procs if not p["id"].startswith("orphans-")]
    deps = {}
    for pid in real:
        raw = tickets[pid].header.get("depends") or ""
        deps[pid] = [d for d in dict.fromkeys(P.tk_id(x) for x in re.findall(r"(?i)tk[-_]?\d+", raw)) if d in real and d != pid]
    pw = proc_waves(real, deps)

    def sortkey(p):
        return (p["id"].startswith("orphans-"), p.get("wave") or 0, p["state"] == "done", p["id"])

    def summary_of(p):
        """Строка-итог только из фактов плана: последний сделанный шаг и «N из M» (без текстов модели)."""
        if not p.get("has_plan") or BOARD_RE.search(p["title"]):
            return None
        done = [s for s in p["steps"] if s["state"] == "done"]
        if not done:
            return None
        return f"сделано: {done[-1]['title']} — {len(done)} из {len(p['steps'])}"

    for p in procs:
        st = p["steps"]
        run = next((s for s in st if s["state"] in ACTIVE), None)
        pq = [q for q in openq if q.get("process") == p["id"]]
        p.update(state=_pstate(st), step_now=_step_now(st), steps_total=len(st),
                 eta_min=run["eta_min"] if run else None,
                 wait_min=max((mins_since(q.get("since")) or 0 for q in pq), default=None) if pq else None,
                 wave=pw.get(p["id"]), depends=deps.get(p["id"], []))
        p["summary"] = summary_of(p)
    procs.sort(key=sortkey)
    for i, p in enumerate(procs, 1):
        p["n"] = i
    waves = wave_groups(procs)

    # --- общий прогресс: «прошло» — рабочее время по событиям процессов
    stamps = []
    for p in procs:
        if p["id"].startswith("orphans-"):
            continue
        for e in H.all_entries(tickets[p["id"]]):
            stamps.append(e.ts.timestamp())
        for s in (plans.get(p["id"]) or {}).get("steps", []):
            for k in ("started_at", "finished_at"):
                d = P.parse(s.get(k))
                if d:
                    stamps.append(d.timestamp())
        for q in allq:
            if q.get("process") == p["id"]:
                for k in ("since", "answered_at"):
                    d = P.parse(q.get(k))
                    if d:
                        stamps.append(d.timestamp())
    progress = progress_block([p for p in procs if p.get("plan") and p["state"] != "done"], stamps, now)

    # --- вопросы (неотвеченные)
    questions = []
    for i, q in enumerate(openq, 1):
        keys = {o.get("key") for o in q.get("options") or []}
        questions.append({"id": q["id"], "n": i, "process": q.get("process"), "from": q.get("from"), "on": q.get("on"),
                          "text": q.get("text"), "options": q.get("options") or [],
                          "default": q.get("default") if q.get("default") in keys else None,
                          "since": when(q.get("since")), "wait_min": mins_since(q.get("since"))})

    # --- машины
    mviews = []
    for mid in MORDER:
        m = mach.get(mid)
        if not m:
            continue
        runs = [(p, s) for p in procs if not p["id"].startswith("orphans-") for s in p["steps"]
                if s["on"] == mid and s["state"] in ACTIVE]
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
            now_ = {"state": s["state"], "text": s["title"] + eta}
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

    def by_ceo(tid, ts) -> bool:
        t = tickets.get(tid)
        return bool(t and ts and any(H._role_key(e.author) == "ceo" and abs((ts - e.ts).total_seconds()) < 2 for e in H.all_entries(t)))

    def about_board(tid, text) -> bool:  # про само табло в «Недавно» не пишем — только результаты счёта и задач
        t = tickets.get(tid)
        return bool(BOARD_RE.search(f"{text} {t.header.get('title', '') if t else ''}"))

    for p in procs:  # записи ролей из логов тикетов: заголовок записи без пересказа моделью, машина — по окну шага плана
        t = tickets.get(p["id"])
        if t is None or p["id"].startswith("orphans-"):
            continue
        for e in role_entries(t):
            if H._role_key(e.author) == "ceo" or now - e.ts.timestamp() > FEED_AGE_S or about_board(p["id"], e.text):
                continue
            feed.append((e.ts.timestamp(), {"time": H.news_time(e.ts), "state": "done", "on": on_at(p["id"], e.ts), "to": None,
                                            "text": fact(e), "_tid": p["id"], "_title": H.short_title(p["title"], 20)}))
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
    last = [f for _, f in feed[-6:]]
    for i in range(len(last) - 1, -1, -1):  # префикс задачи — только у самой свежей строки подряд идущих одной задачи
        d = last[i]
        tid, title = d.get("_tid"), d.pop("_title", None)
        if tid and not (i + 1 < len(last) and last[i + 1].get("_tid") == tid):
            d["text"] = f"{title}: {d['text']}"
    for d in last:
        d.pop("_tid", None)

    # --- заголовок, счётчики
    problems = [f"нет связи: {m['name']}" for m in machines if m["id"] != "deck" and m["state"] == "down"]
    if not disp_ok:
        problems.append("диспетчер стоит")
    elif not watch_ok:
        problems.append("сторож стоит")
    cnt = {k: 0 for k in COUNT_KEYS}
    for p in procs:
        for s in p["steps"]:
            cnt[s["state"]] = cnt.get(s["state"], 0) + 1
    if problems:
        head = {"state": "bad", "text": "; ".join(problems)}
    elif questions:
        head = {"state": "wait", "text": f"ждёт вас: {len(questions)}"}
    else:
        head = {"state": "ok", "text": plain.get("headline") or ("всё идёт" if sum(cnt[k] for k in ACTIVE) else "сейчас ничего не идёт")}
    for p in procs:
        p.pop("has_plan", None)
    return {"time": now_dt.strftime("%H:%M"), "built_ts": round(now, 1), "tick_s": getattr(H, "TICK_S", 5), "headline": head,
            "counters": cnt, "progress": progress, "tags": TAGS, "machines": mviews, "waves": waves, "processes": procs,
            "questions": questions, "feed": last}
