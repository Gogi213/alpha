#!/usr/bin/env python3
"""TK-484 проверка приёмки без новой сборки: итоги clippy и cargo test на calc (юниты alsched) по rc и логу, плюс
«Rust не менялся с проверенного дерева» (git diff 5e6b3919..HEAD по src/Cargo.* пуст). По ssh читает только rc-файл
и хвост лога (не обходит /data): rc == 0, в логе clippy — «Finished», в логе test — «1198 passed; 0 failed».
usage: remote_check.py [ХОСТ=root@89.163.242.211] [CLIPPY=1103003646112] [TEST=1103003823567] [ДЕРЕВО=5e6b3919]"""
import os, re, subprocess, sys

HOME_SSH = os.path.join(os.path.expanduser("~"), ".ssh")


def remote(host, job):
    """rc-файл и лог юнита alsched на calc → (rc, лог без построчного шума: строки Finished/test result)."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "UserKnownHostsFile=" + os.path.join(HOME_SSH, "known_hosts")]
    key = os.path.join(HOME_SSH, "id_rsa")
    if os.path.exists(key):
        cmd += ["-i", key]
    cmd += [host, "cat /data/sched/rc/%s; grep -E '^test result|Finished|^error|^warning' /data/sched/logs/%s.log" % (job, job)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if out.returncode not in (0, 1):  # grep без совпадений — 1
        raise RuntimeError("ssh rc %d: %s" % (out.returncode, out.stderr.strip()))
    lines = out.stdout.strip().splitlines()
    return lines[0].strip(), "\n".join(lines[1:])


def rust_unchanged(tree):
    """Rust-код и зависимости с проверенного дерева не менялись (иначе rc относятся к другому коду)."""
    r = subprocess.run(["git", "diff", "--stat", tree, "HEAD", "--", "src", "Cargo.toml", "Cargo.lock"],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode == 0 and not r.stdout.strip()


def main(argv):
    host = argv[1] if len(argv) > 1 else "root@89.163.242.211"
    clippy = argv[2] if len(argv) > 2 else "1103003646112"
    test = argv[3] if len(argv) > 3 else "1103003823567"
    tree = argv[4] if len(argv) > 4 else "5e6b3919"
    bad = []
    rc_c, log_c = remote(host, clippy)
    rc_t, log_t = remote(host, test)
    if rc_c != "0":
        bad.append("clippy rc %s != 0" % rc_c)
    if "Finished" not in log_c or re.search(r"^(error|warning)", log_c, re.M):
        bad.append("лог clippy: нет Finished или есть error/warning")
    if rc_t != "0":
        bad.append("test rc %s != 0" % rc_t)
    m = re.search(r"test result: ok\. (\d+) passed; (\d+) failed", log_t)
    if not m or m.group(2) != "0":
        bad.append("лог test: %s" % (m.group(0) if m else "нет test result"))
    if not rust_unchanged(tree):
        bad.append("src/Cargo.* изменены после %s — rc относятся к другому коду" % tree)
    print("clippy rc %s | test rc %s, passed %s, failed %s | Rust с %s без изменений: %s"
          % (rc_c, rc_t, m.group(1) if m else "?", m.group(2) if m else "?", tree, rust_unchanged(tree)))
    for b in bad:
        print("НАРУШЕНИЕ:", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
