#!/usr/bin/env python3
"""TK-493, П-38 r2 §12.2: защита торгового окна (сутки >= 2479), проверка params, фиксатор K0, журнал чтений, чтение панелей по смещению.

Правила (все обязательные; код отказывает ДО открытия любого файла данных):
  (1) любое чтение данных суток >= 2479 (панели, боковые панели оборота, фандинг, сырьё) - только при флаге --go-window И файле-токене
      $C2_HOME/go_window.token ($C2_HOME по умолчанию ~/xdl9/c2); без них - SystemExit(3);
  (2) токен - три строки, записывает ТОЛЬКО CEO: 1) `<sha256 params>,<sha256 addendum>` (P-38-params-2026-11-07.csv и P-38-params-calib-*.csv),
      2) sha256 скрипта conv_run.py, 3) метка `GO <дата>, CEO`; первые две строки сверяются с фактическими, несовпадение - отказ (код 3);
  (3) калибровочные проходы: last_day >= 2479 не принимаются вообще (даже с токеном), Pi_train - last_day > 2471 (G-C2-9);
  (4) params закоммичены и не изменены: в git-дереве - `git log` по файлу непуст и `git status --porcelain` чист; на Deck (копия без git) - токен
      `c2_git_ok`, записанный `conv_run.py --git-token` на ПК по образцу f8_run.sh (sha256 закоммиченного содержимого params и протокола);
  (5) каждое одобренное чтение суток >= 2479 пишется в журнал $C2_HOME/window_reads.log (время UTC, скрипт, sha256, last_day);
  (6) шаг K0 (calib_start.json) записан до первого чтения панелей формирования: `require_k0()`.
Чтение панелей - только `load_panel_range`: по смещению, с assert на число минут, никогда не за пределами `last_day`.
Не читает: ничего вне формирования, пока не пройдены (1)-(5).
"""
import datetime
import hashlib
import json
import os
import struct
import subprocess
import sys

import numpy as np

GRID0_MS = 1_577_836_800_000
MIN_DAY = 1440
D_FORM0 = 2465            # 01.10.2026 UTC, разогрев
D_SIG0 = 2466             # первые сигнальные сутки
D_TRAIN_LAST = 2471       # Pi_train читает сутки до 2471 включительно
D_FORM_LAST = 2478
D_WIN0 = 2479             # торговое окно, НЕ читается без токена
D_WIN_LAST = 2499

HOME = os.path.expanduser("~/xdl9")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_HINT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))

PARAMS_REL = "docs/research/P-38-params-2026-11-07.csv"
PROTO_REL = "docs/research/P-38-convergence-xdl9.md"


def c2_home():
    return os.environ.get("C2_HOME") or os.path.join(HOME, "c2")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def sha256_text_bytes(b):
    return hashlib.sha256(b).hexdigest()


class Refused(SystemExit):
    """Отказ защиты: код выхода 3."""

    def __init__(self, msg):
        sys.stderr.write("ОТКАЗ (защита окна П-38 §12.2): %s\n" % msg)
        super().__init__(3)
        self.msg = msg


# ------------------------------------------------------------------ состояние защиты (устанавливает точка входа)
class _State:
    go_window = False
    script_path = None            # путь conv_run.py (для sha256 токена и журнала)
    params_paths = ()             # (params, addendum или None)
    mode = "calib"                # 'calib' | 'train' | 'window' | 'test'
    require_k0_flag = True        # K0 обязателен для настоящих чтений формирования
    git_checked = False
    test_root = None              # режим test: читать можно только файлы под этим каталогом (не под ~/xdl9)


STATE = _State()


def configure(*, go_window=False, script_path=None, params_paths=(), mode="calib", require_k0=True, test_root=None):
    STATE.go_window = bool(go_window)
    STATE.script_path = script_path
    STATE.params_paths = tuple(params_paths)
    STATE.mode = mode
    STATE.require_k0_flag = bool(require_k0)
    STATE.git_checked = False
    STATE.test_root = os.path.realpath(test_root) if test_root else None
    if mode == "test" and test_root:
        if os.path.realpath(test_root).startswith(os.path.realpath(HOME)):
            raise Refused("тестовый мир не может лежать внутри %s" % HOME)


def check_path(path):
    """В режиме test читать можно только под test_root; боевые пути в test недоступны."""
    if STATE.mode == "test" and STATE.test_root:
        if not os.path.realpath(path).startswith(STATE.test_root):
            raise Refused("режим test: путь вне тестового мира: %s" % path)


def _token_lines():
    p = os.path.join(c2_home(), "go_window.token")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return [ln.rstrip("\r\n") for ln in f.read().split("\n") if ln.strip() != ""]


def params_sha_line(params_paths=None):
    """Строка 1 токена: `<sha256 params>,<sha256 addendum>` (addendum может отсутствовать: тогда пусто после запятой)."""
    pp = list(params_paths if params_paths is not None else STATE.params_paths)
    a = sha256_file(pp[0]) if pp and pp[0] and os.path.isfile(pp[0]) else ""
    b = sha256_file(pp[1]) if len(pp) > 1 and pp[1] and os.path.isfile(pp[1]) else ""
    return "%s,%s" % (a, b)


def check_window_token(script_path=None, params_paths=None):
    """Возвращает None при успехе, иначе строку-причину отказа."""
    lines = _token_lines()
    if lines is None:
        return "нет файла-токена %s/go_window.token (его выдаёт только CEO)" % c2_home()
    if len(lines) < 3:
        return "токен содержит меньше трёх строк"
    sp = script_path or STATE.script_path
    if not sp or not os.path.isfile(sp):
        return "не задан скрипт c2_run для сверки sha256 токена"
    if lines[0] != params_sha_line(params_paths):
        return "строка 1 токена (sha256 params и addendum) не равна фактической"
    if lines[1] != sha256_file(sp):
        return "строка 2 токена (sha256 скрипта c2_run) не равна фактической"
    if not lines[2].startswith("GO ") or "CEO" not in lines[2]:
        return "строка 3 токена не имеет вида «GO <дата>, CEO»"
    return None


def check_days(last_day, *, what="данные"):
    """Главный вход защиты: вызывается перед ЛЮБЫМ чтением данных. last_day - последние сутки, которые будут прочитаны (включительно)."""
    if STATE.mode == "test":
        return
    if last_day is None:
        raise Refused("не задан last_day для чтения: %s" % what)
    if STATE.mode == "train" and last_day > D_TRAIN_LAST:
        raise Refused("Pi_train не читает сутки > %d (запрошено %d): G-C2-9" % (D_TRAIN_LAST, last_day))
    if STATE.mode in ("calib", "train") and last_day >= D_WIN0:
        raise Refused("калибровочные проходы не принимают last_day >= %d вообще (запрошено %d)" % (D_WIN0, last_day))
    if last_day >= D_WIN0:
        if STATE.mode != "window" or not STATE.go_window:
            raise Refused("сутки >= %d (запрошено %d) без флага --go-window" % (D_WIN0, last_day))
        why = check_window_token()
        if why:
            raise Refused(why)
        ensure_params_committed()
        _log_window_read(last_day, what)


def _log_window_read(last_day, what):
    os.makedirs(c2_home(), exist_ok=True)
    sp = STATE.script_path
    with open(os.path.join(c2_home(), "window_reads.log"), "a", encoding="utf-8") as f:
        f.write("%s\t%s\t%s\tlast_day=%d\t%s\n" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                   os.path.basename(sp or "?"), sha256_file(sp) if sp and os.path.isfile(sp) else "?", last_day, what))


# ------------------------------------------------------------------ params закоммичены
def _git(args, cwd):
    return subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True)


def git_root(start=HERE):
    r = _git(["rev-parse", "--show-toplevel"], start)
    return r.stdout.strip() if r.returncode == 0 else None


def blob_sha(root, rel):
    """sha256 закоммиченного содержимого (не рабочей копии: autocrlf), как blob_sha в f8_run.sh."""
    r = subprocess.run(["git", "-C", root, "show", "HEAD:" + rel], capture_output=True)
    return sha256_text_bytes(r.stdout) if r.returncode == 0 else None


def git_token_path():
    return os.path.join(HERE, "c2_git_ok")


def write_git_token(addendum_rel=None):
    """На ПК (рабочее дерево git): проверить, что params, протокол (и addendum) закоммичены и чисты, записать c2_git_ok для копии на Deck."""
    root = git_root()
    if not root:
        raise Refused("--git-token нужно рабочее дерево git")
    rels = [PARAMS_REL, PROTO_REL] + ([addendum_rel] if addendum_rel else [])
    out = {}
    for rel in rels:
        c = _git(["log", "-1", "--format=%H", "--", rel], root).stdout.strip()
        if not c:
            raise Refused("%s не закоммичен (git log по файлу пуст)" % rel)
        if _git(["status", "--porcelain", "--", rel], root).stdout.strip():
            raise Refused("%s изменён относительно HEAD" % rel)
        if _git(["merge-base", "--is-ancestor", c, "HEAD"], root).returncode != 0:
            raise Refused("коммит %s файла %s не предок HEAD" % (c, rel))
        out[rel] = (c, blob_sha(root, rel))
    with open(git_token_path(), "w", encoding="utf-8", newline="\n") as f:
        f.write("params_commit=%s\nparams_sha256=%s\nproto_sha256=%s\n" % (out[PARAMS_REL][0], out[PARAMS_REL][1], out[PROTO_REL][1]))
        if addendum_rel:
            f.write("addendum_commit=%s\naddendum_sha256=%s\n" % out[addendum_rel])
        f.write("head=%s\n" % _git(["rev-parse", "HEAD"], root).stdout.strip())
    return out


def ensure_params_committed():
    """Отказ, если params не закоммичен. В git-дереве - git; на Deck - токен c2_git_ok с теми же sha256, что у копии файлов params."""
    if STATE.mode == "test" or STATE.git_checked:
        return
    root = git_root()
    pp = [p for p in STATE.params_paths if p]
    if root:
        for rel in (PARAMS_REL,):
            if not _git(["log", "-1", "--format=%H", "--", rel], root).stdout.strip():
                raise Refused("params %s не закоммичен" % rel)
            if _git(["status", "--porcelain", "--", rel], root).stdout.strip():
                raise Refused("params %s изменён относительно HEAD" % rel)
    else:
        tp = git_token_path()
        if not os.path.isfile(tp):
            raise Refused("нет git и нет токена %s: сначала `conv_run.py --git-token` на ПК" % tp)
        kv = dict(ln.split("=", 1) for ln in open(tp, encoding="utf-8").read().split("\n") if "=" in ln)
        if not pp or not os.path.isfile(pp[0]):
            raise Refused("нет файла params для сверки с токеном c2_git_ok")
        # sha256 закоммиченного содержимого считался от LF-текста; копия на Deck пришла как есть - сравниваем нормализованный CR-LF
        with open(pp[0], "rb") as f:
            raw = f.read().replace(b"\r\n", b"\n")
        if sha256_text_bytes(raw) != kv.get("params_sha256"):
            raise Refused("sha256 копии params не равен записанному в c2_git_ok")
    STATE.git_checked = True


# ------------------------------------------------------------------ K0
def calib_start_path():
    return os.path.join(c2_home(), "calib_start.json")


def write_k0(gate_ready_path=None, params_paths=None):
    """K0: фиксатор состава универсума (правило Gate §3.4) ДО чтения панелей. Возвращает записанный словарь."""
    gp = gate_ready_path or os.path.join(HOME, "gate_ready")
    checks_needed = ("rows", "gaps", "units", "open_semantics", "funding", "classes")
    rec = {"time_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "gate_ready_exists": False, "gate_ready_valid": False, "gate_mtime": None, "gate_sha256": None, "gate_checks": [], "gate_nsym": None}
    if os.path.isfile(gp):
        txt = open(gp, encoding="utf-8", errors="replace").read()
        kv = {}
        for ln in txt.split("\n"):
            if "=" in ln:
                k, v = ln.split("=", 1)
                kv[k.strip()] = v.strip()
        ok = [k for k in checks_needed if kv.get(k) == "ok"]
        rec.update(gate_ready_exists=True, gate_mtime=os.path.getmtime(gp), gate_sha256=sha256_file(gp), gate_checks=ok,
                   gate_nsym=kv.get("symbols") or kv.get("nsym"))
        rec["gate_ready_valid"] = len(ok) == len(checks_needed) and rec["gate_nsym"] is not None
    pp = list(params_paths if params_paths is not None else STATE.params_paths)
    rec["params_sha256"] = [sha256_file(p) if p and os.path.isfile(p) else None for p in pp]
    os.makedirs(c2_home(), exist_ok=True)
    p = calib_start_path()
    if os.path.exists(p):
        raise Refused("calib_start.json уже записан (%s): K0 выполняется один раз" % p)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1, sort_keys=True)
    return rec


def require_k0():
    if STATE.mode == "test" or not STATE.require_k0_flag:
        return None
    p = calib_start_path()
    if not os.path.isfile(p):
        raise Refused("нет %s: сначала K0 (запись фиксатора Gate до чтения панелей формирования)" % p)
    return json.load(open(p, encoding="utf-8"))


# ------------------------------------------------------------------ чтение панелей по смещению
def read_sap_header(path):
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first = struct.unpack("<q", h[8:16])[0]
    n = struct.unpack("<Q", h[16:24])[0]
    return first, n


def load_panel_range(path, d0, d1, *, what="panel"):
    """open/close на минутах [d0*1440, (d1+1)*1440), float64, NaN вне спана и в дырах. Читает только нужные байты (np.fromfile со смещением),
    assert на число минут; защита `check_days(d1)` вызывается ДО открытия файла."""
    check_path(path)
    check_days(d1, what=what + " " + os.path.basename(path))
    if STATE.mode != "test":
        require_k0()
        ensure_params_committed()
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
    """Оборот по минутам [d0*1440, (d1+1)*1440) из сырых свечей csv.gz (`t_open_ms,o,h,l,c,v,quote_v`); чтение прекращается на первой строке
    позже last_day (файл идёт по возрастанию времени): суток > last_day не разбирается. Оборот: quote_v, где пуст - c*v; Gate: c*v*mult."""
    import gzip
    check_path(path)
    check_days(d1, what="turnover " + os.path.basename(path))
    if STATE.mode != "test":
        require_k0()
        ensure_params_committed()
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
    """Ставки фандинга -> (минуты от начала сетки int64, ставки float64) за [d0*1440, (d1+1)*1440); чтение прекращается на первой строке позже last_day."""
    import gzip
    check_path(path)
    check_days(d1, what="funding " + os.path.basename(path))
    if STATE.mode != "test":
        require_k0()
        ensure_params_committed()
    if not os.path.isfile(path):
        return np.zeros(0, np.int64), np.zeros(0)
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    ts, rs = [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            p = line.split(",")
            if len(p) < 2:
                continue
            m = (int(p[0]) - GRID0_MS) // 60_000
            if m >= m1:
                break
            if m >= m0:
                ts.append(m)
                rs.append(float(p[1]))
    return np.asarray(ts, np.int64), np.asarray(rs, np.float64)
