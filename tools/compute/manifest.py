#!/usr/bin/env python3
"""КТ-1 (TK-145, план TK-123): манифест сборок и проверка заявки prod.

Манифест `MANIFEST.tsv` (колонки ниже) пишет только `tools/build-release.sh`; бинарник узнаётся по md5, не по пути.
`check_submit` зовётся из `alsched submit`: бинарник в команде вне манифеста или не `active`, команда не через
разрешённую обёртку — нарушение. Режим `SCHED_MANIFEST`: `0` выкл., `warn` (по умолчанию, один цикл) — нарушение
только в alerts.log, `1` — отказ. Обход — `--adhoc "причина"` (пишется в alerts.log).
"""
import hashlib, os, sys, time

COLS = ["md5", "commit", "profile", "features", "status", "built", "path", "used_by"]
MIN_BIN = 1 << 20      # как в guard._binaries: «бинарник» — исполняемый файл крупнее 1 МБ
WRAPPERS = ("warm-run", "cold", "benchrun", "calibrate")
STATUSES = ("active", "frozen", "retired")


def manifest_path():
    return os.environ.get("SCHED_MANIFEST_FILE", "/data/bin/MANIFEST.tsv")


def mode():
    return os.environ.get("SCHED_MANIFEST", "warn")


def load(path=None):
    """{md5: строка-словарь}; нет файла — пусто (нарушением станет сам бинарник)."""
    rows = {}
    try:
        with open(path or manifest_path(), encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("#") or not ln.strip():
                    continue
                v = ln.rstrip("\n").split("\t")
                r = dict(zip(COLS, v + [""] * (len(COLS) - len(v))))
                rows[r["md5"]] = r
    except FileNotFoundError:
        pass
    return rows


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def binaries(argv):
    """Исполняемые файлы крупнее MIN_BIN среди слов команды (абсолютные пути)."""
    return [a for a in argv if os.path.isabs(a) and os.path.isfile(a) and os.access(a, os.X_OK)
            and os.path.getsize(a) > MIN_BIN]


def wrapped(argv):
    """Команда идёт через разрешённую обёртку: имя одного из первых трёх слов (без пути и расширения)."""
    for a in argv[:3]:
        base = os.path.basename(a).rsplit(".", 1)[0]
        if base in WRAPPERS:
            return True
    return False


def violations(argv, rows=None, who=""):
    """Нарушения заявки; who — имя задания: сборка из манифеста получает отметку used_by."""
    rows = load() if rows is None else rows
    out = []
    for b in binaries(argv):
        md5 = md5_of(b)
        r = rows.get(md5)
        if r is not None and who:
            try:
                used_by(manifest_path(), md5, who)
            except OSError as e:        # отметка — не повод ронять заявку
                print(f"alsched: used_by не записан: {e}", file=sys.stderr)
        if r is None:
            out.append(f"бинарник {b} вне манифеста")
        elif r["status"] != "active":
            out.append(f"бинарник {b}: статус {r['status'] or '?'} (нужен active)")
    if not wrapped(argv):
        out.append(f"команда не через {'/'.join(WRAPPERS)}: {' '.join(argv[:3])}")
    return out


def check_submit(argv, adhoc, alert, who=""):
    """-> rc (0 — пускать). alert(text) пишет строку в alerts.log."""
    if mode() == "0":
        return 0
    if adhoc:
        alert(f"manifest --adhoc: {adhoc}: {' '.join(argv[:3])}")
        return 0
    v = violations(argv, who=who)
    if not v:
        return 0
    refuse = mode() == "1"
    for x in v:
        alert(f"manifest {'ОТКАЗ' if refuse else 'предупреждение'}: {x}")
        print(f"alsched: {x}", file=sys.stderr)
    if refuse:
        print('alsched: заявка отклонена; обход — --adhoc "причина"', file=sys.stderr)
        return 3
    return 0


FAST_MIN_S = 15 * 60       # В-229 п.8: prod дольше этого — только быстрым путём (v3+PGO, tools/compute/build-pgo.sh)


def fast_violations(argv, max_runtime_s, rows=None):
    """В-229 п.8 (TK-219): проход prod > 15 мин — бинарь из манифеста с профилем pgo (вкл. r2-pgo). -> список нарушений."""
    if max_runtime_s <= FAST_MIN_S:
        return []
    rows = load() if rows is None else rows
    out = []
    for b in binaries(argv):
        r = rows.get(md5_of(b))
        prof = r["profile"] if r else "вне манифеста"
        if prof != "pgo" and not prof.endswith("-pgo"):
            out.append(f"бинарник {b}: профиль {prof}, проход > 15 мин только на pgo/r2-pgo (build-pgo.sh); иначе --reason")
    return out


def check_fast(argv, max_runtime_s, reason, alert):
    """-> rc (0 — пускать). SCHED_FASTPATH: 1 (по умолчанию, отказ) | warn | 0."""
    mode = os.environ.get("SCHED_FASTPATH", "1")
    if mode == "0":
        return 0
    if reason:
        alert(f"fastpath --reason: {reason}: {' '.join(argv[:3])}")
        return 0
    v = fast_violations(argv, max_runtime_s)
    for x in v:
        alert(f"fastpath {'ОТКАЗ' if mode == '1' else 'предупреждение'}: {x}")
        print(f"alsched: {x}", file=sys.stderr)
    if v and mode == "1":
        print('alsched: заявка отклонена; обход — --reason "причина"', file=sys.stderr)
        return 3
    return 0


EST_K = 1.5                # В-229 п.9: max-runtime >= 1,5 x оценки по пробе «1 сутки x 3 месяца»


def check_estimate(max_runtime_s, est_s, alert):
    """-> rc. est_s — оценка прохода по пробе (--est), None — не указана (prod > 15 мин: предупреждение)."""
    if max_runtime_s <= FAST_MIN_S:
        return 0
    if est_s is None:
        alert("оценка: проход > 15 мин без --est (проба 1 сутки x 3 месяца, docs/COMMANDS.md)")
        print("alsched: проход > 15 мин без --est <оценка по пробе 1 сутки x 3 месяца>", file=sys.stderr)
        return 0
    if max_runtime_s < EST_K * est_s:
        alert(f"оценка ОТКАЗ: max-runtime {max_runtime_s:.0f}s < {EST_K} x оценки {est_s:.0f}s")
        print(f"alsched: max-runtime {max_runtime_s:.0f}s < {EST_K} x --est {est_s:.0f}s — заявка отклонена", file=sys.stderr)
        return 3
    return 0


def used_by(path, md5, who):
    """Отметить в манифесте, кто взял сборку (колонка used_by, без дублей); манифест пишет только это и build-release."""
    rows = load(path)
    r = rows.get(md5)
    if r is None or who in r["used_by"].split(","):
        return
    r["used_by"] = ",".join(x for x in (r["used_by"], who) if x)
    write(path, rows)


def write(path, rows):
    tmp = (path or manifest_path()) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("#" + "\t".join(COLS) + "\n")
        for r in rows.values():
            f.write("\t".join(r[c] for c in COLS) + "\n")
    os.replace(tmp, path or manifest_path())


def bootstrap(path, dirs):
    """Первичная опись уже лежащих сборок (исполняемые > MIN_BIN, как guard._binaries, любое имя): строка на md5, commit «?», profile «legacy», status active
    (статусы frozen/retired назначает решение, не опись); существующие строки не трогает, файлы не удаляет и не двигает."""
    rows = load(path)
    n0 = len(rows)
    for d in dirs:
        for name in sorted(os.listdir(d)):
            f = os.path.join(d, name)
            if os.path.isfile(f) and os.access(f, os.X_OK) and os.path.getsize(f) > MIN_BIN:
                md5 = md5_of(f)
                if md5 not in rows:
                    rows[md5] = dict(md5=md5, commit="?", profile="legacy", features="", status="active",
                                     built=time.strftime("%FT%T", time.localtime(os.path.getmtime(f))), path=f, used_by="")
    write(path, rows)
    return len(rows) - n0


if __name__ == "__main__":       # manifest.py show | check <бинарник…> | bootstrap <каталог…>
    if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
        print("добавлено строк:", bootstrap(manifest_path(), sys.argv[2:]))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        v = violations(sys.argv[2:])
        print("\n".join(v) or "ок")
        sys.exit(1 if v else 0)
    for r in load().values():
        print("\t".join(r[c] for c in COLS))
