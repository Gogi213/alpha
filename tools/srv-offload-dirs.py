#!/usr/bin/env python3
"""TK-608: перенести холодные каталоги /data/<имя> на Storage Box, сверить, и только потом (--delete) удалить на srv.
Все каталоги одного запуска идут ОДНИМ rsync -aH (жёсткие ссылки между ними сохраняются: арбитражные каталоги делят файлы).
Сверка каждого каталога: (1) локальная сводка до и после копии равна (число файлов/ссылок, байты, макс. mtime) — не менялся;
(2) `ls -lAR` на боксе: число файлов, ссылок и сумма байт = локальным; (3) rsync -aHn --itemize-changes ничего не находит;
(4) --sample случайных файлов: sha256 локально заново = `sha256sum` на боксе. Удаляется каталог только при всех четырёх.
Отказ (не копировать, не удалять): каталог из PROTECTED, на него ссылается ЗАПУЩЕННЫЙ (is-active) юнит из /etc/systemd/system
или в нём/на него стоит cwd / открытый файл процесса / путь в cmdline. Строка JSON на каталог — в --manifest.
Запуск (долгий — только через systemd-run): srv-offload-dirs.py --box-root offload-srv-2026-11-18 --manifest M.jsonl [--delete] ДИР..."""
import argparse, glob, hashlib, json, os, random, re, shutil, stat, subprocess, sys, time
DATA = "/data"
PROTECTED = {"p42-tk599", "p42-tk596", "p42-past", "p42-past06", "p42-holdout", "p42-tk604", "p42-tk598", "p42-t566", "tk594",
             "tk592", "p42-book", "p42-premium", "p42-metrics", "klines", "aggtrades", "collect", "turbo-probe", "perf", "health",
             "pages", "upload-state", "arb-tab", "p42", "p42v2", "p42-retitr", "p42-charts"}
ap = argparse.ArgumentParser()
ap.add_argument("dirs", nargs="+"); ap.add_argument("--box-root", required=True); ap.add_argument("--manifest", required=True)
ap.add_argument("--sample", type=int, default=20); ap.add_argument("--bwlimit", type=int, default=40000, help="КБ/с")
ap.add_argument("--delete", action="store_true"); a = ap.parse_args()
def log(*x): print(time.strftime("%H:%M:%S"), *x, flush=True)
def ssh(cmd, timeout=3600):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "box", cmd], capture_output=True, text=True, timeout=timeout)
def scan(d):
    """Локальная сводка каталога без перехода по ссылкам."""
    reg = lnk = size = mt = 0; files = []
    for r, ds, fs in os.walk(f"{DATA}/{d}"):
        for f in fs:
            p = os.path.join(r, f); st = os.lstat(p)
            if stat.S_ISREG(st.st_mode): reg += 1; size += st.st_size; mt = max(mt, st.st_mtime_ns); files.append(p)
            elif stat.S_ISLNK(st.st_mode): lnk += 1
        for dn in ds:
            if os.path.islink(os.path.join(r, dn)): lnk += 1
    return {"files": reg, "symlinks": lnk, "bytes": size, "max_mtime_ns": mt}, files
def busy(d):
    p = f"{DATA}/{d}"; why = []
    for u in glob.glob("/etc/systemd/system/*.service") + glob.glob("/etc/systemd/system/*.timer"):
        try:
            if re.search(re.escape(p) + r"([^A-Za-z0-9_-]|$)", open(u, errors="replace").read()):   # остановленный юнит каталог не держит
                if subprocess.run(["systemctl", "is-active", "--quiet", os.path.basename(u)]).returncode == 0: why.append(f"юнит {os.path.basename(u)}")
        except OSError: pass
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            links = [os.readlink(f"/proc/{pid}/cwd")] + [os.readlink(f"/proc/{pid}/fd/{x}") for x in os.listdir(f"/proc/{pid}/fd")]
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError: continue
        if any(l == p or l.startswith(p + "/") for l in links) or re.search(re.escape(p) + r"([^A-Za-z0-9_-]|$)", cmd):
            if pid != str(os.getpid()) and "srv-offload-dirs" not in cmd: why.append(f"процесс {pid}: {cmd[:80]}")
    return why
def box_summary(d):
    r = ssh(f"ls -lAR {a.box_root}/{d}")
    reg = lnk = size = 0
    for line in r.stdout.splitlines():
        m = re.match(r"^([-l])\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s", line)
        if m:
            if m.group(1) == "-": reg += 1; size += int(m.group(2))
            else: lnk += 1
    return {"files": reg, "symlinks": lnk, "bytes": size}, r.returncode
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
todo, before, allfiles = [], {}, {}
for d in a.dirs:
    if "/" in d or d in PROTECTED or not os.path.isdir(f"{DATA}/{d}"): log(f"ОТКАЗ {d}: защищён/не каталог/не /data/<имя>"); continue
    w = busy(d)
    if w: log(f"ОТКАЗ {d}: занят — {w[:3]}"); continue
    before[d], allfiles[d] = scan(d); todo.append(d); log(f"до копии {d}: {before[d]}")
if not todo: sys.exit("нечего копировать")
ssh(f"mkdir -p {a.box_root}")
t0 = time.time()
cmd = ["rsync", "-aH", "--partial", f"--bwlimit={a.bwlimit}", "-e", "ssh -o BatchMode=yes"] + todo + [f"box:{a.box_root}/"]
log("rsync:", " ".join(todo)); r = subprocess.run(cmd, cwd=DATA, capture_output=True, text=True)
log(f"rsync rc={r.returncode} за {time.time()-t0:.0f} с {r.stderr.strip()[-300:]}")
for d in todo:
    rec = {"dir": d, "box_path": f"/home/{a.box_root}/{d}", "local_before": before[d], "rsync_rc": r.returncode, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    why = None
    if r.returncode != 0: why = f"rsync rc={r.returncode}"
    else:
        after, _ = scan(d); rec["local_after"] = after
        if after != before[d]: why = "каталог менялся во время копии"
    if not why:
        bs, rc = box_summary(d); rec["box"] = bs
        if rc != 0 or any(bs[k] != before[d][k] for k in ("files", "symlinks", "bytes")): why = f"бокс {bs} != локально {before[d]}"
    if not why:
        dry = subprocess.run(["rsync", "-aHn", "--itemize-changes", "-e", "ssh -o BatchMode=yes", d, f"box:{a.box_root}/"], cwd=DATA, capture_output=True, text=True)
        diff = [l for l in dry.stdout.splitlines() if l.strip()]
        rec["rsync_dry_diff_lines"] = len(diff)
        if dry.returncode != 0 or diff: why = f"повторный rsync -n видит расхождения: {diff[:3]}"
    if not why:
        pick = random.sample(allfiles[d], min(a.sample, len(allfiles[d]))); rel = [os.path.relpath(p, DATA) for p in pick]
        out = ssh("sha256sum " + " ".join(f"{a.box_root}/{x}" for x in rel))
        rem = {l.partition("  ")[2].strip(): l.partition("  ")[0] for l in out.stdout.splitlines()}
        bad = [x for p, x in zip(pick, rel) if rem.get(f"{a.box_root}/{x}") != sha256(p)]
        rec["sample"] = {"n": len(pick), "bad": len(bad)}
        if bad or len(rem) != len(pick): why = f"sha256 выборки: {len(bad)} из {len(pick)} не сошлись {bad[:2]}"
    rec["verified"] = why is None; rec["why_not"] = why; rec["deleted"] = False
    if why is None and a.delete:
        shutil.rmtree(f"{DATA}/{d}"); rec["deleted"] = True
    log(f"{'OK ' if why is None else 'FAIL'} {d}: {rec.get('box') or ''} sample={rec.get('sample')} {'удалён' if rec['deleted'] else ''} {why or ''}")
    open(a.manifest, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
log(f"свободно на /data: {shutil.disk_usage(DATA).free/1e9:.1f} ГБ")
