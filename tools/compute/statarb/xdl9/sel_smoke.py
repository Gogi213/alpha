#!/usr/bin/env python3
"""TK-493 S2 (П-37 r3.2 §14, §18; проверка 7): дымовая проба КОМАНД ВОЛН на панелях ФОРМИРОВАНИЯ — те же флаги, что в боевых командах волн (режим `trade` из sel_waves.build_cmd),
но панели ~/xdl9/form, фандинг ~/xdl9/funding_form, задания 01.10-14.10 (sigma-клоны), выходы в ~/xdl9/sel/waves/smoke_out. Торговое окно НЕ читается: любая команда, в которой
остался путь к панелям/фандингу торговли (`xdl9/trade`, `xdl9/funding/`), отвергается до запуска.

  python sel_smoke.py gen   - smoke_cmds.sh (по первой группе каждого вида, a0+a1 / a0) + проверка защит
Запуск на Deck: cd ~/xdl9/sel/waves && bash ../smoke_cmds.sh; результат - smoke_out/smoke.log (OK / FAIL по виду) и выходы всех флагов волны.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402
import sel_waves as W  # noqa: E402

HOME_REL = "$HOME/xdl9"
NL = chr(10)


def to_formation(cmd, jobs_trade, jobs_form, w):
    c = cmd
    c = c.replace(HOME_REL + "/trade", HOME_REL + "/form")
    c = c.replace(HOME_REL + "/funding/", HOME_REL + "/funding_form/").replace(HOME_REL + "/funding ", HOME_REL + "/funding_form ")
    c = c.replace("out/w%d/" % w, "smoke_out/")
    c = c.replace(jobs_trade, jobs_form)
    c = c.replace("--from-day %d --to-day %d" % (S.D_TRADE0, S.D_END), "--from-day %d --to-day %d" % (S.D_FORM0, S.D_TRADE0))
    c = c.replace("d2/", "smoke_d2/")
    return c


def guard(cmd):
    bad = [t for t in (HOME_REL + "/trade", HOME_REL + "/funding/", HOME_REL + "/funding ", "2500", "out/w") if t in cmd]
    if bad:
        raise SystemExit("торговое окно в дымовой команде (%s): %s" % (bad, cmd[:200]))


def gen():
    man = S.read_csv(os.path.join(S.DOCS, "waves_manifest.csv"))
    seen, cmds = set(), []
    for r in man:
        if r["kind"] in seen:
            continue
        seen.add(r["kind"])
        w = int(r["wave"])
        st = W.stem_of(r["cell"], r["ex_y"], r["ex_x"])
        jt = r["jobs_file"]
        if r["kind"] == "D2":
            cmd = to_formation(r["cmd"], jt, jt, w)                           # D2: список пар; задания по шардам пишет d2_diff_funding.py (сутки формирования)
        else:
            jf = "sigma/%s.jobs.csv" % st
            cmd = to_formation(r["cmd"], jt, jf, w).replace(r["cells_file"], "sigma/%s.cells.csv" % r["cell"].replace("|", "_"))
        guard(cmd)
        cmds.append((r["kind"], cmd))
    head = ["#!/usr/bin/env bash",
            "# TK-493 S2: дымовая проба команд волн на ФОРМИРОВАНИИ (cd ~/xdl9/sel/waves; bash ../smoke_cmds.sh)",
            "set -u", "THREADS=${THREADS:-8}",
            'tj() { seq -s, 0 $(( $(wc -l < "$1") - 2 )); }',
            "mkdir -p smoke_out smoke_d2", ": > smoke_out/smoke.log", ": > smoke_out/d2_diff.log"]
    lines = list(head)
    for k, c in cmds:
        lines.append("echo '== %s' >> smoke_out/smoke.log" % k)
        lines.append("%s >> smoke_out/smoke.log 2>&1 && echo OK %s >> smoke_out/smoke.log || echo FAIL %s >> smoke_out/smoke.log" % (c, k, k))
    with open(os.path.join(S.SEL, "smoke_cmds.sh"), "w", encoding="utf-8", newline=NL) as fh:
        fh.write(NL.join(lines) + NL)
    print("команд", len(cmds), [k for k, _ in cmds])


if __name__ == "__main__":
    {"gen": gen}[sys.argv[1]]()
