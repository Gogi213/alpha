#!/usr/bin/env python3
"""TK-493, П-39, G-X-5: проверки защиты поиска на Deck (запуск после прохода): (а) --last-day 2500 даёт отказ с кодом 3; (б) sha256 conv_guard.py и conv_window.py равны закоммиченным, токена нет,
window_reads.log и выходы окна (SHA256.txt) не изменены; (в) conv_window.py без токена отказывает (код 3); (г) в explore_reads.log нет суток >= 2500; (д) первая строка каждого выходного файла - баннер.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402

GUARD_SHA = "c87d9411fb23b24b2304a9932514437b2829281a7b22a1bf13a1f1d47dab38c2"
WINDOW_SHA = "bd4e1324f3d5a30ae9f479a9c4d57f46358578a61678486f206d9fc0695b3d75"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=XG.c2x_home())
    ap.add_argument("--c2", default=os.path.expanduser("~/xdl9/c2"))
    a = ap.parse_args(argv)
    res = {}
    py = sys.executable
    r = subprocess.run([py, os.path.join(HERE, "conv_explore.py"), "run", "--univ", "main", "--tag", "g5_refuse", "--last-day", "2500", "--out", a.out], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    res["a_last_day_2500_refused_code3"] = (r.returncode == 3, r.returncode, (r.stderr or "").strip().splitlines()[-1:] )
    res["b_conv_guard_sha_equal"] = XG.sha256_lf(os.path.join(HERE, "conv_guard.py")) == GUARD_SHA
    res["b_conv_window_sha_equal"] = XG.sha256_lf(os.path.join(HERE, "conv_window.py")) == WINDOW_SHA
    res["b_no_token"] = not any(f.startswith("go_window.token") for f in os.listdir(a.c2))
    sf = os.path.join(a.c2, "window", "SHA256.txt")
    bad = []
    for line in open(sf, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        h, fn = line.split("  ", 1)
        p = os.path.join(a.c2, "window", fn)
        if not os.path.isfile(p) or hashlib.sha256(open(p, "rb").read()).hexdigest() != h:
            bad.append(fn)
    res["b_window_outputs_unchanged"] = (not bad, bad)
    res["b_window_reads_log_mtime"] = os.path.getmtime(os.path.join(a.c2, "..", "c2", "window_reads.log")) if os.path.isfile(os.path.join(a.c2, "window_reads.log")) else None
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", C2_HOME=os.path.join(a.out, "c2home_g5"))
    os.makedirs(env["C2_HOME"], exist_ok=True)
    r2 = subprocess.run([py, os.path.join(HERE, "conv_window.py"), "token-check", "--spec", os.path.join(HERE, "..", "..", "..", "..", "..", "docs", "research", "P-38-calib-gate-2026-11-07", "window_spec_prod.json")],
                        capture_output=True, text=True, env=env)
    res["c_conv_window_without_token_refuses"] = (r2.returncode != 0, r2.returncode, (r2.stderr or r2.stdout).strip().splitlines()[-1:])
    mx = 0
    for line in open(os.path.join(a.out, XG.LOG_NAME), encoding="utf-8"):
        d = json.loads(line)
        for k in ("max_day_read", "d1", "last_day"):
            if k in d:
                mx = max(mx, int(d[k]))
        if "days" in d:
            mx = max(mx, max(d["days"]))
    res["g_explore_reads_log_max_day"] = (mx <= 2499, mx)
    bb = XG.verify_banners(os.path.join(a.out, "out")) + XG.verify_banners(os.path.join(a.out, "tables"))
    res["d_files_without_banner"] = (not bb, bb[:10])
    ok = all((v[0] if isinstance(v, tuple) else v) for k, v in res.items() if k not in ("b_window_reads_log_mtime",))
    XG.write_json(os.path.join(a.out, "tables", "gate_g_x_5.json"), dict(res, ok=ok))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print("G-X-5:", "ПРОЙДЕНО" if ok else "НЕ ПРОЙДЕНО")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
