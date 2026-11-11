#!/usr/bin/env python3
"""TK-530 (G-022, В-135): выгрузка закрытых суток коллектора на Storage Box. Общий для всех бирж.

srv `<src>/<ДЕНЬ UTC>/*` -> box `<box-dir>/<биржа>/<ДЕНЬ>/` + SHA256SUMS и UPLOADED.json рядом.
Закрытые сутки = день < сегодня UTC и ни один файл дня не менялся дольше --quiet-s (назначаемое, Судья).
Сверка — sha256 каждого файла на боксе (`ssh box sha256sum`) против локального, не счёт rsync.
Локальное удаляется только при .ok + неизменных size/mtime + свободно < --prune-free-gb (без флага — никогда).
Повтор безопасен: сверенные неизменные сутки пропускаются, rsync докачивает. Параллельный запуск — flock.
Состояние: <state>/<биржа>/<ДЕНЬ>.ok; здоровье: <health>/upload-<биржа>.json (schema 1).
Запуск: day_upload.py --exchange binance --src /data/bnws --quiet-s N [--bwlimit KBPS] [--prune-free-gb N]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # Windows: только тесты, боевой запуск — Linux (srv)
    fcntl = None

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUMS = "SHA256SUMS"
MARK = "UPLOADED.json"
SHA_CHUNK = 100  # файлов в одной команде sha256sum на боксе


class UploadError(Exception):
    pass


def utc_day(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


def day_files(day_dir):
    """Обычные файлы суток (без служебных SUMS/MARK): {имя: (размер, mtime_ns)}."""
    out = {}
    for n in sorted(os.listdir(day_dir)):
        p = os.path.join(day_dir, n)
        if n in (SUMS, MARK) or not os.path.isfile(p):
            continue
        st = os.stat(p)
        out[n] = (st.st_size, st.st_mtime_ns)
    return out


def closed_days(src, now, quiet_s):
    """Сутки, которые можно выгружать: прошли, непусты, файлы не менялись дольше quiet_s."""
    today = utc_day(now)
    out = []
    for d in sorted(os.listdir(src)):
        p = os.path.join(src, d)
        if not DAY_RE.match(d) or not os.path.isdir(p) or d >= today:
            continue
        files = day_files(p)
        if files and max(m for _, m in files.values()) / 1e9 <= now - quiet_s:
            out.append(d)
    return out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_ok(state_dir, day):
    try:
        with open(os.path.join(state_dir, day + ".ok")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def is_done(state_dir, day, day_dir):
    """Сутки сверены и с тех пор не менялись (размер и mtime каждого файла)."""
    ok = read_ok(state_dir, day)
    if ok is None:
        return False
    cur = {n: list(v) for n, v in day_files(day_dir).items()}
    return ok.get("files") == cur


class SshTransport:
    """Реальный бокс: rsync + ssh по алиасу из ~/.ssh/config (порт 23, ключ). Команды бокса — урезанный набор."""

    def __init__(self, host, bwlimit=None):
        self.host, self.bwlimit = host, bwlimit

    def _ssh(self, cmd):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, cmd], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise UploadError(f"ssh {cmd[:60]}: rc={r.returncode} {r.stderr.strip()[:200]}")
        return r.stdout

    def mkdir(self, rel):
        self._ssh(f"mkdir -p {rel}")

    def push(self, local_dir, rel, names):
        cmd = ["rsync", "-a", "--partial", "-e", "ssh -o BatchMode=yes"]
        if self.bwlimit:
            cmd.append(f"--bwlimit={self.bwlimit}")
        cmd += [os.path.join(local_dir, n) for n in names] + [f"{self.host}:{rel}/"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise UploadError(f"rsync rc={r.returncode} {r.stderr.strip()[:200]}")

    def sha(self, rel, names):
        out = {}
        for i in range(0, len(names), SHA_CHUNK):
            part = names[i:i + SHA_CHUNK]
            txt = self._ssh("sha256sum " + " ".join(f"{rel}/{n}" for n in part))
            for line in txt.splitlines():
                h, _, p = line.partition("  ")
                out[os.path.basename(p.strip())] = h
        return out


class LocalTransport:
    """«Бокс» в локальной папке — для тестов."""

    def __init__(self, root):
        self.root = root

    def mkdir(self, rel):
        os.makedirs(os.path.join(self.root, rel), exist_ok=True)

    def push(self, local_dir, rel, names):
        for n in names:
            shutil.copy2(os.path.join(local_dir, n), os.path.join(self.root, rel, n))

    def sha(self, rel, names):
        out = {}
        for n in names:
            p = os.path.join(self.root, rel, n)
            if os.path.isfile(p):
                out[n] = sha256_file(p)
        return out


def upload_day(tr, src, state_dir, exchange, day, box_dir):
    """Выгрузить и сверить одни сутки. Возвращает {'bytes', 'files', 'seconds'}; при расхождении — UploadError."""
    t0 = time.time()
    day_dir = os.path.join(src, day)
    before = day_files(day_dir)
    rel = f"{box_dir}/{exchange}/{day}"
    local_sha = {n: sha256_file(os.path.join(day_dir, n)) for n in before}
    sums_dir = os.path.join(state_dir, "sums", day)
    os.makedirs(sums_dir, exist_ok=True)
    with open(os.path.join(sums_dir, SUMS), "w") as f:
        for n in sorted(local_sha):
            f.write(f"{local_sha[n]}  {n}\n")
    tr.mkdir(rel)
    tr.push(day_dir, rel, sorted(before))
    tr.push(sums_dir, rel, [SUMS])
    remote = tr.sha(rel, sorted(before) + [SUMS])
    bad = [n for n in before if remote.get(n) != local_sha[n]]
    if bad or remote.get(SUMS) != sha256_file(os.path.join(sums_dir, SUMS)):
        raise UploadError(f"{day}: sha на боксе != локальный: {(bad or [SUMS])[:5]}")
    after = day_files(day_dir)
    if after != before:
        raise UploadError(f"{day}: файлы суток изменились во время выгрузки")
    rec = {"exchange": exchange, "day": day, "files": {n: list(v) for n, v in before.items()},
           "bytes": sum(v[0] for v in before.values()), "verified_ms": int(time.time() * 1000),
           "seconds": round(time.time() - t0, 1), "sums_sha256": remote[SUMS]}
    mark = os.path.join(sums_dir, MARK)
    with open(mark, "w") as f:
        json.dump(rec, f)
    tr.push(sums_dir, rel, [MARK])
    tmp = os.path.join(state_dir, day + ".ok.tmp")
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(state_dir, day + ".ok"))
    return {"bytes": rec["bytes"], "files": len(before), "seconds": rec["seconds"]}


def prune(src, state_dir, free_gb, disk_path=None):
    """Удалить локальные сверенные неизменные сутки (старые первыми), пока свободно < free_gb. Возвращает удалённые дни."""
    gone = []
    for fn in sorted(os.listdir(state_dir)):
        if not fn.endswith(".ok"):
            continue
        if shutil.disk_usage(disk_path or src).free / 1e9 >= free_gb:
            break
        day = fn[:-3]
        day_dir = os.path.join(src, day)
        if os.path.isdir(day_dir) and is_done(state_dir, day, day_dir):
            shutil.rmtree(day_dir)
            gone.append(day)
    return gone


def write_health(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, path)


def run(args, tr, now=None):
    now = time.time() if now is None else now
    state = os.path.join(args.state, args.exchange)
    os.makedirs(state, exist_ok=True)
    lock = open(os.path.join(state, "lock"), "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("уже идёт другой запуск", file=sys.stderr)
        return 0
    t0, total, err, done = time.time(), 0, None, []
    pending = [d for d in closed_days(args.src, now, args.quiet_s) if not is_done(state, d, os.path.join(args.src, d))]
    for d in pending:
        try:
            r = upload_day(tr, args.src, state, args.exchange, d, args.box_dir)
            total += r["bytes"]
            done.append(d)
            print(f"{d}: {r['files']} файлов, {r['bytes']} байт, {r['seconds']} с — сверено", flush=True)
        except (UploadError, OSError, subprocess.SubprocessError) as e:
            err = f"{d}: {e}"
            print("СБОЙ " + err, file=sys.stderr, flush=True)
            break  # следующая попытка таймера; дни по порядку, дыр не оставляем
    pruned = prune(args.src, state, args.prune_free_gb) if args.prune_free_gb else []
    oks = sorted(f[:-3] for f in os.listdir(state) if f.endswith(".ok"))
    write_health(os.path.join(args.health, f"upload-{args.exchange}.json"), {
        "schema": 1, "kind": "upload", "exchange": args.exchange, "ts_ms": int(time.time() * 1000),
        "last_ok_day": oks[-1] if oks else None, "pending_days": [d for d in pending if d not in done],
        "lag_days": len([d for d in pending if d not in done]), "last_error": err,
        "last_run_ms": int(t0 * 1000), "last_duration_s": round(time.time() - t0, 1), "last_bytes": total,
        "pruned": pruned})
    return 1 if err else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exchange", required=True)
    ap.add_argument("--src", required=True, help="корень коллектора: <src>/<ДЕНЬ UTC>/")
    ap.add_argument("--quiet-s", type=float, required=True, help="сутки закрыты, если файлы не менялись дольше (назначает Судья)")
    ap.add_argument("--box-dir", default="alpha/collect")
    ap.add_argument("--ssh-host", default="box")
    ap.add_argument("--bwlimit", type=int, default=None, help="КБ/с для rsync (по замеру)")
    ap.add_argument("--state", default="/data/upload-state")
    ap.add_argument("--health", default="/data/health")
    ap.add_argument("--prune-free-gb", type=float, default=None, help="удалять сверенное, пока свободно меньше (число Судьи); без флага не удаляет")
    a = ap.parse_args()
    sys.exit(run(a, SshTransport(a.ssh_host, a.bwlimit)))


if __name__ == "__main__":
    main()
