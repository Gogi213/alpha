#!/usr/bin/env python3
"""Снимок конфига прогона (TK-068), работает на сервере счёта под benchrun*.sh.
  snap.py begin <класс> <команда…>  -> печатает путь манифеста /data/registry/runs/<ts>-<pid>.json
  snap.py end <манифест> <rc>       -> дописывает итог и строку в /data/registry/auto.jsonl
Манифест = всё, чтобы повторить прогон: cmdline, env (ALPHA_*/PREWARM_*/EVENTS_*/…), файлы-скрипты и списки клеток
(копии по sha256 в /data/registry/files/<sha>), sha входных данных (csv/…), md5 бинарников, git-коммит скриптов, если есть."""
import hashlib, json, os, re, shlex, subprocess, sys, time, socket

REG = os.environ.get("REG_DIR", "/data/registry")
ENVRE = re.compile(r"^(ALPHA_|PREWARM_|EVENTS_|BENCH_|FAST_|SIG_|EVENT_|RUST_|LOB_|HOLD_)")
PATHRE = re.compile(r"(/(?:data|opt|root|usr/local|home)/[A-Za-z0-9_./+-]+)")
MAXCOPY = 2_000_000      # файлы до 2 МБ копируем целиком (скрипты, клетки)
MAXHASH = 300_000_000    # до 300 МБ — sha256 (пул csv, verdict.csv); крупнее — только размер и mtime


def sha(p, n=None):
    h = hashlib.sha256() if n is None else hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def collect(args):
    seen, files, inputs, bins = set(), {}, {}, {}
    queue = []
    for a in args:
        queue += PATHRE.findall(a) + ([a] if os.path.isabs(a) else [])
    import glob
    for g in os.environ.get("REG_GLOBS", "").split(":"):
        queue += sorted(glob.glob(g)) if g else []
    depth = {p: 0 for p in queue}
    while queue:
        p = queue.pop(0).rstrip(".,;:)\"'")
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        sz = os.path.getsize(p)
        if os.access(p, os.X_OK) and sz > MAXCOPY:
            bins[p] = {"md5": sha(p, 5), "size": sz}
        elif sz <= MAXCOPY:
            try:
                txt = open(p, "rb").read()
            except OSError:
                continue
            s = hashlib.sha256(txt).hexdigest()
            os.makedirs(f"{REG}/files", exist_ok=True)
            dst = f"{REG}/files/{s}"
            if not os.path.exists(dst):
                open(dst, "wb").write(txt)
            files[p] = {"sha256": s, "size": sz}
            if depth.get(p, 0) < 3 and re.search(rb"^#!|\bbounce-grid\b|\.sh\b", txt[:4000]):
                for q in PATHRE.findall(txt.decode("utf-8", "replace")):
                    if q not in seen:
                        depth[q] = depth.get(p, 0) + 1
                        queue.append(q)
        elif sz <= MAXHASH:
            inputs[p] = {"sha256": sha(p), "size": sz}
        else:
            inputs[p] = {"size": sz, "mtime": int(os.path.getmtime(p)), "sha256": None}
    return files, inputs, bins


def begin(cls, argv):
    os.makedirs(f"{REG}/runs", exist_ok=True)
    t = time.strftime("%Y%m%dT%H%M%S")
    path = f"{REG}/runs/{t}-{os.getpid()}.json"
    files, inputs, bins = collect(argv)
    man = {"host": socket.gethostname(), "cls": cls, "start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "t0": time.time(),
           "cmdline": " ".join(shlex.quote(a) for a in argv), "cwd": os.getcwd(),
           "env": {k: v for k, v in sorted(os.environ.items()) if ENVRE.match(k)},
           "files": files, "inputs": inputs, "binaries": bins}
    for d in ("/opt/alpha-compute", "/data/alpha-src", "/opt/alpha"):
        if os.path.isdir(d + "/.git"):
            try:
                man["git"] = subprocess.run(["git", "-C", d, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                pass
            break
    json.dump(man, open(path, "w"), ensure_ascii=False, indent=1)
    print(path)


def end(path, rc):
    man = json.load(open(path))
    man["rc"] = int(rc)
    man["wall_s"] = round(time.time() - man.pop("t0"), 1)
    json.dump(man, open(path, "w"), ensure_ascii=False, indent=1)
    with open(f"{REG}/auto.jsonl", "a") as f:
        f.write(json.dumps({"host": man["host"], "cls": man["cls"], "start": man["start"], "pid": int(re.search(r"-(\d+)\.json$", path)[1]),
                            "wall_s": man["wall_s"], "rc": man["rc"], "cmd": man["cmdline"][:400], "manifest": os.path.basename(path)}, ensure_ascii=False) + "\n")
    try:     # TK-081: результат — в журнал отпечатков автоматически
        import guard
        os.chdir(man.get("cwd") or ".")
        guard.done(man["cls"], man["rc"], shlex.split(man["cmdline"]), wall_s=man["wall_s"],
                   result=os.environ.get("GUARD_RESULT") or man.get("cwd"))
    except Exception as e:
        print(f"snap: журнал отпечатков не записан: {e}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "begin":
        begin(sys.argv[2], sys.argv[3:])
    else:
        end(sys.argv[2], sys.argv[3])
