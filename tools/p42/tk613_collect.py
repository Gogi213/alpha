#!/usr/bin/env python3
"""П-42 TK-613: сборщик счётчиков задач для вкладки «Прогресс» страницы srv:8090.

Раз в минуту (таймер tk613-progress.timer) читает файлы и юниты srv — только чтение, ничего не трогает — и пишет один JSON
/data/pages/ml/data/tasks.json (его читает страница). Источники — только машинные счётчики, самоотчётов тредов нет:
  TK-607  /data/p42-tk607/storefill/fill.log («store-fill: N/84745 …»), report.txt DONE, юнит tk607*
  TK-599  юнит tk599-run, run.rc, results/, frozen.commit, progress.json (steps_done/steps_total; нет — шаги из switch/live2.out)
  TK-609  логи ворот /data/p42-tk609/ga*-<метка>*.log (конец «GAn rc=N»), юниты tk609*
  TK-611  /data/p42-tk611/ga.log (таблицы PASS/FAIL из 56), юнит tk611*
  TK-610  метки DONE этапов /data/p42-tk610 (репетиция R0/R1/cand, реальный счёт wa/wb/wc × R0/R1/R2), юнит tk610*
  srv     нагрузка, свободное место на /
Использование: tk613_collect.py [выходной.json]. Корень данных — $TK613_DATA (по умолчанию /data). Ничего не падает от отсутствующих файлов:
у задачи без данных состояние «нет данных».
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.environ.get("TK613_DATA", "/data")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TK613_OUT", f"{ROOT}/pages/ml/data/tasks.json")
HIST = os.environ.get("TK613_HIST", f"{ROOT}/p42-tk613/hist.json")
NB = " "
FILL_TOTAL_FALLBACK = 84745
GA_TOTAL = 56
ST = {"run": "идёт", "pause": "пауза", "wait": "ждёт", "done": "готово", "fail": "ошибка", "nodata": "нет данных"}


def P(*a):
    return os.path.join(ROOT, *a)


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def newest(paths):
    t = [m for m in (mtime(x) for x in paths) if m is not None]
    return max(t) if t else None


def read(path, tail=262144):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - tail))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def lines(path, tail=262144):
    s = read(path, tail)
    return [x for x in s.splitlines() if x.strip()] if s else []


def jload(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def n(v):
    return f"{int(v):,}".replace(",", NB)


def dec(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def to_ts(v):
    """epoch / ISO-строка → epoch секунд или None."""
    if isinstance(v, (int, float)) and v > 1e9:
        return float(v) / 1000 if v > 1e12 else float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"(\d{4}-\d\d-\d\d)[ T](\d\d):(\d\d)(?::\d\d)? ?GMT([+-]\d+)", v.strip())
        if m:
            d = datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3]}:00+00:00")
            return d.timestamp() - int(m[4]) * 3600
        try:
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            return None
    return None


def list_units():
    """[(имя без .service, active, sub)] по tk599*/tk607*/tk609*/tk610*/tk611*."""
    try:
        r = subprocess.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager", "tk599*", "tk607*", "tk609*", "tk610*", "tk611*"],
                           capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for ln in r.stdout.splitlines():
        c = ln.split()
        if len(c) >= 4:
            out.append((c[0].removesuffix(".service"), c[2], c[3]))
    return out


def running(units, prefix):
    return [u[0] for u in (units or []) if u[0].startswith(prefix) and u[1] in ("active", "activating", "reloading")]


def failed(units, prefix):
    return [u[0] for u in (units or []) if u[0].startswith(prefix) and u[1] == "failed"]


def hist_rate(hist, key, done, now, window=7200, min_span=600):
    """Скорость по истории замеров (done растёт во времени): (рост за окно) / (время). Пишет замер в hist."""
    h = hist.setdefault(key, [])
    if h and done < h[-1][1]:
        h.clear()
    if not h or h[-1][1] != done or now - h[-1][0] >= 120:
        if len(h) >= 2 and h[-1][1] == h[-2][1] == done:
            h[-1][0] = int(now)
        else:
            h.append([int(now), done])
    del h[:max(0, len(h) - 1500)]
    while h and h[0][0] < now - 86400:
        h.pop(0)
    old = [s for s in h if s[0] >= now - window]
    if not old:
        return None
    dt = now - old[0][0]
    if dt < min_span or done <= old[0][1]:
        return None
    return (done - old[0][1]) / dt


def row(tid, name, state, text="", **kw):
    d = {"id": tid, "name": name, "state": state, "state_text": ST[state], "done": None, "total": None, "pct": None, "text": text, "rate": None,
         "rate_unit": None, "eta_ts": None, "eta_hi_ts": None, "eta_note": None, "updated_ts": None, "updated_src": None, "stale_min": None, "stages": None}
    d.update(kw)
    if d["done"] is not None and d["total"]:
        d["pct"] = round(max(0.0, min(100.0, 100.0 * d["done"] / d["total"])), 1)
    return d


def finish(d, now, rate=None, unit=None, note=None, stale_after=1800, mul=1):
    """Скорость (rate — в единицах за секунду; показывается ×mul), ETA и «давно не обновлялось» — общая часть."""
    if d["state"] == "run":
        if rate and rate > 0:
            d["rate"], d["rate_unit"] = rate * mul, unit
            if d["total"] and d["done"] is not None and d["total"] > d["done"]:
                d["eta_ts"] = now + (d["total"] - d["done"]) / rate
                d["eta_note"] = note
        if d["updated_ts"] and now - d["updated_ts"] > stale_after:
            d["stale_min"] = int((now - d["updated_ts"]) // 60)
    return d


# ── TK-607: заполнение склада тиков ────────────────────────────────────────────────────────────────────────────────────────────────────
def tk607(units, hist, now):
    name = "Склад тиков: заполнение из архивов (ускоряет весь счёт)"
    d = P("p42-tk607", "storefill")
    log = f"{d}/fill.log"
    pat = re.compile(r"store-fill: (\d+)/(\d+) за ([\d.]+) с")
    pts = []
    last = None
    for ln in lines(log):
        m = pat.search(ln)
        if m:
            w = re.search(r"записано (\d+)", ln)
            pts.append((int(m[1]), float(m[3]), int(w[1]) if w else 0))
            last = ln
    rep = (read(f"{d}/report.txt", 20000) or "")
    ru = running(units, "tk607")
    done_flag = bool(re.search(r"\bDONE\b", rep))
    upd = newest([log, f"{d}/report.txt"])
    if not pts:
        st = "run" if ru else ("done" if done_flag else "nodata")
        return row("TK-607", name, st, "fill.log без строк «store-fill: N/…»", updated_ts=upd, updated_src="fill.log")
    N = pts[-1][0]
    total = int(re.search(pat, last)[2]) or FILL_TOTAL_FALLBACK
    num = lambda k: (re.search(k + r" (\d+)", last) or [None, None])[1]
    diffs, errs = num("различий"), num("ошибок")
    gb = re.search(r"склад ([\d.]+) ГБ", last)
    parts = [f"{n(N)} из {n(total)} файлов"]
    for k in ("записано", "пропущено"):
        if num(k) is not None:
            parts.append(f"{k} {n(num(k))}")
    if gb:
        parts.append(f"склад {dec(float(gb[1]), 2)} ГБ")
    parts.append(f"различий {diffs or 0}, ошибок {errs or 0}")
    bad = (diffs not in (None, "0")) or (errs not in (None, "0"))
    if done_flag or (N >= total and not ru):
        st = "fail" if bad else "done"
    elif bad:
        st = "fail"
    elif ru:
        st = "run"
    else:
        st = "pause"
    r = row("TK-607", name, st, " · ".join(parts), done=N, total=total, updated_ts=upd, updated_src="fill.log")
    if done_flag:
        r["text"] += " · report.txt: DONE"
    # скорость — записанных файлов в секунду по последним ~10 строкам лога: пропущенные (уже в складе) не работа, при перезапуске лог
    # быстро пролистывает их с начала; оставшиеся файлы считаются ещё не записанными
    rate = None
    win = pts[-10:]
    if len(win) >= 2 and win[-1][1] > win[0][1] and win[-1][2] > win[0][2]:
        rate = (win[-1][2] - win[0][2]) / (win[-1][1] - win[0][1])
    return finish(r, now, rate, "файл/с", "по скорости записи (без пропущенных) за последние ~10 строк лога; остаток считается ещё не записанным", stale_after=600)


# ── TK-599: полный счёт подбора ────────────────────────────────────────────────────────────────────────────────────────────────────────
def tk599(units, hist, now):
    name = "Подбор параметров пробоя: полный счёт (раунды по окнам)"
    b = P("p42-tk599")
    ru = running(units, "tk599")
    rc_txt = (read(f"{b}/run.rc", 100) or "").strip()
    prog = jload(f"{b}/progress.json")
    frozen = (read(f"{b}/frozen.commit", 200) or "").strip()
    results = os.path.isdir(f"{b}/results")
    live = f"{b}/switch/live2.out"
    live_txt = read(live, 4000) or ""
    done = total = None
    extra = []
    upd = None
    src = None
    p_eta, p_basis, total_est = (None, None), None, False
    if isinstance(prog, dict) and isinstance(prog.get("steps_done"), (int, float)):
        done = int(prog["steps_done"])
        tot = prog.get("steps_total", prog.get("steps_total_est"))
        total = int(tot) if isinstance(tot, (int, float)) and tot > 0 else None
        total_est = "steps_total" not in prog
        upd = to_ts(prog.get("updated_utc") or prog.get("updated") or prog.get("updated_at") or prog.get("ts")) or mtime(f"{b}/progress.json")
        src = "progress.json"
        if prog.get("stage"):
            extra.append(str(prog["stage"]))
        pr = prog.get("procedures") if isinstance(prog.get("procedures"), dict) else {}
        if isinstance(pr.get("remaining"), int) and isinstance(pr.get("total"), int):
            extra.append(f"процедур осталось {pr['remaining']} из {pr['total']}")
        fr = prog.get("frontier") if isinstance(prog.get("frontier"), dict) else {}
        if isinstance(fr.get("missing_runs"), int):
            extra.append(f"на границе не хватает {fr['missing_runs']} прогонов")
        eta = prog.get("eta") if isinstance(prog.get("eta"), dict) else {}
        p_eta = (to_ts(eta.get("low_gmt4") or eta.get("low") or prog.get("eta_ts")), to_ts(eta.get("high_gmt4") or eta.get("high")))
        p_basis = f"уверенность: {eta['confidence']}" if eta.get("confidence") else None
    else:
        m = re.search(r"шагов (\d+)", live_txt)
        if m:
            done = int(m[1])
            upd = mtime(live)
            src = "switch/live2.out"
            r = re.search(r"раундов (\d+)", live_txt)
            ms = re.search(r"'missing': (\d+)", live_txt)
            if r:
                extra.append(f"раундов {r[1]}")
            if ms:
                extra.append(f"на границе не хватает {ms[1]} прогонов")
        else:
            nb = len([x for x in (os.listdir(f"{b}/log") if os.path.isdir(f"{b}/log") else []) if re.match(r"b\d{4}-wa\.log$", x)])
            if nb:
                extra.append(f"пакетов bNNNN: {nb}")
            upd = newest([f"{b}/log"])
            src = "log/"
    if ru:
        st = "run"
    elif rc_txt:
        st = "done" if rc_txt == "0" else "fail"
    elif failed(units, "tk599"):
        st = "fail"
    else:
        st = "pause" if (done is not None or os.path.isdir(b)) else "nodata"
    parts = []
    if done is not None:
        parts.append(f"шагов {n(done)}" + (f" из ≈{n(total)}" if total and total_est else f" из {n(total)}" if total else " (всего: неизвестно)"))
    parts += extra
    if st in ("done", "fail") and rc_txt:
        parts.append(f"run.rc = {rc_txt}")
    if st == "pause":
        parts.append("юнит tk599-run не запущен, run.rc нет")
    if frozen:
        parts.append(f"заморожено: {frozen[:12]}")
    if results:
        parts.append("results/ есть")
    r = row("TK-599", name, st, " · ".join(parts), done=done, total=total, updated_ts=upd, updated_src=src)
    rate = hist_rate(hist, "TK-599", done, now) if done is not None else None
    r = finish(r, now, rate if st == "run" else None, "шагов/ч", "по росту числа шагов за последние ~2 ч", mul=3600)
    if st == "run" and p_eta[0]:
        r["eta_ts"], r["eta_hi_ts"] = p_eta
        r["eta_note"] = "оценка из progress.json (нижняя–верхняя граница" + (f"; {p_basis}" if p_basis else "") + ")"
    return r


# ── TK-611: сверка таблиц признаков ────────────────────────────────────────────────────────────────────────────────────────────────────
def tk611(units, hist, now, fill_state):
    name = f"Сверка таблиц признаков с эталоном ({GA_TOTAL} таблиц)"
    log = P("p42-tk611", "ga.log")
    res = {}
    why = {}
    for ln in lines(log):
        m = re.match(r"(\S+) (PASS|FAIL)\b(.*)", ln)
        if m:
            res[m[1]] = m[2]
            why[m[1]] = m[3].split("|")[0].strip()[:90]
    if not res and mtime(log) is None:
        return row("TK-611", name, "nodata", "ga.log нет", total=GA_TOTAL)
    ok = sum(1 for v in res.values() if v == "PASS")
    bad = [k for k, v in res.items() if v == "FAIL"]
    ru = running(units, "tk611")
    if ok >= GA_TOTAL:
        st = "done"
    elif ru:
        st = "run"
    elif bad and ok + len(bad) >= GA_TOTAL:
        st = "fail"
    else:
        st = "pause"
    parts = [f"{ok} PASS из {GA_TOTAL}", f"FAIL {len(bad)}"]
    if bad:
        parts.append("последний FAIL: " + bad[-1] + (f" ({why[bad[-1]]})" if why.get(bad[-1]) else ""))
    if st == "pause":
        parts.append("юнитов нет; пауза до заполнения склада (TK-607)" if fill_state != "done" else "юнитов нет")
    r = row("TK-611", name, st, " · ".join(parts), done=ok, total=GA_TOTAL, updated_ts=mtime(log), updated_src="ga.log")
    rate = hist_rate(hist, "TK-611", ok, now)
    r = finish(r, now, rate, "таблиц/ч", "по росту числа PASS за последние ~2 ч", mul=3600)
    return r


# ── TK-609: ворота быстрого расчёта таблиц признаков ───────────────────────────────────────────────────────────────────────────────────
def tk609(units, hist, now, fill_state):
    name = "Быстрый расчёт таблиц признаков: ворота сверки G-A1…G-A3"
    d = P("p42-tk609")
    ru = running(units, "tk609")
    tags = []
    try:
        for f in os.listdir(d):
            m = re.fullmatch(r"ga1-(.+)\.log", f)
            if m:
                tags.append((os.stat(f"{d}/{f}").st_mtime, m[1]))
    except OSError:
        return row("TK-609", name, "nodata", f"{d} нет")
    if not tags:
        return row("TK-609", name, "pause" if not ru else "run", "логов ворот серии нет", total=6)
    tag = max(tags)[1]
    gates = [("G-A1", f"ga1-{tag}.log"), ("G-A3", f"ga3-{tag}.log")] + [(f"G-A2 {w} {k}", f"ga2-{tag}-{w}-{k}.log") for k in ("deriv", "flow") for w in ("u0", "aug")]
    stages, ok_n, bad_n, cmp_n, diff_n, paths = [], 0, 0, 0, 0, []
    for lbl, f in gates:
        paths.append(f"{d}/{f}")
        ls = lines(f"{d}/{f}")
        for ln in ls:
            m = re.search(r"rows ref=\d+ new=\d+ total_diffs=(\d+)", ln)
            if m:
                cmp_n += 1
                diff_n += int(m[1])
        end = re.fullmatch(r".*\brc=(\d+)", ls[-1]) if ls else None
        if end:
            s = "done" if end[1] == "0" else "fail"
        elif ls:
            s = "run" if ru else "pause"
        else:
            s = "todo"
        ok_n += s == "done"
        bad_n += s == "fail"
        stages.append({"name": lbl, "st": s})
    if ru:
        st = "run"
    elif bad_n:
        st = "fail"
    elif ok_n == len(gates):
        st = "done"
    else:
        st = "pause"
    text = f"серия {tag}: ворот закончено {ok_n} из {len(gates)} · сверок таблиц {cmp_n}, расхождений {diff_n}"
    if bad_n:
        text += f" · ворот с rc≠0: {bad_n}"
    if st == "pause":
        text += " · юнитов tk609* нет; пауза до заполнения склада (TK-607)" if fill_state != "done" else " · юнитов tk609* нет"
    r = row("TK-609", name, st, text, done=ok_n, total=len(gates), updated_ts=newest(paths), updated_src=f"ga*-{tag}.log", stages=stages)
    return finish(r, now, None, None, stale_after=2400)


# ── TK-610: CatBoost на конфигурации TK-599 ────────────────────────────────────────────────────────────────────────────────────────────
def tk610(units, hist, now, frozen):
    name = "CatBoost на конфигурации TK-599: репетиция и реальный счёт"
    b = P("p42-tk610")
    if not os.path.isdir(b):
        return row("TK-610", name, "nodata", f"{b} нет")
    ru = running(units, "tk610")
    stages = []
    marks = []

    def stage(lbl, done_file, fail_file=None):
        marks.append(done_file)
        v = (read(done_file, 50) or "").strip()
        if v:
            s = "done" if v.split()[0] in ("0", "done") or re.match(r"\d{4}-\d\d-\d\d", v) else "fail"
        elif fail_file and os.path.exists(fail_file):
            s = "fail"
        elif os.path.exists(os.path.dirname(done_file)) and ru:
            s = "run"
        else:
            s = "todo"
        stages.append({"name": lbl, "st": s})
        return s

    out = f"{b}/out"
    stage("репетиция R0", f"{out}/dry-wc-R0/DONE", f"{out}/dry-wc-R0/FAIL")
    stage("репетиция R1", f"{out}/dry-wc-R1/DONE", f"{out}/dry-wc-R1/FAIL")
    stage("репетиция cand", f"{b}/dry/cand.DONE", f"{b}/dry/cand.FAIL")
    real = [f"{w}-{r}" for w in ("wa", "wb", "wc") for r in ("R0", "R1", "R2")]
    try:
        real += sorted(x for x in os.listdir(out) if not x.startswith("dry") and x not in real and os.path.isdir(f"{out}/{x}"))
    except OSError:
        pass
    for x in real:
        stage(x, f"{out}/{x}/DONE", f"{out}/{x}/FAIL")
    dn = sum(s["st"] == "done" for s in stages)
    fl = sum(s["st"] == "fail" for s in stages)
    dry_done = all(s["st"] == "done" for s in stages[:3])
    real_done = sum(s["st"] == "done" for s in stages[3:])
    if fl:
        st = "fail"
    elif dn == len(stages):
        st = "done"
    elif ru:
        st = "run"
    elif dry_done and not frozen:
        st = "wait"
    else:
        st = "pause"
    dry_txt = ", ".join(f"{s['name'].split()[-1]} {'✓' if s['st'] == 'done' else ('✗' if s['st'] == 'fail' else ('…' if s['st'] == 'run' else '–'))}" for s in stages[:3])
    text = f"этапов {dn} из {len(stages)} · репетиция: {dry_txt} · реальный счёт: {real_done} из {len(stages) - 3}"
    if st == "wait":
        text += " · ждёт заморозки TK-599 (frozen.commit)"
    elif st == "pause":
        text += " · юнитов tk610* нет"
        fresh = newest(marks + [f"{b}/dry", out])
        if fresh and now - fresh < 300:
            text += f"; но файлы меняются (последнее изменение {int(now - fresh)} с назад)"
    r = row("TK-610", name, st, text, done=dn, total=len(stages), updated_ts=newest(marks + [f"{b}/dry", out]), updated_src="метки DONE этапов", stages=stages)
    return finish(r, now, None, None, stale_after=3600)


def host(now):
    try:
        l1, l5, l15 = os.getloadavg()
    except (OSError, AttributeError):
        l1 = l5 = l15 = None
    try:
        du = shutil.disk_usage("/")
        disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1), "used_pct": round(100 * du.used / du.total, 1)}
    except OSError:
        disk = None
    return {"load": [round(x, 2) for x in (l1, l5, l15)] if l1 is not None else None, "cpus": os.cpu_count(), "disk": disk}


def safe(tid, name, fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — страница не должна ронять сборщик из-за одного источника
        return row(tid, name, "nodata", f"сборщик не смог прочитать источник: {type(e).__name__}: {e}"[:200])


def collect(now=None, units=None):
    now = now or time.time()
    units = list_units() if units is None else units
    hist = jload(HIST) or {}
    if not isinstance(hist, dict):
        hist = {}
    t607 = safe("TK-607", "Склад тиков", lambda: tk607(units, hist, now))
    fill = t607["state"]
    frozen = bool((read(P("p42-tk599", "frozen.commit"), 100) or "").strip())
    tasks = [safe("TK-599", "Подбор параметров пробоя", lambda: tk599(units, hist, now)), t607,
             safe("TK-609", "Быстрый расчёт таблиц признаков", lambda: tk609(units, hist, now, fill)),
             safe("TK-611", "Сверка таблиц признаков", lambda: tk611(units, hist, now, fill)),
             safe("TK-610", "CatBoost на конфигурации TK-599", lambda: tk610(units, hist, now, frozen))]
    return {"generated_ts": now, "host": host(now), "units_ok": units is not None, "tasks": tasks}, hist


def main():
    data, hist = collect()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    try:
        os.makedirs(os.path.dirname(HIST), exist_ok=True)
        with open(HIST + ".tmp", "w", encoding="utf-8") as f:
            json.dump(hist, f, separators=(",", ":"))
        os.replace(HIST + ".tmp", HIST)
    except OSError:
        pass
    print(OUT, " ".join(f"{t['id']}={t['state']}" for t in data["tasks"]))


if __name__ == "__main__":
    main()
