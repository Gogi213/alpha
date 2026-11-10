#!/usr/bin/env python3
"""TK-526: приёмка ядра θ_t одной командой с ПК: `python tools/p40core/accept-tk526.py`.

Тесты идут на srv (В-103; на ПК cargo нельзя), дерево /root/p40core-tk526:
1) заливает на srv orch.py, assemble.py, aux_feats.py и test_*.py из ветки (LF);
2) сверяет md5 исходников ядра (rs/Cargo.toml, rs/src/*.rs) на srv с веткой — расхождение = отказ (код 3);
3) на srv: `cargo test --release` (ядро), `python3 test_assemble.py`, `python3 test_orch_l1.py`.
Код 0 — только если все три зелёные; 1 — тест красный; 2 — не залилось; 3 — исходники на srv ≠ ветке."""
import os
import sys

os.environ.setdefault("P40_DIR", "/root/p40core-tk526")
import gate  # noqa: E402  (REMOTE читается из P40_DIR при импорте)

R = gate.REMOTE
STEPS = [
    ("cargo test --release (ядро)", f"export PATH=$PATH:/root/.cargo/bin; cd {R}/rs && nice -n 19 cargo test --release 2>&1 | tail -15; exit ${{PIPESTATUS[0]}}"),
    ("test_assemble.py", f"cd {R} && nice -n 19 python3 test_assemble.py 2>&1 | tail -6; exit ${{PIPESTATUS[0]}}"),
    ("test_orch_l1.py", f"cd {R} && nice -n 19 python3 test_orch_l1.py 2>&1 | tail -6; exit ${{PIPESTATUS[0]}}"),
]


def push() -> bool:
    import io
    import tarfile
    buf = io.BytesIO()
    names = ["orch.py", "assemble.py", "aux_feats.py", *(p.name for p in sorted(gate.D.glob("test_*.py")))]
    with tarfile.open(fileobj=buf, mode="w") as t:
        for n in names:
            b = gate.lf(gate.D / n)
            ti = tarfile.TarInfo(n)
            ti.size = len(b)
            ti.mode = 0o644
            t.addfile(ti, io.BytesIO(b))
    r = gate.ssh(f"tar xf - -C {R}", buf.getvalue())
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
    return r.returncode == 0


def main() -> int:
    if not push():
        return 2
    if not gate.sources_match():
        return 3
    bad = 0
    for name, cmd in STEPS:
        r = gate.ssh(f"bash -c '{cmd}'")
        print(f"== {name}: код {r.returncode}")
        sys.stdout.write(r.stdout.decode(errors="replace"))
        sys.stderr.write(r.stderr.decode(errors="replace"))
        bad |= r.returncode != 0
    print("ПРИЁМКА TK-526:", "отказ" if bad else "зелёная")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
