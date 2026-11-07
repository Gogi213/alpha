#!/usr/bin/env python3
"""TK-493, П-39 (режим ПОИСКА схождений C2): защита поиска. ОТДЕЛЬНЫЙ модуль: conv_guard.py и conv_window.py (защита подтверждающего окна П-38) не меняются и не вызываются.

Правила (G-X-5):
  (1) поиск читает сутки 2465..2499 (01.10–04.11.2026) и НИКОГДА сутки >= 2500: любое чтение с last_day >= 2500 - Refused, код 3, до открытия файла;
  (2) токен окна (go_window.token), его журнал (window_reads.log), выходы окна и калибровки (c2/window*, c2/out*, c2g/out*) не читаются и не пишутся; токены модуль не читает;
  (3) запись - только под корнем поиска (по умолчанию ~/xdl9/c2x): check_write; каждый выходной файл начинается строкой-баннером «ПОИСК П-39 — НЕ ПОДТВЕРЖДЕНИЕ…»
      (CSV - «# …», JSON - ключ _banner, txt/md - первая строка; gz - первая строка CSV); verify_banners проверяет каталог;
  (4) журнал чтений ~/xdl9/c2x/explore_reads.log: время, скрипт, sha256, pid, событие, диапазон суток (JSON по строке, добавление);
  (5) протокол П-39, params и addendum, заморожены коммитом: файл x_git_ok каталога кода (sha256 по LF blob'ов коммита) сверяется с развёрнутыми файлами.
Чтение панелей, оборота и фандинга - те же форматы, что у conv_guard (по смещению, с assert на число минут), но с проверкой check_days этого модуля.
"""
import datetime
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import struct
import sys
import time

import numpy as np

GRID0_MS = 1_577_836_800_000
MIN_DAY = 1440
D_FORM0 = 2465            # 01.10.2026 UTC, разогрев
D_SIG0 = 2466
D_SEARCH_LAST = 2499      # 04.11.2026: последние сутки, которые поиск вправе читать
D_CONF0 = 2500            # 05.11.2026: сутки подтверждения - поиск не читает никогда
BANNER = "ПОИСК П-39 — НЕ ПОДТВЕРЖДЕНИЕ: сутки 2465…2499 (01.10–04.11), выбрано по 35 суткам, пост hoc; слов «подтверждено» нет"
RUNNER = "conv_explore.py"
HOME = os.path.expanduser("~/xdl9")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_NAME = "explore_reads.log"
FORBIDDEN_NAMES = ("go_window.token", "window_reads.log", "confirm_reads.log", "go_confirm.token")
FORBIDDEN_DIRS = ("c2/window", "c2/out", "c2/out_noq5", "c2g/out", "c2g/out_noq5", "c2g/ng")     # выходы окна и калибровки (читать их поиск не должен; конфиги берутся из кода)
NO_BANNER_DIRS = ("cache", "work", "turn")       # промежуточные массивы (бинарные), не выходные файлы


def c2x_home():
    return os.environ.get("C2X_HOME") or os.path.join(HOME, "c2x")


class Refused(SystemExit):
    """Отказ защиты поиска: код выхода 3."""

    def __init__(self, msg):
        sys.stderr.write("ОТКАЗ (защита поиска П-39): %s\n" % msg)
        super().__init__(3)
        self.msg = msg


def _under(path, root):
    try:
        r = os.path.realpath(root)
        return os.path.commonpath([os.path.realpath(path), r]) == r
    except ValueError:
        return False


class _State:
    mode = None                 # 'search' | 'test'
    out_root = None
    test_root = None
    script_path = None
    log_path = None
    max_read = None             # разделяемый (fork) максимум суток, прочитанных процессом и потомками
    configured = False


STATE = _State()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def sha256_lf(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def configure(*, out_root=None, script_path=None, test_root=None, mode="search"):
    """Точка входа вызывает один раз. mode 'search': читать можно ~/xdl9 (кроме выходов окна), писать - под out_root (по умолчанию ~/xdl9/c2x). mode 'test': всё под test_root."""
    if mode not in ("search", "test"):
        raise Refused("режим %r" % mode)
    if mode == "test":
        if not test_root:
            raise Refused("режим test требует test_root")
        if _under(test_root, HOME) or _under(HOME, test_root):
            raise Refused("тестовый мир не может лежать внутри и быть предком %s" % HOME)
        out_root = out_root or test_root
        if not _under(out_root, test_root):
            raise Refused("режим test: out_root вне test_root")
    else:
        out_root = out_root or c2x_home()
    STATE.mode = mode
    STATE.out_root = os.path.abspath(out_root)
    STATE.test_root = os.path.realpath(test_root) if test_root else None
    STATE.script_path = script_path
    STATE.log_path = os.path.join(STATE.out_root, LOG_NAME) if mode == "search" else os.path.join(STATE.out_root, "explore_reads_test.log")
    STATE.max_read = mp.RawValue("i", 0)
    STATE.configured = True
    os.makedirs(STATE.out_root, exist_ok=True)


def _need():
    if not STATE.configured:
        raise Refused("защита поиска не настроена (configure)")


def check_days(last_day, *, what="данные"):
    """Главный вход: вызывается перед ЛЮБЫМ чтением данных. last_day - последние сутки чтения (включительно). Сутки >= 2500 - отказ в любом режиме."""
    _need()
    if last_day is None:
        raise Refused("не задан last_day для чтения: %s" % what)
    if int(last_day) >= D_CONF0:
        raise Refused("поиск не читает сутки >= %d (запрошено %d, %s): они принадлежат подтверждению" % (D_CONF0, int(last_day), what))
    if int(last_day) > STATE.max_read.value:
        STATE.max_read.value = int(last_day)


def check_path(path):
    """Читать нельзя токены и журналы окна, выходы окна и калибровки; в режиме test - только под test_root."""
    _need()
    b = os.path.basename(path)
    if b in FORBIDDEN_NAMES or b.startswith("go_window.token"):
        raise Refused("поиск не читает токены и журналы окна: %s" % path)
    if STATE.mode == "test":
        if not _under(path, STATE.test_root):
            raise Refused("режим test: путь вне тестового мира: %s" % path)
        return
    for d in FORBIDDEN_DIRS:
        if _under(path, os.path.join(HOME, d)):
            raise Refused("поиск не читает выходы окна/калибровки (%s): %s" % (d, path))


def check_write(path):
    _need()
    if not _under(path, STATE.out_root):
        raise Refused("запись вне корня поиска %s: %s" % (STATE.out_root, path))
    b = os.path.basename(path)
    if b in FORBIDDEN_NAMES or b.startswith("go_window.token"):
        raise Refused("поиск не пишет токены и журналы окна: %s" % path)


# ------------------------------------------------------------------ журнал чтений
def log_event(event, **kw):
    _need()
    rec = {"t": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "script": os.path.basename(STATE.script_path or sys.argv[0]), "pid": os.getpid(),
           "event": event}
    try:
        if STATE.script_path and os.path.isfile(STATE.script_path):
            rec["sha256"] = sha256_file(STATE.script_path)
    except OSError:
        pass
    rec.update(kw)
    with open(STATE.log_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def log_max_read(event="max_read"):
    log_event(event, max_day_read=int(STATE.max_read.value))


# ------------------------------------------------------------------ баннер и запись
def banner_csv():
    return "# " + BANNER + "\n"


def write_text(path, text, banner_first=True):
    check_write(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write((BANNER + "\n") if banner_first else "")
        f.write(text)


def write_json(path, obj):
    check_write(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    d = dict(obj) if isinstance(obj, dict) else {"data": obj}
    d = {"_banner": BANNER, **d}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(_jsonable(d), f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_csv(path, header, rows, gz=False):
    """CSV с первой строкой «# баннер»; rows - итерируемое списков; gz - gzip (mtime=0: байты детерминированы)."""
    check_write(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if gz:
        import io
        raw = open(path, "wb")
        f = io.TextIOWrapper(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6), encoding="utf-8", newline="\n")
    else:
        f = open(path, "w", encoding="utf-8", newline="\n")
    try:
        f.write(banner_csv())
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(_cell(x) for x in r) + "\n")
    finally:
        f.close()
        if gz:
            raw.close()


def _cell(x):
    if x is None:
        return ""
    if isinstance(x, (float, np.floating)):
        return "" if not np.isfinite(x) else "%.12g" % x
    s = str(x)
    if "," in s or '"' in s or "\n" in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def first_line(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.readline().rstrip("\n")
    with open(path, "rb") as f:
        head = f.read(4096)
    try:
        return head.decode("utf-8").split("\n", 1)[0].rstrip("\r")
    except UnicodeDecodeError:
        return "<binary>"


def banner_ok(path):
    """Первая строка файла - баннер (CSV/txt/md: «# баннер» либо баннер; JSON: ключ _banner в начале)."""
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f).get("_banner") == BANNER
            except (ValueError, AttributeError):
                return False
    fl = first_line(path)
    return fl == BANNER or fl == "# " + BANNER


def verify_banners(root, skip=NO_BANNER_DIRS):
    """-> список файлов под root (кроме промежуточных каталогов skip и журнала), у которых первая строка - не баннер. Пустой список = G-X-5 (д) пройдено."""
    bad = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        top = rel.split(os.sep)[0] if rel != "." else ""
        if top in skip:
            dn[:] = []
            continue
        if top == "code":
            dn[:] = []
            continue
        for f in fn:
            if f in (LOG_NAME, "explore_reads_test.log") or f.endswith((".npy", ".npz", ".pyc")):
                continue
            p = os.path.join(dp, f)
            if not banner_ok(p):
                bad.append(os.path.relpath(p, root))
    return bad


# ------------------------------------------------------------------ протокол заморожен коммитом
def ensure_protocol_frozen(code_root, rel_files):
    """x_git_ok в code_root: {"commit": ..., "files": {rel: sha256_lf blob'а коммита}}; развёрнутые rel_files должны совпасть. Нет файла/расхождение - Refused."""
    p = os.path.join(code_root, "x_git_ok")
    if not os.path.isfile(p):
        raise Refused("нет %s (sha256 закоммиченных протокола/params/addendum П-39): развернуть код через deploy_explore.sh" % p)
    ok = json.load(open(p, encoding="utf-8"))
    out = {}
    for rel in rel_files:
        fp = os.path.join(code_root, rel)
        if not os.path.isfile(fp):
            raise Refused("нет файла %s" % rel)
        want = ok["files"].get(rel)
        have = sha256_lf(fp)
        if want != have:
            raise Refused("%s: sha256 %s не равен записанному в x_git_ok (%s): файл изменён после коммита" % (rel, have[:12], (want or "нет")[:12]))
        out[rel] = have
    return ok.get("commit"), out


# ------------------------------------------------------------------ чтение панелей, оборота, фандинга (форматы conv_guard)
def read_sap_header(path):
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first = struct.unpack("<q", h[8:16])[0]
    n = struct.unpack("<Q", h[16:24])[0]
    return first, n


def load_panel_range(path, d0, d1, *, what="panel"):
    check_path(path)
    check_days(d1, what=what + " " + os.path.basename(path))
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    nf = m1 - m0
    first, n = read_sap_header(path)
    lo, hi = max(m0, first), min(m1, first + n)
    o = np.full(nf, np.nan)
    c = np.full(nf, np.nan)
    if hi > lo:
        cnt = hi - lo
        assert hi <= m1 and cnt <= nf, "число минут вне ожидаемого"
        o[lo - m0:hi - m0] = np.fromfile(path, dtype="<f8", count=cnt, offset=24 + (lo - first) * 8)
        c[lo - m0:hi - m0] = np.fromfile(path, dtype="<f8", count=cnt, offset=24 + n * 8 + (lo - first) * 8)
    return o, c


def load_turnover_raw(path, d0, d1, *, exchange="", mult=1.0):
    check_path(path)
    check_days(d1, what="turnover " + os.path.basename(path))
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    out = np.zeros(m1 - m0, dtype=np.float64)
    t_end_ms = GRID0_MS + m1 * 60_000
    t_beg_ms = GRID0_MS + m0 * 60_000
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            p = line.strip().split(",")
            if len(p) < 6:
                continue
            t = int(p[0])
            if t >= t_end_ms:
                break
            if t < t_beg_ms:
                continue
            m = (t - GRID0_MS) // 60_000 - m0
            c, v = float(p[4]), float(p[5])
            q = p[6] if len(p) > 6 else ""
            if exchange == "gate":
                out[m] = c * v * mult
            else:
                out[m] = float(q) if q != "" else c * v
    return out


def load_funding_range(path, d0, d1):
    ts, rs, _ = load_funding_full(path, d0, d1)
    return ts, rs


def load_funding_full(path, d0, d1):
    """Выплаты фандинга ноги на минутах [d0*1440, (d1+1)*1440): (минуты от начала сетки int64, ставки float64, interval_h float64 (nan, если колонки нет))."""
    check_path(path)
    check_days(d1, what="funding " + os.path.basename(path))
    if not os.path.isfile(path):
        return np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    ts, rs, ih = [], [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            p = line.strip().split(",")
            if len(p) < 2:
                continue
            m = (int(p[0]) - GRID0_MS) // 60_000
            if m >= m1:
                break
            if m >= m0:
                ts.append(m)
                rs.append(float(p[1]))
                try:
                    ih.append(float(p[2]) if len(p) > 2 and p[2] != "" else float("nan"))
                except ValueError:
                    ih.append(float("nan"))
    return np.asarray(ts, np.int64), np.asarray(rs, np.float64), np.asarray(ih, np.float64)
