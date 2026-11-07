#!/usr/bin/env python3
"""TK-493, П-38 r2 §12.2 (+ дельта 07.11, закрытие B1/m1/m2 ревизии): защита торгового окна (сутки >= 2479), проверка params, фиксатор K0, журнал чтений, чтение панелей по смещению.

Правила (все обязательные; код отказывает ДО открытия любого файла данных):
  (1) любое чтение данных суток >= 2479 (панели, боковые панели оборота, фандинг, сырьё) - только при флаге --go-window, режиме window, ЕДИНСТВЕННОМ раннере
      conv_window.py (RUNNER) и файле-токене $C2_HOME/go_window.token ($C2_HOME по умолчанию ~/xdl9/c2); без них - Refused / SystemExit(3);
  (2) токен записывает ТОЛЬКО CEO (r3 R3.8): по строке `<sha256 по LF>  <путь>` на каждый файл перечня (по возрастанию пути) и последняя строка `GO <дата>, CEO`.
      Перечень = ВСЕ `conv/*.py` и `conv/*.sh` каталога кода (путь `tools/compute/statarb/xdl9/conv/<имя>`), `portfolio.py` и `cell_status.py` (`tools/compute/statarb/`),
      params G0.1, params r3 и addendum (`docs/research/<имя>`), все `P-38-params-calib-*.csv` рядом с addendum, spec окна и входные файлы раннера (classpar_*.json и q5_*.json
      рядом с указанными, jobs_w2.csv, эталон k; путь вида `~/...`). Любое расхождение (изменён файл, лишний или недостающий файл, лишняя или недостающая строка) - отказ (код 3).
      Хэши считаются по LF-содержимому (CRLF -> LF), чтобы копия на Deck равнялась коммиту;
  (3) токен ОДНОРАЗОВЫЙ: проверка перед первым чтением окна переименовывает его атомарно (os.replace) в `go_window.token.used.<pid>.<ns>`; раннер удаляет этот файл в конце
      (`finish_window`, в том числе при падении) и пишет записи в `window_reads.log`; однократность - по файлу (второй вызов не находит токена), а не по журналу.
      ИСКЛЮЧЕНИЕ (GW, B1 07.11): сбой раннера ДО ПЕРВОГО открытия файла данных окна (`mark_window_read`: ни одной панели, ни оборота, ни фандинга суток >= 2479 не открыто)
      не сжигает токен - `finish_window("failed")` переименовывает `.used` обратно и пишет `event=aborted_before_read` (журнал сбалансирован: незавершённого прогона нет);
      после первого открытия файла окна токен сгорает окончательно (`event=run_end`);
  (4) калибровочные проходы: last_day >= 2479 не принимаются вообще (даже с токеном), Pi_train - last_day > 2471 (G-C2-9); режим testb (проверка раннера на формировании с
      искусственной границей D <= 2478) не читает сутки >= 2479 никогда, токен и --go-window ему недоступны;
  (5) params, addendum и params r3 закоммичены и не изменены: в git-дереве - `git log` по каждому файлу непуст, `git status --porcelain` чист, содержимое по LF равно blob HEAD;
      на Deck (копия без git) - токен `c2_git_ok`, записанный `conv_run.py git-token [--addendum REL] [--params-r3 REL]` на ПК (sha256 закоммиченного содержимого) - m2;
  (6) шаг K0 (calib_start.json) записан до первого чтения панелей формирования: `require_k0()`;
  (7) тестовый мир (`--test-world`) не может лежать внутри ~/xdl9 и не может быть предком ~/xdl9 (m1); в режиме test недоступен любой путь под ~/xdl9.
Чтение панелей - только `load_panel_range`: по смещению, с assert на число минут, никогда не за пределами `last_day`.
Не читает: ничего вне формирования, пока не пройдены (1)-(5).
"""
import datetime
import hashlib
import json
import multiprocessing as mp
import os
import struct
import subprocess
import sys
import time

import numpy as np

GRID0_MS = 1_577_836_800_000
MIN_DAY = 1440
D_FORM0 = 2465            # 01.10.2026 UTC, разогрев
D_SIG0 = 2466             # первые сигнальные сутки
D_TRAIN_LAST = 2471       # Pi_train читает сутки до 2471 включительно
D_FORM_LAST = 2478
D_WIN0 = 2479             # торговое окно, НЕ читается без токена
D_WIN_LAST = 2499

RUNNER = "conv_window.py"           # единственный раннер, которому разрешено читать окно

HOME = os.path.expanduser("~/xdl9")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_HINT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))

PARAMS_REL = "docs/research/P-38-params-2026-11-07.csv"
PROTO_REL = "docs/research/P-38-convergence-xdl9.md"
PARAM_KEYS = ("params", "addendum", "r3")          # порядок в params_paths: params G0.1, addendum, params r3
REL_CODE = "tools/compute/statarb/xdl9/conv/"
REL_STATARB = "tools/compute/statarb/"
REL_DOCS = "docs/research/"


def c2_home():
    return os.environ.get("C2_HOME") or os.path.join(HOME, "c2")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def sha256_lf(path):
    """sha256 содержимого с CRLF -> LF: копия на Deck (LF) равна коммиту, рабочая копия на ПК (autocrlf) - тоже (m2)."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def sha256_text_bytes(b):
    return hashlib.sha256(b).hexdigest()


class Refused(SystemExit):
    """Отказ защиты: код выхода 3."""

    def __init__(self, msg):
        sys.stderr.write("ОТКАЗ (защита окна П-38 §12.2): %s\n" % msg)
        super().__init__(3)
        self.msg = msg


def _under(path, root):
    """path лежит внутри root (или равен ему) по realpath; сравнение по компонентам пути, а не по префиксу строки."""
    try:
        r = os.path.realpath(root)
        return os.path.commonpath([os.path.realpath(path), r]) == r
    except ValueError:
        return False


# ------------------------------------------------------------------ состояние защиты (устанавливает точка входа)
class _State:
    go_window = False
    script_path = None            # путь скрипта точки входа (журнал)
    params_paths = ()             # (params G0.1, addendum, params r3) - нужные заменяются None
    mode = "calib"                # 'calib' | 'train' | 'window' | 'test' | 'testb'
    require_k0_flag = True        # K0 обязателен для настоящих чтений формирования
    git_checked = False
    test_root = None              # режим test: читать можно только файлы под этим каталогом (не под ~/xdl9)
    runner = None                 # имя раннера, которому разрешено читать окно (RUNNER)
    extra_inputs = ()             # ((метка, путь), ...) входные файлы раннера, входящие в манифест токена
    code_dir = None               # каталог кода (по умолчанию HERE); тесты подставляют временный
    boundary = None               # режим testb: искусственная граница окна D (сутки D..2478)
    window_cleared = False        # токен проверен и забран в этом процессе (наследуется потомками fork)
    window_logged = False
    token_claimed = None          # путь забранного токена (удаляется в finish_window)
    read_flag = None              # разделяемый (fork) флаг «файл данных окна уже открыт» (mark_window_read); потомки fork видят его


STATE = _State()


def configure(*, go_window=False, script_path=None, params_paths=(), mode="calib", require_k0=True, test_root=None, runner=None, extra_inputs=(), code_dir=None,
              boundary=None):
    # проверки ДО изменения состояния: отказавшийся configure не оставляет разрешающего состояния (API-вызовы в одном процессе)
    if mode == "test" and test_root:
        # m1: ни внутри ~/xdl9, ни предок ~/xdl9 (иначе боевые панели оказались бы «внутри тестового мира»)
        if _under(test_root, HOME):
            raise Refused("тестовый мир не может лежать внутри %s" % HOME)
        if _under(HOME, test_root):
            raise Refused("тестовый мир не может быть предком %s (корень %s)" % (HOME, test_root))
    if mode == "testb":
        if boundary is None or not (D_SIG0 <= boundary <= D_FORM_LAST):
            raise Refused("режим test-boundary: граница D должна быть в %d..%d (задано %s)" % (D_SIG0, D_FORM_LAST, boundary))
        if go_window:
            raise Refused("режим test-boundary несовместим с --go-window")
    STATE.go_window = bool(go_window)
    STATE.script_path = script_path
    STATE.params_paths = tuple(params_paths)
    STATE.mode = mode
    STATE.require_k0_flag = bool(require_k0)
    STATE.git_checked = False
    STATE.test_root = os.path.realpath(test_root) if test_root else None
    STATE.runner = runner
    STATE.extra_inputs = tuple(extra_inputs)
    STATE.code_dir = code_dir
    STATE.boundary = boundary
    STATE.window_cleared = False
    STATE.window_logged = False
    STATE.token_claimed = None
    STATE.read_flag = mp.RawValue("b", 0)


def check_path(path):
    """В режиме test читать можно только под test_root; боевые пути (под ~/xdl9) в test недоступны."""
    if STATE.mode == "test" and STATE.test_root:
        if not _under(path, STATE.test_root):
            raise Refused("режим test: путь вне тестового мира: %s" % path)
        if _under(path, HOME):
            raise Refused("режим test: путь под %s недоступен: %s" % (HOME, path))


def _code_dir():
    return STATE.code_dir or HERE


# ------------------------------------------------------------------ манифест и токен окна
def statarb_dir():
    """Каталог portfolio.py и cell_status.py - тот же поиск, что у conv_agg._load_statarb (в том же каталоге кода)."""
    cd = _code_dir()
    for base in (os.environ.get("C2_STATARB"), os.path.join(cd, "..", ".."), os.path.expanduser("~/xdl9/tools/statarb")):
        if base and os.path.isfile(os.path.join(base, "portfolio.py")) and os.path.isfile(os.path.join(base, "cell_status.py")):
            return os.path.abspath(base)
    return None


def window_manifest(params_paths=None, extra=None):
    """Манифест токена: список (путь-метка, sha256 по LF), по возрастанию метки. Заданный, но отсутствующий файл - Refused (токен нельзя ни выдать, ни принять).
    Метки: код - tools/compute/statarb/xdl9/conv/<имя>, statarb - tools/compute/statarb/<имя>, params - docs/research/<имя>, прочее - как в extra (метка, путь)."""
    items, real = {}, {}

    def add(label, path):
        if not path or not os.path.isfile(path):
            raise Refused("манифест: нет файла %s (%s)" % (label, path))
        rp = os.path.realpath(path)
        if label in items:
            if real[label] == rp:
                return                                  # тот же файл указан дважды (например addendum и маска P-38-params-calib-*.csv)
            raise Refused("манифест: повторная метка %s для разных файлов" % label)
        items[label] = sha256_lf(path)
        real[label] = rp
    cd = _code_dir()
    for f in sorted(os.listdir(cd)):
        if (f.endswith(".py") or f.endswith(".sh")) and os.path.isfile(os.path.join(cd, f)):
            add(REL_CODE + f, os.path.join(cd, f))
    sd = statarb_dir()
    for nm in ("portfolio.py", "cell_status.py"):
        add(REL_STATARB + nm, os.path.join(sd, nm) if sd else None)
    pp = list(params_paths if params_paths is not None else STATE.params_paths)
    for p in pp[:3]:
        if p:
            add(REL_DOCS + os.path.basename(p), p)
    for lab, p in (extra if extra is not None else STATE.extra_inputs):
        add(lab, p)
    return sorted(items.items())


def manifest_lines(params_paths=None, extra=None):
    return ["%s  %s" % (sha, lab) for lab, sha in window_manifest(params_paths, extra)]


def token_text(go_label, params_paths=None, extra=None):
    """Полный текст токена для записи CEO: манифест (по строке `sha256  путь`, по возрастанию пути) и `GO <дата>, CEO`. Файл не пишется."""
    if not go_label.startswith("GO ") or "CEO" not in go_label:
        raise ValueError("метка должна иметь вид «GO <дата>, CEO»")
    return "\n".join(manifest_lines(params_paths, extra) + [go_label]) + "\n"


def token_path():
    return os.path.join(c2_home(), "go_window.token")


def _token_lines():
    p = token_path()
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return [ln.rstrip("\r\n") for ln in f.read().split("\n") if ln.strip() != ""]


def params_sha_line(params_paths=None):
    """Справочная строка `<sha256 params>,<sha256 addendum>[,<sha256 r3>]` (по LF); в токен не входит (токен - манифест)."""
    pp = list(params_paths if params_paths is not None else STATE.params_paths)
    return ",".join(sha256_lf(p) if p and os.path.isfile(p) else "" for p in pp[:3])


def check_window_token(params_paths=None, extra=None):
    """Возвращает None при успехе, иначе строку-причину отказа. Токен не забирает."""
    lines = _token_lines()
    if lines is None:
        return "нет файла-токена %s (его выдаёт только CEO)" % token_path()
    if len(lines) < 2:
        return "токен содержит меньше двух строк (манифест и метка GO)"
    if not lines[-1].startswith("GO ") or "CEO" not in lines[-1]:
        return "последняя строка токена не имеет вида «GO <дата>, CEO»"
    try:
        want = manifest_lines(params_paths, extra)
    except SystemExit as e:
        return "манифест не собран: %s" % getattr(e, "msg", e)
    got = lines[:-1]
    if got != want:
        def to_map(ls):
            m = {}
            for ln in ls:
                if "  " in ln:
                    h, lab = ln.split("  ", 1)
                    m[lab] = h
                else:
                    m["?" + ln] = ""
            return m
        wm, gm = to_map(want), to_map(got)
        diff = sorted([k for k in wm if k not in gm] + [k for k in gm if k not in wm] + [k for k in wm if k in gm and wm[k] != gm[k]])
        return "манифест токена не равен фактическому (файлов: токен %d, факт %d); расходятся: %s" % (len(gm), len(wm), ", ".join(diff[:8]) or "порядок строк")
    return None


def _append_log(rec):
    os.makedirs(c2_home(), exist_ok=True)
    sp = STATE.script_path
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(c2_home(), "window_reads.log"), "a", encoding="utf-8") as f:
        f.write("\t".join([now, os.path.basename(sp or "?"), sha256_lf(sp) if sp and os.path.isfile(sp) else "?", "pid=%d" % os.getpid()] + list(rec)) + "\n")


def log_window_event(kind, text=""):
    _append_log(["event=" + kind, text])


def log_unfinished_runs():
    """Число прогонов окна в window_reads.log, начатых (event=token_claimed) и не завершённых (нет event=run_end и нет event=aborted_before_read - сбой до первого чтения, токен возвращён).
    Снимается только записью CEO строки event=run_end (r3 R3.8 п. 2)."""
    p = os.path.join(c2_home(), "window_reads.log")
    if not os.path.isfile(p):
        return 0
    started = ended = 0
    with open(p, encoding="utf-8") as f:
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            ev = [x for x in parts if x.startswith("event=")]
            if ev and ev[0] == "event=token_claimed":
                started += 1
            elif ev and ev[0] in ("event=run_end", "event=aborted_before_read"):
                ended += 1
    return max(0, started - ended)


def _claim_window_token():
    """Проверка токена и ОДНОРАЗОВЫЙ захват: атомарное переименование. Только один процесс может забрать файл."""
    if STATE.window_cleared:
        return
    why = check_window_token()
    if why:
        raise Refused(why)
    if log_unfinished_runs():
        raise Refused("в window_reads.log есть незавершённый прогон окна (нет event=run_end): отказ до нового решения CEO и записи в «Отступления» (r3 R3.8 п. 2)")
    ensure_params_committed()
    src = token_path()
    dst = "%s.used.%d.%d" % (src, os.getpid(), time.time_ns())
    try:
        os.replace(src, dst)
    except OSError:
        raise Refused("токен уже забран другим вызовом (однократность по файлу): %s" % src)
    STATE.token_claimed = dst
    STATE.window_cleared = True
    log_window_event("token_claimed", "token_sha256=%s manifest_files=%d" % (sha256_lf(dst), len(manifest_lines())))


def window_was_read():
    """Открыт ли хотя бы один файл данных окна в этом прогоне (флаг общий для потомков fork)."""
    return bool(STATE.read_flag is not None and STATE.read_flag.value)


def mark_window_read(last_day, what):
    """Вызывается читателями (load_panel_range, load_turnover_raw, load_funding_range) ПОСЛЕ всех проверок и ДО открытия файла: с этого момента токен восстановить нельзя."""
    if STATE.mode != "window" or last_day is None or last_day < D_WIN0 or STATE.read_flag is None or STATE.read_flag.value:
        return
    STATE.read_flag.value = 1
    log_window_event("first_window_read", what)


def finish_window(outcome="done", reason=""):
    """Конец прогона окна: удалить забранный токен (токен одноразовый) и записать итог в журнал. Вызывается в finally раннера. -> True, если токен возвращён.
    Сбой (outcome == 'failed') ДО первого открытия файла данных окна: токен возвращается переименованием `.used` обратно, в журнал - `event=aborted_before_read` и причина
    (если на месте токена уже лежит другой файл - не затирается, запись о том же и `.used` удаляется)."""
    p = STATE.token_claimed
    if p is None:
        return False
    restored = False
    why = (" reason=" + str(reason)[:300].replace(chr(10), " ").replace(chr(9), " ")) if reason else ""
    try:
        if outcome == "failed" and not window_was_read():
            tp = token_path()
            if os.path.exists(tp):
                log_window_event("aborted_before_read", "token_restored=no (на месте токена уже лежит другой файл: не затёрт)" + why)
            else:
                try:
                    os.replace(p, tp)
                    restored = True
                    log_window_event("aborted_before_read", "token_restored=yes (ни один файл данных окна не открывался)" + why)
                except OSError as e:
                    log_window_event("run_end", "outcome=failed_before_read_token_not_restored (%s)%s" % (e, why))
        else:
            log_window_event("run_end", "outcome=%s%s" % (outcome, why))
    finally:
        STATE.token_claimed = None
        if restored:
            STATE.window_cleared = False
            STATE.window_logged = False
        else:
            try:
                os.remove(p)
            except OSError:
                pass
    return restored


def check_days(last_day, *, what="данные"):
    """Главный вход защиты: вызывается перед ЛЮБЫМ чтением данных. last_day - последние сутки, которые будут прочитаны (включительно)."""
    if STATE.mode == "test":
        return
    if last_day is None:
        raise Refused("не задан last_day для чтения: %s" % what)
    if STATE.mode == "testb":
        if last_day >= D_WIN0:
            raise Refused("режим test-boundary не читает сутки >= %d (запрошено %d, %s)" % (D_WIN0, last_day, what))
        return
    if STATE.mode == "train" and last_day > D_TRAIN_LAST:
        raise Refused("Pi_train не читает сутки > %d (запрошено %d): G-C2-9" % (D_TRAIN_LAST, last_day))
    if STATE.mode in ("calib", "train") and last_day >= D_WIN0:
        raise Refused("калибровочные проходы не принимают last_day >= %d вообще (запрошено %d)" % (D_WIN0, last_day))
    if last_day >= D_WIN0:
        if STATE.mode != "window" or not STATE.go_window:
            raise Refused("сутки >= %d (запрошено %d) без флага --go-window" % (D_WIN0, last_day))
        if STATE.runner != RUNNER:
            raise Refused("окно читает только раннер %s (запрошено из другого пути)" % RUNNER)
        _claim_window_token()
        if not STATE.window_logged:
            STATE.window_logged = True
            _append_log(["last_day=%d" % last_day, what])


# ------------------------------------------------------------------ params закоммичены
def _git(args, cwd):
    try:
        return subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True)
    except OSError:
        return subprocess.CompletedProcess(args, 127, "", "git не найден")


def git_root(start=None):
    r = _git(["rev-parse", "--show-toplevel"], start or _code_dir())
    return r.stdout.strip() if r.returncode == 0 else None


def blob_sha(root, rel):
    """sha256 закоммиченного содержимого (не рабочей копии: autocrlf), как blob_sha в f8_run.sh."""
    try:
        r = subprocess.run(["git", "-C", root, "show", "HEAD:" + rel], capture_output=True)
    except OSError:
        return None
    return sha256_text_bytes(r.stdout.replace(b"\r\n", b"\n")) if r.returncode == 0 else None


def git_token_path():
    return os.path.join(_code_dir(), "c2_git_ok")


def write_git_token(addendum_rel=None, r3_rel=None):
    """На ПК (рабочее дерево git): проверить, что params, протокол (и addendum, params r3) закоммичены и чисты, записать c2_git_ok для копии на Deck."""
    root = git_root()
    if not root:
        raise Refused("--git-token нужно рабочее дерево git")
    rels = [PARAMS_REL, PROTO_REL] + ([addendum_rel] if addendum_rel else []) + ([r3_rel] if r3_rel else [])
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
        if r3_rel:
            f.write("r3_commit=%s\nr3_sha256=%s\n" % out[r3_rel])
        f.write("head=%s\n" % _git(["rev-parse", "HEAD"], root).stdout.strip())
    return out


def _param_entries():
    return [(PARAM_KEYS[i], p) for i, p in enumerate(STATE.params_paths[:3]) if p]


def ensure_params_committed():
    """Отказ, если params / addendum / params r3 не закоммичены. В git-дереве - git (log, status, содержимое по LF = blob HEAD); на Deck - токен c2_git_ok
    с теми же sha256 по LF, что у копий файлов (m2: addendum и r3 проверяются так же, как params)."""
    if STATE.mode == "test" or STATE.git_checked:
        return
    ents = _param_entries()
    if STATE.mode == "window":
        have = {k for k, _ in ents}
        if "params" not in have or "addendum" not in have:
            raise Refused("окно: в конфигурации защиты нет params G0.1 и addendum")
    root = git_root()
    if root:
        rroot = os.path.realpath(root)
        for key, p in ents:
            if not os.path.isfile(p):
                raise Refused("нет файла %s: %s" % (key, p))
            ap = os.path.realpath(p)
            if not _under(ap, rroot):
                raise Refused("%s лежит вне git-дерева (%s): нельзя проверить коммит" % (key, p))
            rel = os.path.relpath(ap, rroot).replace(os.sep, "/")
            if not _git(["log", "-1", "--format=%H", "--", rel], root).stdout.strip():
                raise Refused("%s %s не закоммичен" % (key, rel))
            if _git(["status", "--porcelain", "--", rel], root).stdout.strip():
                raise Refused("%s %s изменён относительно HEAD" % (key, rel))
            if sha256_lf(ap) != blob_sha(root, rel):
                raise Refused("%s %s: содержимое по LF не равно blob HEAD" % (key, rel))
    else:
        tp = git_token_path()
        if not os.path.isfile(tp):
            raise Refused("нет git и нет токена %s: сначала `conv_run.py git-token` на ПК" % tp)
        with open(tp, encoding="utf-8") as f:
            kv = dict(ln.split("=", 1) for ln in f.read().split("\n") if "=" in ln)
        if not ents:
            raise Refused("нет файла params для сверки с токеном c2_git_ok")
        for key, p in ents:
            if not os.path.isfile(p):
                raise Refused("нет файла %s для сверки с токеном c2_git_ok: %s" % (key, p))
            want = kv.get(key + "_sha256")
            if want is None:
                raise Refused("в c2_git_ok нет %s_sha256 (записать `conv_run.py git-token` с этим файлом)" % key)
            if sha256_lf(p) != want:
                raise Refused("sha256 (LF) копии %s не равен записанному в c2_git_ok" % key)
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
    mark_window_read(d1, what + " " + os.path.basename(path))
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
    mark_window_read(d1, "turnover " + os.path.basename(path))
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
    mark_window_read(d1, "funding " + os.path.basename(path))
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
