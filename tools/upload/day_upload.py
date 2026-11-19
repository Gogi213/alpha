#!/usr/bin/env python3
"""G-022: выгрузка закрытых суток коллекторов (Rust) с srv на Storage Box. НИЧЕГО НЕ УДАЛЯЕТ (предпочтение 3).

srv `<root>/<ДЕНЬ UTC>/*` (+ `<root>/pool-<ДЕНЬ>.json`) -> box `<box-dir>/<биржа>/<ДЕНЬ>/` + SHA256SUMS и UPLOADED.json.
Закрытые сутки = день < сегодня UTC и ни один файл дня не менялся дольше --quiet-s.
Сверка — sha256 каждого файла на боксе (`ssh box sha256sum`) против локального, не счёт rsync.
Локально — только чтение: удаления нет ни в коде, ни флагом; юнит монтирует /data только на чтение (кроме state и health).
Повтор безопасен: сверенные неизменные сутки пропускаются. Параллельный запуск — flock.
Состояние: <state>/<биржа>/<ДЕНЬ>.ok; здоровье: <health>/upload-<биржа>.json (schema 2).
Свободное место --disk-path < --free-alert-gb — только строка ALERT в журнал и поля в health, без действий.
Запуск: day_upload.py --source binance=/data/collect/binance/data --source okx=/data/collect/okx/data --quiet-s 300
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


def day_files(root, day):
    """Файлы суток: {имя: путь} — обычные файлы <root>/<день>/ (без служебных SUMS/MARK) и <root>/pool-<день>.json."""
    out = {}
    day_dir = os.path.join(root, day)
    for n in sorted(os.listdir(day_dir)):
        p = os.path.join(day_dir, n)
        if n not in (SUMS, MARK) and os.path.isfile(p):
            out[n] = p
    pool = os.path.join(root, f"pool-{day}.json")
    if os.path.isfile(pool):
        out[os.path.basename(pool)] = pool
    return out


def stat_files(files):
    """{имя: [размер, mtime_ns]} — отпечаток для «не менялось»."""
    out = {}
    for n, p in files.items():
        st = os.stat(p)
        out[n] = [st.st_size, st.st_mtime_ns]
    return out


def closed_days(root, now, quiet_s):
    """Сутки, которые можно выгружать: прошли, непусты, файлы не менялись дольше quiet_s."""
    today = utc_day(now)
    out = []
    for d in sorted(os.listdir(root)):
        if not DAY_RE.match(d) or not os.path.isdir(os.path.join(root, d)) or d >= today:
            continue
        st = stat_files(day_files(root, d))
        if st and max(v[1] for v in st.values()) / 1e9 <= now - quiet_s:
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


def is_done(state_dir, root, day):
    """Сутки сверены и с тех пор не менялись (размер и mtime каждого файла)."""
    ok = read_ok(state_dir, day)
    return ok is not None and ok.get("files") == stat_files(day_files(root, day))


class SshTransport:
    """Реальный бокс: rsync + ssh по алиасу из ~/.ssh/config (порт 23, ключ). Команды бокса — урезанный набор."""

    def __init__(self, host, bwlimit=None):
        self.host, self.bwlimit = host, bwlimit

    def _ssh(self, cmd):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, cmd], capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise UploadError(f"ssh {cmd[:60]}: rc={r.returncode} {r.stderr.strip()[:200]}")
        return r.stdout

    def mkdir(self, rel):
        self._ssh(f"mkdir -p {rel}")

    def push_cmd(self, paths, rel):
        cmd = ["rsync", "-a", "--partial", "-e", "ssh -o BatchMode=yes"]
        if self.bwlimit:
            cmd.append(f"--bwlimit={self.bwlimit}")
        return cmd + list(paths) + [f"{self.host}:{rel}/"]

    def push(self, paths, rel):
        r = subprocess.run(self.push_cmd(paths, rel), capture_output=True, text=True)
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

    def push(self, paths, rel):
        for p in paths:
            shutil.copy2(p, os.path.join(self.root, rel, os.path.basename(p)))

    def sha(self, rel, names):
        out = {}
        for n in names:
            p = os.path.join(self.root, rel, n)
            if os.path.isfile(p):
                out[n] = sha256_file(p)
        return out


def upload_day(tr, root, state_dir, exchange, day, box_dir):
    """Выгрузить и сверить одни сутки. Возвращает {'bytes', 'files', 'seconds'}; при расхождении — UploadError."""
    t0 = time.time()
    files = day_files(root, day)
    before = stat_files(files)
    rel = f"{box_dir}/{exchange}/{day}"
    local_sha = {n: sha256_file(p) for n, p in files.items()}
    sums_dir = os.path.join(state_dir, "sums", day)
    os.makedirs(sums_dir, exist_ok=True)
    sums_path = os.path.join(sums_dir, SUMS)
    with open(sums_path, "w") as f:
        for n in sorted(local_sha):
            f.write(f"{local_sha[n]}  {n}\n")
    tr.mkdir(rel)
    tr.push([files[n] for n in sorted(files)], rel)
    tr.push([sums_path], rel)
    remote = tr.sha(rel, sorted(files) + [SUMS])
    bad = [n for n in files if remote.get(n) != local_sha[n]]
    if bad or remote.get(SUMS) != sha256_file(sums_path):
        raise UploadError(f"{day}: sha на боксе != локальный: {(bad or [SUMS])[:5]}")
    if stat_files(day_files(root, day)) != before:
        raise UploadError(f"{day}: файлы суток изменились во время выгрузки")
    skipped = sorted(n for n in os.listdir(os.path.join(root, day)) if n not in files and n not in (SUMS, MARK))
    rec = {"exchange": exchange, "day": day, "files": before, "skipped_non_files": skipped,
           "bytes": sum(v[0] for v in before.values()), "verified_ms": int(time.time() * 1000),
           "seconds": round(time.time() - t0, 1), "sums_sha256": remote[SUMS]}
    mark = os.path.join(sums_dir, MARK)
    with open(mark, "w") as f:
        json.dump(rec, f)
    tr.push([mark], rel)
    write_json(os.path.join(state_dir, day + ".ok"), rec)
    return {"bytes": rec["bytes"], "files": len(before), "seconds": rec["seconds"]}


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, path)  # атомарная подмена своего служебного файла (state/health), не данных коллектора


def run_exchange(args, tr, exchange, root, now, disk):
    """Выгрузить закрытые сутки одной биржи. 0 — всё ок, 1 — сбой."""
    state = os.path.join(args.state, exchange)
    os.makedirs(state, exist_ok=True)
    t0, total, err, done = time.time(), 0, None, []
    pending = [d for d in closed_days(root, now, args.quiet_s) if not is_done(state, root, d)]
    for d in pending:
        try:
            r = upload_day(tr, root, state, exchange, d, args.box_dir)
            total += r["bytes"]
            done.append(d)
            print(f"{exchange} {d}: {r['files']} файлов, {r['bytes']} байт, {r['seconds']} с — сверено", flush=True)
        except (UploadError, OSError, subprocess.SubprocessError) as e:
            err = f"{d}: {e}"
            print(f"СБОЙ {exchange} {err}", file=sys.stderr, flush=True)
            break  # следующая попытка таймера; дни по порядку, дыр не оставляем
    oks = sorted(f[:-3] for f in os.listdir(state) if f.endswith(".ok"))
    left = [d for d in pending if d not in done]
    write_json(os.path.join(args.health, f"upload-{exchange}.json"), {
        "schema": 2, "kind": "upload", "exchange": exchange, "ts_ms": int(time.time() * 1000),
        "last_ok_day": oks[-1] if oks else None, "pending_days": left, "lag_days": len(left), "last_error": err,
        "last_run_ms": int(t0 * 1000), "last_duration_s": round(time.time() - t0, 1), "last_bytes": total, **disk})
    return 1 if err else 0


def disk_state(path, alert_gb):
    free_gb = round(shutil.disk_usage(path).free / 1e9, 1)
    alert = free_gb < alert_gb
    if alert:
        print(f"ALERT: свободно на {path} {free_gb} ГБ < {alert_gb} ГБ (удаления нет — решает владелец)", file=sys.stderr, flush=True)
    return {"disk_path": path, "disk_free_gb": free_gb, "disk_alert": alert}


def run(args, tr, now=None):
    now = time.time() if now is None else now
    os.makedirs(args.state, exist_ok=True)
    lock = open(os.path.join(args.state, "lock"), "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("уже идёт другой запуск", file=sys.stderr)
        return 0
    disk = disk_state(args.disk_path, args.free_alert_gb)
    rc = 0
    for src in args.source:
        exchange, _, root = src.partition("=")
        if not exchange or not os.path.isdir(root):
            print(f"СБОЙ источник {src!r}: нет каталога", file=sys.stderr, flush=True)
            rc = 1
            continue
        rc |= run_exchange(args, tr, exchange, root, now, disk)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", action="append", required=True, metavar="БИРЖА=КОРЕНЬ", help="корень коллектора: <КОРЕНЬ>/<ДЕНЬ UTC>/; можно несколько раз")
    ap.add_argument("--quiet-s", type=float, default=300.0, help="сутки закрыты, если файлы не менялись дольше")
    ap.add_argument("--box-dir", default="alpha/collect")
    ap.add_argument("--ssh-host", default="box")
    ap.add_argument("--bwlimit", type=int, default=None, help="КБ/с для rsync")
    ap.add_argument("--state", default="/data/upload-state")
    ap.add_argument("--health", default="/data/health")
    ap.add_argument("--disk-path", default="/data")
    ap.add_argument("--free-alert-gb", type=float, default=50.0, help="свободно меньше — строка ALERT и disk_alert в health")
    a = ap.parse_args()
    sys.exit(run(a, SshTransport(a.ssh_host, a.bwlimit)))


if __name__ == "__main__":
    main()
