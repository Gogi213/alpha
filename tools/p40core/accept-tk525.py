#!/usr/bin/env python3
"""TK-525: приёмка хвоста L1 CHFT одной командой с ПК: `python tools/p40core/accept-tk525.py [--smoke]` (по образцу accept-tk526.py).

Всё идёт на srv (5.231.25.224) через ssh, на ПК только ssh; дерево orch — /root/p40core-tk525o. Шаги:
1) заливает orch.py, test_orch_l1.py, srv-tk525-*.sh из ветки на srv (LF);
2) п.4: `python3 test_orch_l1.py` (в т. ч. .tr.ok Binance и --l1-aux);
3) п.1: srv-tk525-hl-probe.sh — без SNAPSEQ rc=3 (STRICT), с SNAPSEQ rc=0 и 24 часа, пустой вход rc=0;
4) п.5: /data/p40l1/audit_rc525.json — exp = ok_l1 + fail по каждой бирже, ok_empty/nodir/partial нет, fail — только названные в аргументах `ex/СИМВОЛ/ДЕНЬ` (по умолчанию пусто; список — /data/p40l1/audit525_fail.txt), tr_any = ok_l1 (сделки у каждых суток);
5) п.2: у Bitget fail 0 (CARRY); п.3: Binance tr_ok = ok_l1;
6) `--smoke`: srv-tk525-orch-smoke.sh (DOGE Binance×Bybit 28.09, K1 through, L1 = сырьё) — код 0 и 5.aux/exits/fills.csv на месте.
Код 0 — всё зелёное; 1 — шаг красный; 2 — не залилось."""
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

D = Path(__file__).resolve().parent
R = "/root/p40core-tk525o"
SSH = [shutil.which("ssh") or "ssh", "-o", "BatchMode=yes", "srv"]


def ssh(cmd: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(SSH + [cmd], input=data, capture_output=True)


def lf(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def push() -> bool:
    buf = io.BytesIO()
    names = ["orch.py", "test_orch_l1.py", *(p.name for p in sorted(D.glob("srv-tk525-*.sh")))]
    with tarfile.open(fileobj=buf, mode="w") as t:
        for n in names:
            b = lf(D / n)
            ti = tarfile.TarInfo(n)
            ti.size = len(b)
            ti.mode = 0o755 if n.endswith(".sh") else 0o644
            t.addfile(ti, io.BytesIO(b))
    r = ssh(f"tar xf - -C {R}", buf.getvalue())
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
    return r.returncode == 0


def run(name: str, cmd: str) -> subprocess.CompletedProcess:
    r = ssh(f"bash -c '{cmd}'")
    print(f"== {name}: код {r.returncode}")
    sys.stdout.write(r.stdout.decode(errors="replace"))
    sys.stderr.write(r.stderr.decode(errors="replace"))
    return r


def hl_ok(out: str) -> bool:
    need = ["без SNAPSEQ rc=3", "SNAPSEQ rc=0 часов=24", "пустой вход rc=0"]
    return all(any(n in line and not (n == "SNAPSEQ rc=0 часов=24" and "без" in line) for line in out.splitlines()) for n in need)


def audit_problems(j: dict, allow: set[str]) -> list[str]:
    bad = []
    for ex, a in j.items():
        fail = a.get("fail", 0)
        if a["exp"] != a["ok_l1"] + fail:
            bad.append(f"{ex}: exp {a['exp']} ≠ ok_l1 {a['ok_l1']} + fail {fail}")
        for k in ("ok_empty", "nodir", "partial_no_ok"):
            if a.get(k):
                bad.append(f"{ex}: {k}={a[k]}")
        if a["tr_any"] != a["ok_l1"]:
            bad.append(f"{ex}: сделки tr_any {a['tr_any']} ≠ ok_l1 {a['ok_l1']}")
        if ex == "binance_futures" and a.get("tr_ok") != a["ok_l1"]:
            bad.append(f"{ex}: .tr.ok {a.get('tr_ok')} ≠ ok_l1 {a['ok_l1']}")
        if ex == "bitget_futures" and fail:
            bad.append(f"{ex}: fail {fail} (CARRY)")
    return bad


def main() -> int:
    smoke = "--smoke" in sys.argv
    allow = {a for a in sys.argv[1:] if a != "--smoke"}
    if not push():
        return 2
    bad = 0
    r = run("test_orch_l1.py", f"cd {R} && nice -n 19 python3 test_orch_l1.py 2>&1 | tail -8; exit ${{PIPESTATUS[0]}}")
    bad |= r.returncode != 0
    r = run("п.1 HL STRICT/SNAPSEQ", f"bash {R}/srv-tk525-hl-probe.sh 2>&1")
    ok = r.returncode == 0 and hl_ok(r.stdout.decode(errors="replace"))
    print("HL:", "ок" if ok else "красный")
    bad |= not ok
    r = ssh("cat /data/p40l1/audit_rc525.json")
    if r.returncode:
        print("== аудит: audit_rc525.json не прочитан")
        bad = 1
    else:
        probs = audit_problems(json.loads(r.stdout), allow)
        fl = ssh("cat /data/p40l1/audit525_fail.txt").stdout.decode(errors="replace").splitlines()
        named = {"/".join(x.split("	")[:3]) for x in fl if x.strip()}
        probs += [f"fail не разрешён: {k}" for k in sorted(named - allow)]
        print("== аудит (audit_rc525.json):", "ок" if not probs else "красный")
        for p in probs:
            print("  ", p)
        bad |= bool(probs)
    if smoke:
        r = run("смоук L1=сырьё", f"cd {R} && bash srv-tk525-orch-smoke.sh /tmp/tk525acc 2>&1 | tail -14; exit ${{PIPESTATUS[0]}}")
        r2 = ssh("ls /tmp/tk525acc/1/*/5.aux.csv /tmp/tk525acc/1/*/5.exits.csv /tmp/tk525acc/1/*/5.fills.csv")
        print("== файлы смоука:", "на месте" if r2.returncode == 0 else "нет")
        bad |= r.returncode != 0 or r2.returncode != 0
    print("ПРИЁМКА TK-525:", "отказ" if bad else "зелёная")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
