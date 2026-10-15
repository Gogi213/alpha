"""TK-244, В-9: вкладка «Прогоны» — состояние прогонов формы из файлов самого прогона и `alsched ps` (без обхода /data).
Источники: <run_dir>/L-*/run.json (заявка), days.txt (сутки к счёту), out/run-<мес>-<сутки>/{claim,done} (маркеры раннера day-cells.sh),
psim/closes-p14f1-cap{0,3}-<мес>.json (месяцы psim), result.json (отказ root-юнита), overlay.jsonl (посчитано, сервер) + канон Летописи;
запрос, ещё не взятый root-юнитом = <request_dir>/L-*.json («в очереди»). Чистые функции: время и ps приходят аргументами.
Веса этапов в общем % — НЕ придуманные: проход F1 П-14 (TK-231, 501 хеш × 251 суток): счёт ≈ 11,5 ч (11–12), psim ≈ 50 мин
(TK-231.md строки 98–128) => W_DAYS = 690/(690+50). Для малых прогонов формы доля psim не измерена (проба Г-27×янв: 6 мин всего) —
подпись «веса по проходу F1»; после первых боевых прогонов заменить замером (docs/findings/tk244-metrics-2026-10-15.md)."""
import datetime
import json
import os
import re

import metrics

CALC_MIN, PSIM_MIN = 690.0, 50.0   # TK-231: счёт 11–12 ч (середина), psim ≈ 50 мин — весь проход F1, 9 месяцев
W_DAYS = CALC_MIN / (CALC_MIN + PSIM_MIN)
W_PSIM = 1 - W_DAYS
TZ = datetime.timezone(datetime.timedelta(hours=4))   # GMT+4
STATUS = {"run": "идёт", "queue": "в очереди", "wait": "ждёт Летопись", "done": "в Летописи", "fail": "отказ"}

_PS = re.compile(r"^(?P<id>\S+)\s+(?P<state>\S+)\s+(?P<cls>\S+)\s+ядер=\s*(?P<cores>\d+)\s+(?P<name>\S+)\s+(?P<age>[\d.]+) мин rc=(?P<rc>\S*)\s*(?P<cmd>.*)$")
_PS_HEAD = re.compile(r"ядра: занято (\d+)/(\d+)")


def parse_ps(text):
    """Вывод `alsched ps` -> {busy, total, jobs:[{id,state,cls,cores,name,age_min,rc,cmd}]}; нераспознанные строки пропускаются."""
    out = {"busy": None, "total": None, "jobs": []}
    for line in (text or "").splitlines():
        h = _PS_HEAD.search(line)
        if h:
            out["busy"], out["total"] = int(h.group(1)), int(h.group(2))
            continue
        m = _PS.match(line.strip())
        if m:
            out["jobs"].append({"id": m["id"], "state": m["state"], "cls": m["cls"], "cores": int(m["cores"]), "name": m["name"],
                                "age_min": float(m["age"]), "rc": m["rc"], "cmd": m["cmd"]})
    return out


def foreign_jobs(ps, form_ids):
    """«Другие задания на calc (не из формы)»: идущие и ждущие, имя не среди прогонов формы."""
    return [j for j in ps["jobs"] if j["state"] in ("running", "queued") and j["name"] not in form_ids]


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def days_progress(run):
    """(готово, всего, время первого claim) — по маркерам out/run-*/done против days.txt. Всего None, пока раннер не записал days.txt."""
    total = None
    try:
        with open(os.path.join(run, "days.txt"), encoding="utf-8") as f:
            total = sum(1 for ln in f if ln.strip())
    except OSError:
        pass
    done, first = 0, None
    out = os.path.join(run, "out")
    try:
        names = os.listdir(out)
    except OSError:
        names = []
    for nm in names:
        if not nm.startswith("run-"):
            continue
        d = os.path.join(out, nm)
        try:
            t = os.stat(os.path.join(d, "claim")).st_mtime
            first = t if first is None else min(first, t)
        except OSError:
            pass
        if os.path.exists(os.path.join(d, "done")):
            done += 1
    return done, total, first


def psim_progress(run, months):
    """(месяцев psim готово, всего): месяц готов, когда есть closes и cap 0, и cap 3."""
    done = sum(1 for m in months if all(os.path.exists(os.path.join(run, "psim", f"closes-p14f1-cap{c}-{m}.json")) for c in (0, 3)))
    return done, len(months)


def percent(days, psim, finished):
    """Общий % — взвешенно по этапам (веса по проходу F1, см. шапку)."""
    if finished:
        return 100
    d, t = days
    p, q = psim
    return round(100 * (W_DAYS * (d / t if t else 0) + W_PSIM * (p / q if q else 0)))


def eta(now, days, psim, first_claim, est_s, ps_age_min=None):
    """-> (осталось мин | None, источник). До первых готовых суток — оценка заявки (назначено, не измерено); дальше — темп готовых суток."""
    d, t = days
    p, q = psim
    psim_left = PSIM_MIN * ((q - p) / q) if q else 0
    if t and d and first_claim and now > first_claim:
        pace = (now - first_claim) / 60 / d   # мин на сутки (стенные, все исполнители вместе)
        return round(pace * (t - d) + psim_left), "по темпу готовых суток"
    left = est_s / 60 - (ps_age_min or 0) if est_s else None
    return (None if left is None else max(0, round(left))), "оценка заявки (назначено, не измерено)"


def hhmm(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


def run_record(run, run_id, now, ps_job=None, request=None, in_canon=None, est_s=None):
    """Одна строка вкладки. run — каталог прогона (может не существовать: запрос в очереди). ps_job — строка alsched ps с name == run_id.
    request — содержимое запроса из request_dir (ещё не взят). in_canon — все хеши оверлея уже в Летописи (None — оверлея нет).
    -> dict: id, s (run|queue|wait|done|fail), hyps [{hyp, hm}], hm, days, psim, pct, cores, left_min, end, src, why."""
    meta = _json(os.path.join(run, "run.json")) or {}
    res = _json(os.path.join(run, "result.json"))
    months = sorted({m for ms in (meta.get("new_hm") or {}).values() for m in ms})
    days = days_progress(run)
    psim = psim_progress(run, months)
    rec = {"id": run_id, "hyps": meta.get("hyps") or (request or {}).get("hyps") or [], "hm": meta.get("hm_new"), "months": months,
           "days": [days[0], days[1]], "psim": list(psim), "cores": ps_job["cores"] if ps_job else None, "why": None,
           "t0": None, "left_min": None, "end": None, "src": None}
    if res is not None and not res.get("ok"):
        return rec | {"s": "fail", "why": res.get("why") or "отказ root-юнита", "pct": 0}
    overlay = os.path.exists(os.path.join(run, "overlay.jsonl")) and os.path.exists(os.path.join(run, "psim", "psim-p14f1.done"))
    if overlay:
        return rec | {"s": "done" if in_canon else "wait", "pct": 100, "left_min": 0}
    try:
        with open(os.path.join(run, "postload.log"), encoding="utf-8") as f:
            return rec | {"s": "fail", "why": f.read().strip()[:200], "pct": percent(days[:2], psim, False)}
    except OSError:
        pass
    pct = percent(days[:2], psim, False)
    st = ps_job["state"] if ps_job else None
    if st == "running":
        left, src = eta(now, days[:2], psim, days[2], est_s, ps_job["age_min"])
        return rec | {"s": "run", "pct": pct, "left_min": left, "src": src, "end": None if left is None else hhmm(now + left * 60)}
    if st == "done" and ps_job["rc"] not in ("", "0"):
        return rec | {"s": "fail", "why": f"alsched rc={ps_job['rc']}", "pct": pct}
    if st is None and meta:   # заявка была, итога нет, в очереди calc её нет — оборвано
        return rec | {"s": "fail", "why": "задание не в очереди calc, итога нет (оборвано)", "pct": pct}
    left, src = eta(now, days[:2], psim, None, est_s)
    return rec | {"s": "queue", "pct": 0, "left_min": left, "src": src}


def run_metrics(run):
    """{хеш: hash_metrics} за месяцы ЭТОГО прогона: overlay.jsonl (хеш, месяц, ext.cell_line) × psim/closes-p14f1-cap0-<мес>.json (cap 0, как
    pnl_usd Летописи). Читает только файлы прогона. Нет оверлея — {} (на странице прочерк)."""
    try:
        with open(os.path.join(run, "overlay.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        return {}
    closes, per = {}, {}
    for r in rows:
        ln = (r.get("ext") or {}).get("cell_line")
        if not ln:
            continue
        form, _, st = ln.partition(" ")   # как postload.build: вариант closes = «состояние@форма»
        m = r["month"]
        if m not in closes:
            closes[m] = _json(os.path.join(run, "psim", f"closes-p14f1-cap0-{m}.json")) or {}
        per.setdefault(r["cell_id"], {})[m] = closes[m].get(f"{st}@{form}", {}).get(m, {}).get("0", [])
    return {h: metrics.hash_metrics(t) for h, t in per.items()}
