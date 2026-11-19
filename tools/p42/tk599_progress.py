#!/usr/bin/env python3
"""TK-599: прогресс основного прогона для страницы владельца (TK-613). Только чтение: tk599-run не трогает.

    python3 tk599_progress.py [--root /data/p42-tk599] [--code /data/p42-tk599/code3] [--out /data/p42-tk599/progress.json]

Раз в запуск: живой повтор по кэшу (`tk599_replay.py trace --live`, ничего не пишет в кэш) → число шагов и процедур, дошедших до совместной
сетки; метки ready таблиц признаков по окнам; процесс, который идёт сейчас; история `progress/history.jsonl` → скорости; оценка конца.
Процедур 10 (по стороне: четыре свёртки + полная). Шагов всего — оценка: готовые + (оставшиеся процедуры × медиана числа шагов готовых).
Конец — вилка: нижняя граница по таблицам признаков (оставшиеся процедуры × таблиц на процедуру ÷ скорость), верхняя — линейно по процедурам от запуска.
"""
import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import time

GENS = 10
RUN_START = "2026-11-18T12:12:06+00:00"           # первый запуск tk599-run (код `code`); отсюда считается линейная оценка


def now():
    return dt.datetime.now(dt.timezone.utc)


def gmt4(t):
    return (t + dt.timedelta(hours=4)).strftime("%Y-%m-%d %H:%M GMT+4")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def stage(root):
    ps = sh("ps -eo etime,args | grep -E 'alpha-feat|alpha-master breakout|retitr_ftab.py build|rustfeat_run' | grep -v grep | grep -v tk610 | grep -v ga1-v2")
    for line in ps.splitlines():
        if "retitr_ftab.py build" in line or "alpha-feat" in line or "rustfeat_run" in line:
            parts = line.split()
            side = parts[parts.index("build") + 1] if "build" in parts else ""
            w = next((p.split("/")[4] for p in parts if p.startswith(root + "/ftab/")), "")
            return "таблица признаков (поток %s, окно %s): сборка alpha-feat" % (side or "?", w or "?")
        if "alpha-master breakout" in line:
            return "раунд движка (alpha-master breakout tick)"
    if os.path.exists(os.path.join(root, "run.rc")):
        return "прогон закончен"
    return "процедура в Python (между раундами)" if sh("systemctl is-active tk599-run") == "active" else "юнит не активен"


def ready_counts(root):
    out = {}
    for w in ("wa", "wb", "wc"):
        files = glob.glob(os.path.join(root, "ftab", w, "ready", "*"))
        out[w] = {"n": len(files), "mtimes": sorted(os.path.getmtime(f) for f in files)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/data/p42-tk599")
    ap.add_argument("--code", default="/data/p42-tk599/code3")
    ap.add_argument("--out", default="/data/p42-tk599/progress.json")
    ap.add_argument("--skip-trace", action="store_true", help="не запускать повтор, взять прошлый след")
    a = ap.parse_args()
    root, t = a.root, now()
    wd = os.path.join(root, "progress")
    os.makedirs(wd, exist_ok=True)
    tj = os.path.join(wd, "trace.json")
    if not a.skip_trace:
        env = dict(os.environ, PYTHONUTF8="1")
        subprocess.run(["nice", "-n", "19", sys.executable, os.path.join(a.code, "tools/p42/tk599_replay.py"), "trace", a.code, tj + ".new", "--root", root, "--live"],
                       cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(tj + ".new"):
            os.replace(tj + ".new", tj)
    tr = json.load(open(tj)) if os.path.exists(tj) else {"steps": [], "frontier": None, "done": False}
    steps = tr["steps"]
    n_steps = len(steps)
    joints = sum(1 for s in steps if s["kind"] == "joint")
    per_gen, cur = [], 0
    for s in steps:
        cur += 1
        if s["kind"] == "joint":
            per_gen.append(cur)
            cur = 0
    srt = sorted(per_gen) or [35]
    mean_gen = srt[len(srt) // 2]                      # медиана числа шагов готовых процедур (у f1 проходов больше — медиана их отсекает)
    done = bool(tr.get("done")) or os.path.exists(os.path.join(root, "results", "results.pkl"))
    rem_gens = 0 if done else GENS - joints
    total_est = n_steps if done else int(round(n_steps + rem_gens * mean_gen))
    rc = open(os.path.join(root, "run.rc")).read().strip() if os.path.exists(os.path.join(root, "run.rc")) else None
    state = "finished" if (done and rc == "0") else ("failed" if rc not in (None, "0") else ("running" if sh("systemctl is-active tk599-run") == "active" else "stopped"))

    rc_ = ready_counts(root)
    streams = {w: v["n"] for w, v in rc_.items()}
    s_now = min(streams.values())
    hist_p = os.path.join(wd, "history.jsonl")
    hist = [json.loads(l) for l in open(hist_p)] if os.path.exists(hist_p) else []
    rec = {"t": t.isoformat(timespec="seconds"), "steps": n_steps, "gens": joints, "streams": s_now}
    if not hist or hist[-1]["steps"] != rec["steps"] or hist[-1]["streams"] != rec["streams"] or (t - dt.datetime.fromisoformat(hist[-1]["t"])).total_seconds() > 3000:
        with open(hist_p, "a") as f:
            f.write(json.dumps(rec) + "\n")
        hist.append(rec)

    def rate(key, hours):
        lo = [h for h in hist if (t - dt.datetime.fromisoformat(h["t"])).total_seconds() <= hours * 3600]
        if len(lo) < 2:
            return None
        dtm = (dt.datetime.fromisoformat(lo[-1]["t"]) - dt.datetime.fromisoformat(lo[0]["t"])).total_seconds() / 3600
        return None if dtm < 0.25 else round((lo[-1][key] - lo[0][key]) / dtm, 2)

    recent = [m for w in rc_.values() for m in w["mtimes"] if t.timestamp() - m <= 3 * 3600]
    streams_per_h = round(len(recent) / 3 / 3, 2)      # таблиц за 3 ч ÷ 3 окна = потоков в час
    # потоков на процедуру: готовых потоков (минус 2 базовых) ÷ процедур, дошедших до совместной сетки
    spg = (s_now - 2) / joints if joints else None
    elapsed_h = (t - dt.datetime.fromisoformat(RUN_START)).total_seconds() / 3600
    eta = {"low": None, "high": None}
    if not done and joints and streams_per_h > 0:
        eta["low"] = gmt4(t + dt.timedelta(hours=rem_gens * spg / streams_per_h))
    if not done and joints:
        eta["high"] = gmt4(t + dt.timedelta(hours=elapsed_h * rem_gens / joints))
    fr = tr.get("frontier") or {}
    out = {
        "updated_utc": t.isoformat(timespec="seconds"), "updated_gmt4": gmt4(t),
        "state": state, "stage": stage(root),
        "steps_done": n_steps, "steps_total_est": total_est, "pct": 100.0 if done else round(100.0 * n_steps / max(total_est, 1), 1),
        "procedures": {"total": GENS, "reached_joint": joints, "remaining": rem_gens, "median_steps_per_procedure": round(mean_gen, 1)},
        "frontier": {"missing_runs": fr.get("missing"), "requests": fr.get("requests")} if fr else None,
        "ftab_streams_ready_per_window": streams, "ftab_streams_per_h_3h": streams_per_h, "ftab_streams_per_procedure": None if spg is None else round(spg, 2),
        "rate": {"steps_per_h_6h": rate("steps", 6), "procedures_per_h_6h": rate("gens", 6), "streams_per_h_6h": rate("streams", 6)},
        "eta": {"low_gmt4": eta["low"], "high_gmt4": eta["high"], "confidence": "низкая"},
        "restarts": len(glob.glob(os.path.join(root, "run.rc.crashed.*"))),
        "basis": ("Шаги — живой повтор по кэшу (tk599_replay trace --live): шаг оси/совместной сетки, процедур 10 = 2 стороны × (4 свёртки + полная). "
                  "Всего шагов — оценка: готовые + оставшиеся процедуры × %.0f (медиана у готовых). Конец: нижняя граница — оставшиеся процедуры × "
                  "потоков на процедуру ÷ скорость сборки потоков (движок не учтён); верхняя — линейно по процедурам с запуска 18.11 12:12 UTC. "
                  "Прежняя оценка конца (03–04 GMT+4 19.11) не сбылась — вилка грубая." % mean_gen),
        "trace_age_min": round((time.time() - os.path.getmtime(tj)) / 60, 1) if os.path.exists(tj) else None,
    }
    tmp = a.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    print(json.dumps(out, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
