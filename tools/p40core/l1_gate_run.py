#!/usr/bin/env python3
"""TK-515: гейты G-L1a и G-L1b одной командой с ПК без оболочки и WSL: `python tools/p40core/l1_gate_run.py`.

1) заливает на 5.231.25.224 (P40_DIR, по умолчанию /root/p40core-tk515) l1_gate.sh, l1_gate_b.sh, l1_diff.py из ветки (LF);
2) сверяет md5 исходников ядра (rs/) и pq2bin (pq2bin/src, Cargo.toml) на сервере с веткой — расхождение = отказ (код 3);
3) запускает l1_gate.sh (G-L1a), затем l1_gate_b.sh (G-L1b); код выхода 0 — оба зелёные, 1 — отказ гейта, 2 — не залилось, 3 — исходники ≠ ветке."""
import hashlib
import io
import os
import subprocess
import sys
import tarfile

os.environ.setdefault("P40_DIR", "/root/p40core-tk515")  # до import gate: он читает P40_DIR при загрузке
import gate  # noqa: E402
from gate import D, REMOTE, SSH, lf, ssh  # noqa: E402

SCRIPTS = ["l1_gate.sh", "l1_gate_b.sh", "l1_diff.py"]


def push() -> bool:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for n in SCRIPTS:
            b = lf(D / n)
            ti = tarfile.TarInfo(n)
            ti.size = len(b)
            ti.mode = 0o755
            t.addfile(ti, io.BytesIO(b))
    r = ssh(f"tar xf - -C {REMOTE}", buf.getvalue())
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
    return r.returncode == 0


def pq2bin_match() -> bool:
    local = {p.relative_to(D / "pq2bin").as_posix(): hashlib.md5(lf(p)).hexdigest()
             for p in [D / "pq2bin" / "Cargo.toml", *sorted((D / "pq2bin" / "src").glob("*.rs"))]}
    r = ssh(f"cd {REMOTE}/pq2bin && for f in Cargo.toml src/*.rs; do echo \"$(tr -d '\r' < $f | md5sum | cut -c1-32)  $f\"; done")
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
        return False
    remote = {}
    for line in r.stdout.decode().splitlines():
        h, _, name = line.partition("  ")
        remote[name.strip()] = h
    bad = sorted(k for k in local.keys() | remote.keys() if local.get(k) != remote.get(k))
    for k in bad:
        print(f"ГЕЙТ: pq2bin/{k} на сервере ≠ ветке")
    return not bad


def run(script: str) -> int:
    p = subprocess.Popen(SSH + [f"Q={REMOTE} bash {REMOTE}/{script}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in p.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    return p.wait()


def main() -> int:
    if not push():
        return 2
    if not (gate.sources_match() and pq2bin_match()):
        return 3
    rc_a = run("l1_gate.sh")
    rc_b = run("l1_gate_b.sh")
    print(f"G-L1a rc={rc_a}, G-L1b rc={rc_b}")
    return 0 if rc_a == 0 and rc_b == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
