#!/usr/bin/env python3
"""TK-493, П-38: тесты раннера окна и защиты (закрытие B1, m1, m2 ревизии 07.11): красные тесты защиты (CLI и API), токен v2 (манифест всего кода), одноразовость,
test-boundary, m1 (тестовый мир - предок ~/xdl9), git-токен по LF, старт окна «с нуля» в срезе W2, Холм, сверка раннера с conv_run на синтетике (G-C2-9w в миниатюре).
Запуск на Deck (в каталоге кода):  ~/xdl9/venv/bin/python test_conv_window.py [имя_теста ...]
Данные реальных панелей и окна не читаются (синтетика во временных каталогах)."""
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G            # noqa: E402
import conv_hints as HI           # noqa: E402
import conv_lib as L              # noqa: E402
import test_conv as TC            # noqa: E402  (write_world, run_cli)

OK = []


def test(fn):
    OK.append(fn)
    return fn


def refused(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SystemExit as e:
        assert e.code == 3, "код выхода %r вместо 3" % (e.code,)
        return
    raise AssertionError("отказа нет: %s%s" % (getattr(fn, "__name__", fn), a))


def w(path, text, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if binary else "w", **({} if binary else dict(encoding="utf-8", newline=""))) as f:
        f.write(text)


class Env:
    """Временный «репозиторий»: код (.py/.sh), statarb (portfolio.py, cell_status.py), params G0.1 + addendum + r3, один входной файл."""

    def __init__(self):
        self.d = tempfile.mkdtemp()
        self.code = os.path.join(self.d, "code")
        self.sa = os.path.join(self.d, "statarb")
        self.home = os.path.join(self.d, "c2")
        os.makedirs(self.home)
        for nm, txt in (("a.py", "x = 1\n"), ("b.py", "y = 2\n"), ("run.sh", "echo 1\n"), ("notes.txt", "не хэшируется\n")):
            w(os.path.join(self.code, nm), txt)
        w(os.path.join(self.sa, "portfolio.py"), "# portfolio\n")
        w(os.path.join(self.sa, "cell_status.py"), "# cell_status\n")
        self.pf, self.af, self.r3 = (os.path.join(self.d, n) for n in ("params.csv", "addendum.csv", "params_r3.csv"))
        for p in (self.pf, self.af, self.r3):
            w(p, "key,value\nk,%s\n" % os.path.basename(p))
        self.inp = os.path.join(self.d, "classpar_full.json")
        w(self.inp, "{}\n")
        self.script = os.path.join(self.code, "a.py")
        os.environ["C2_HOME"] = self.home
        os.environ["C2_STATARB"] = self.sa

    def cfg(self, mode="window", go=True, runner=G.RUNNER, r3=True, **kw):
        G.configure(mode=mode, go_window=go, runner=runner, params_paths=(self.pf, self.af, self.r3 if r3 else None), extra_inputs=[("input:" + self.inp, self.inp)],
                    code_dir=self.code, script_path=self.script, **kw)
        G.STATE.git_checked = True            # git-проверка params - отдельный тест

    def token(self, go="GO 07.11, CEO", text=None):
        w(os.path.join(self.home, "go_window.token"), text if text is not None else G.token_text(go))

    def clean(self):
        os.environ.pop("C2_HOME", None)
        os.environ.pop("C2_STATARB", None)
        G.configure(mode="calib")


def lf_crlf(p):
    b = open(p, "rb").read()
    w(p, b.replace(b"\n", b"\r\n"), binary=True)


@test
def w01_manifest_covers_all_code_and_inputs():
    e = Env()
    try:
        e.cfg()
        labs = [l for l, _ in G.window_manifest()]
        cp, sp, dp = G.REL_CODE, G.REL_STATARB, G.REL_DOCS
        assert labs == sorted([cp + "a.py", cp + "b.py", cp + "run.sh", sp + "portfolio.py", sp + "cell_status.py", dp + "params.csv", dp + "addendum.csv", dp + "params_r3.csv",
                               "input:" + e.inp]), labs                      # метки - пути (r3 R3.8): код conv/, statarb/, docs/research/
        assert cp == "tools/compute/statarb/xdl9/conv/" and labs == sorted(labs), "порядок - по возрастанию пути"
        before = G.manifest_lines()
        w(os.path.join(e.code, "zzz.py"), "z = 3\n")                       # новый .py входит в манифест
        assert G.manifest_lines() != before and cp + "zzz.py" in [l for l, _ in G.window_manifest()]
        os.remove(os.path.join(e.code, "zzz.py"))
        assert G.manifest_lines() == before
        lf_crlf(os.path.join(e.code, "a.py"))                              # CRLF-копия даёт тот же sha256 (m2)
        lf_crlf(e.pf)
        assert G.manifest_lines() == before, "хэш по LF зависит от переводов строк"
        os.remove(e.r3)                                                    # заданный, но отсутствующий файл - отказ
        refused(G.window_manifest)
    finally:
        e.clean()


@test
def w02_token_accepts_exact_manifest_and_refuses_tamper():
    e = Env()
    try:
        e.cfg()
        e.token()
        assert G.check_window_token() is None
        good = G.token_text("GO 07.11, CEO")

        def tampered(label, mutate):
            e.cfg()
            e.token(text=good)
            undo = mutate()
            why = G.check_window_token()
            assert why, "токен принят при: " + label
            e.cfg()
            refused(G.check_days, 2479)
            assert os.path.exists(os.path.join(e.home, "go_window.token")), "токен забран при отказе: " + label
            undo()
        def edit(path, new):
            old = open(path, "rb").read()
            w(path, new, binary=True)
            return lambda: w(path, old, binary=True)
        tampered("изменён код a.py", lambda: edit(os.path.join(e.code, "a.py"), b"x = 99\n"))
        tampered("изменён run.sh", lambda: edit(os.path.join(e.code, "run.sh"), b"echo 2\n"))
        tampered("изменён статарб cell_status.py", lambda: edit(os.path.join(e.sa, "cell_status.py"), b"# changed\n"))
        tampered("изменён portfolio.py", lambda: edit(os.path.join(e.sa, "portfolio.py"), b"# changed\n"))
        tampered("изменён params G0.1", lambda: edit(e.pf, b"key,value\nk,other\n"))
        tampered("изменён addendum", lambda: edit(e.af, b"key,value\nk,other\n"))
        tampered("изменён params r3", lambda: edit(e.r3, b"key,value\nk,other\n"))
        tampered("изменён входной файл (classpar)", lambda: edit(e.inp, b'{"x": 1}\n'))

        def add_extra():
            p = os.path.join(e.code, "extra.py")
            w(p, "q = 1\n")
            return lambda: os.remove(p)
        tampered("добавлен лишний .py", add_extra)
        # правки самого токена
        for label, text in (("без строки манифеста", "\n".join(good.split("\n")[:3] + good.split("\n")[4:])),
                            ("без первой строки манифеста", "\n".join(good.split("\n")[1:])),
                            ("лишняя (повторная) строка манифеста", good.split("\n")[0] + "\n" + good),
                            ("строки манифеста в обратном порядке", "\n".join(list(reversed(good.split("\n")[:-2])) + good.split("\n")[-2:])),
                            ("без метки GO", "\n".join(good.split("\n")[:-2]) + "\n"),
                            ("метка без CEO", good.replace("GO 07.11, CEO", "GO 07.11, кто-то")),
                            ("метка без GO", good.replace("GO 07.11, CEO", "07.11, CEO")),
                            ("старый формат из трёх строк", "deadbeef,\n" + G.sha256_file(e.script) + "\nGO 07.11, CEO\n"),
                            ("пустой", "")):
            e.cfg()
            e.token(text=text)
            assert G.check_window_token(), "токен принят: " + label
        # params r3 заданы в конфигурации, а токен выдан без него
        e.cfg(r3=False)
        e.token(text=good)
        assert G.check_window_token(), "токен с r3 принят без r3 в конфигурации"
        e.cfg(r3=False)
        e.token()
        assert G.check_window_token() is None
        e.cfg()
        e.token()
        assert G.check_window_token() is None
    finally:
        e.clean()


@test
def w03_token_is_single_use_and_logged():
    e = Env()
    try:
        e.cfg()
        e.token()
        G.check_days(2479, what="первое чтение")
        assert not os.path.exists(os.path.join(e.home, "go_window.token")), "токен остался на месте после захвата"
        used = [f for f in os.listdir(e.home) if f.startswith("go_window.token.used.")]
        assert len(used) == 1 and G.STATE.window_cleared
        G.check_days(2499, what="второе чтение")                      # в процессе токен уже принят
        log = open(os.path.join(e.home, "window_reads.log"), encoding="utf-8").read().strip().split("\n")
        assert sum("event=token_claimed" in ln for ln in log) == 1 and sum("last_day=2479" in ln for ln in log) == 1, log
        G.finish_window("done")
        assert not [f for f in os.listdir(e.home) if f.startswith("go_window.token")], "токен не удалён"
        assert "event=run_end" in open(os.path.join(e.home, "window_reads.log"), encoding="utf-8").read()
        # второй вызов: токена нет - отказ (однократность по файлу)
        e.cfg()
        refused(G.check_days, 2479)
        # токен выдан заново (вторым решением CEO) - снова принимается один раз
        e.token()
        G.check_days(2480)
        G.finish_window("failed")
        e.cfg()
        refused(G.check_days, 2480)
        # прогон оборван без finally (kill): в журнале нет run_end - новый токен не принимается, пока CEO не запишет строку run_end
        e.cfg()
        e.token()
        G.check_days(2479)
        assert G.log_unfinished_runs() == 1
        e.cfg()                                                        # «новый процесс» (состояние не наследуется)
        e.token()
        refused(G.check_days, 2479)
        assert os.path.exists(os.path.join(e.home, "go_window.token")), "токен забран при отказе по журналу"
        with open(os.path.join(e.home, "window_reads.log"), "a", encoding="utf-8") as lf:
            lf.write("2026-11-07T00:00:00Z" + chr(9) + "ceo" + chr(9) + "-" + chr(9) + "pid=0" + chr(9) + "event=run_end" + chr(9) + "outcome=acknowledged-by-CEO" + chr(10))
        assert G.log_unfinished_runs() == 0
        e.cfg()
        G.check_days(2479)
        G.finish_window("done")
        assert G.log_unfinished_runs() == 0
        # гонка: другой процесс успел забрать файл между проверкой манифеста и захватом - второй захват отказан
        e.cfg()
        e.token()
        p = os.path.join(e.home, "go_window.token")
        orig = G.check_window_token
        G.check_window_token = lambda *a, **k: (os.remove(p), None)[1]
        try:
            refused(G.check_days, 2479)
        finally:
            G.check_window_token = orig
        assert not G.STATE.window_cleared
    finally:
        e.clean()


@test
def w04_window_requires_flag_mode_and_the_one_runner():
    e = Env()
    try:
        e.token()
        e.cfg(go=False)
        refused(G.check_days, 2479)
        e.cfg(runner=None)
        refused(G.check_days, 2479)
        e.cfg(runner="conv_run.py")
        refused(G.check_days, 2479)
        for mode in ("calib", "train"):
            e.cfg(mode=mode)
            refused(G.check_days, 2479)
            refused(G.check_days, 2499)
        e.cfg(mode="train")
        refused(G.check_days, 2472)
        G.check_days(2471)
        assert os.path.exists(os.path.join(e.home, "go_window.token")), "токен забран при отказах"
        assert not os.path.exists(os.path.join(e.home, "window_reads.log"))
    finally:
        e.clean()


@test
def w05_test_boundary_never_reads_the_window():
    e = Env()
    try:
        e.token()                                                       # даже валидный токен режиму testb недоступен
        e.cfg(mode="testb", go=False, boundary=2472)
        G.check_days(2478)
        for last in (2479, 2480, 2499):
            refused(G.check_days, last)
        ghost = os.path.join(e.d, "nonexistent", "X.sap")              # отказ идёт ДО открытия файла: иначе был бы FileNotFoundError
        refused(G.load_panel_range, ghost, 2465, 2479)
        refused(G.load_panel_range, ghost, 2465, 2499)
        refused(G.load_turnover_raw, ghost, 2465, 2479)
        refused(G.load_funding_range, ghost, 2465, 2480)
        refused(G.check_days, None)
        assert os.path.exists(os.path.join(e.home, "go_window.token")) and not os.path.exists(os.path.join(e.home, "window_reads.log"))
        for bad in (2479, 2499, 2465, 0, None):
            refused(lambda b=bad: G.configure(mode="testb", boundary=b))
        refused(lambda: G.configure(mode="testb", boundary=2472, go_window=True))
        G.configure(mode="testb", boundary=2466)
        G.configure(mode="testb", boundary=2478)
    finally:
        e.clean()


@test
def w06_test_world_may_not_be_ancestor_of_home():
    e = Env()
    try:
        home = os.path.realpath(G.HOME)
        for root in (os.path.dirname(home), os.path.dirname(os.path.dirname(home)), home, os.path.join(home, "c2", "x"), os.path.join(home, "panels")):
            refused(lambda r=root: G.configure(mode="test", test_root=r))
        G.configure(mode="test", test_root=home + "_neighbour")         # сосед по префиксу строки - не предок и не потомок: допустим
        tw = tempfile.mkdtemp()
        G.configure(mode="test", test_root=tw)
        G.check_path(os.path.join(tw, "panels", "a.sap"))
        refused(G.check_path, os.path.join(G.HOME, "panels", "bybit", "BTCUSDT.sap"))
        refused(G.check_path, tw + "2/x.sap")                          # сосед по префиксу строки - вне тестового мира
        refused(G.check_path, "/etc/passwd")
        link = os.path.join(tempfile.mkdtemp(), "ln")
        try:
            os.symlink(home, link)
        except OSError:
            return
        refused(lambda: G.configure(mode="test", test_root=link))       # симлинк на ~/xdl9 раскрывается
    finally:
        e.clean()


@test
def w07_git_token_checks_params_addendum_and_r3_by_lf():
    e = Env()
    try:
        def gtok(**kv):
            w(os.path.join(e.code, "c2_git_ok"), "".join("%s=%s\n" % kv_ for kv_ in kv.items()))
        sha = lambda p: G.sha256_lf(p)       # noqa: E731
        good = dict(params_sha256=sha(e.pf), addendum_sha256=sha(e.af), r3_sha256=sha(e.r3), head="x")
        gtok(**good)
        assert G.git_root(e.code) is None, "временный каталог внутри git - тест неприменим"
        e.cfg(mode="calib", go=False, runner=None)
        G.STATE.git_checked = False
        G.ensure_params_committed()
        lf_crlf(e.af)                                                    # CRLF-копия на Deck равна закоммиченному LF
        G.STATE.git_checked = False
        G.ensure_params_committed()
        w(e.af, "key,value\nk,изменён\n")
        G.STATE.git_checked = False
        refused(G.ensure_params_committed)
        gtok(**dict(good, addendum_sha256=sha(e.af)))
        G.STATE.git_checked = False
        G.ensure_params_committed()
        w(e.r3, "key,value\nk,r3-изменён\n")                              # params r3 тоже сверяется
        G.STATE.git_checked = False
        refused(G.ensure_params_committed)
        g2 = dict(good, addendum_sha256=sha(e.af))
        del g2["r3_sha256"]                                              # r3 задан, а в токене его нет - отказ
        gtok(**g2)
        G.STATE.git_checked = False
        refused(G.ensure_params_committed)
        os.remove(os.path.join(e.code, "c2_git_ok"))
        G.STATE.git_checked = False
        refused(G.ensure_params_committed)
        # режим окна требует params и addendum в конфигурации
        e.cfg(mode="window", go=True)
        G.configure(mode="window", go_window=True, runner=G.RUNNER, params_paths=(e.pf, None), code_dir=e.code)
        refused(G.ensure_params_committed)
        # в калибровочных режимах addendum и r3 не обязательны (прежнее поведение)
        gtok(**good)
        G.configure(mode="calib", params_paths=(e.pf, None), code_dir=e.code)
        G.ensure_params_committed()
    finally:
        e.clean()


def cli(args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(HERE, "conv_window.py")] + args, capture_output=True, text=True, env=e)


def write_spec(d, variants, extra=None):
    cp = os.path.join(d, "classpar_full.json")
    json.dump({"crypto": {"kstale": 30, "m_r": 5, "q_stale": 0.9}}, open(cp, "w"))
    spec = dict(name="тест", root=d, params=None, addendum=None, params_r3=None, w2_jobs=None, w2_k_ref=None, variants=[])
    for v in variants:
        spec["variants"].append(dict(id=v["id"], cls="crypto", combo=v["combo"], classpar=cp, q5=v.get("q5"), sigma_form=5.0, a3b=v.get("a3b"), formation_labels=v.get("formation_labels")))
    spec.update(extra or {})
    p = os.path.join(d, "spec.json")
    json.dump(spec, open(p, "w"), ensure_ascii=False)
    return p


@test
def w08_cli_red_tests():
    d = tempfile.mkdtemp()
    home = os.path.join(d, "c2")
    os.makedirs(home)
    spec = write_spec(d, [dict(id="V1", combo=[25.2, 3, 1, 120, 2.0])])
    env = {"C2_HOME": home}
    out = os.path.join(d, "out")
    base = ["--spec", spec, "--out", out]
    cases = [
        (["run"], "без флагов и spec"),
        (["run"] + base, "без режима"),
        (["run", "--go-window"] + base, "с флагом, без токена"),
        (["run", "--go-window", "--test-boundary", "2472"] + base, "два режима"),
        (["run", "--test-boundary", "2479"] + base, "граница 2479"),
        (["run", "--test-boundary", "2499"] + base, "граница 2499"),
        (["run", "--test-boundary", "2465"] + base, "граница ниже 2466"),
        (["run", "--test-boundary", "2472", "--last-day", "2480"] + base, "last-day >= 2479 вне синтетики"),
        (["run", "--go-window", "--test-world", d] + base, "окно в тестовом мире"),
        (["run", "--go-window", "--limit-pairs", "3"] + base, "limit-pairs в окне"),
        (["run", "--test-boundary", "2472", "--test-world", os.path.dirname(G.HOME)] + base, "тестовый мир - предок ~/xdl9 (m1)"),
        (["run", "--test-boundary", "2472", "--test-world", G.HOME] + base, "тестовый мир = ~/xdl9"),
        (["run", "--test-boundary", "2472", "--test-world", os.path.join(G.HOME, "c2")] + base, "тестовый мир внутри ~/xdl9"),
    ]
    for args, label in cases:
        r = cli(args, env)
        assert r.returncode == 3, (label, r.returncode, r.stderr[-300:])
    assert not os.path.exists(out) and not os.path.exists(os.path.join(home, "window_reads.log")), "выходы или журнал созданы при отказе"
    # токен есть, но неверный (произвольный манифест): отказ и токен не забран
    tp = os.path.join(home, "go_window.token")
    open(tp, "w").write("0" * 64 + "  tools/compute/statarb/xdl9/conv/conv_window.py\nGO 07.11, CEO\n")
    r = cli(["run", "--go-window"] + base, env)
    assert r.returncode == 3, r.stderr[-300:]
    assert os.path.exists(tp) and not [f for f in os.listdir(home) if ".used." in f], "токен забран при отказе"
    assert not os.path.exists(out)
    # старый путь conv_run: --mode window отключён
    for extra in ([], ["--go-window"]):
        r = subprocess.run([sys.executable, os.path.join(HERE, "conv_run.py"), "series", "--last-day", "2499", "--mode", "window", "--out", d] + extra, capture_output=True, text=True,
                           env=dict(os.environ, **env))
        assert r.returncode == 3, r.stderr[-300:]
    assert "conv_window.py" in r.stderr


def pids_of(tw):
    import conv_world as W
    return [p["pid"] for p in W.World(tw, test=True).universe(check_sha=False) if p["cls"] == "crypto"]


@test
def w09_runner_equals_conv_run_on_synthetic_world():
    """G-C2-9w в миниатюре: раннер (test-world + test-boundary) и `conv_run grid --first-sig-day` на одном мире дают одни и те же таблицы и сделки; потоки 1 и 3 - тоже."""
    tw = tempfile.mkdtemp()
    TC.write_world(tw, n_assets=8)
    d = tempfile.mkdtemp()
    pids = pids_of(tw)
    q5 = {p: (20.0 if i % 4 else None) for i, p in enumerate(pids)}          # 20 <= T/4 = 30: три четверти пар допущены, четверть - нет
    q5p = os.path.join(d, "q5_full.json")
    json.dump(q5, open(q5p, "w"))
    cpf = os.path.join(d, "classpar_full.json")
    cph = os.path.join(d, "classpar_H0.json")
    json.dump({"crypto": {"kstale": 25, "m_r": 4, "q_stale": 0.9}}, open(cph, "w"))
    a3b1 = {"h1on0": {"combo": [25.2, 3, 1, 120, 2.0], "classpar": cph}, "h0on1": {"combo": [40.0, 3, 1, 360, 2.0], "classpar": cph}}
    ok_fl = {k: {"ok": True, "text": "формирование: %s пройдено" % k} for k in ("a3b", "a3_cells", "a8")}
    weak_fl = dict(ok_fl, a3b={"ok": False, "text": "формирование: a3b не пройдено"})
    V = [dict(id="V1", combo=[25.2, 3, 1, 120, 2.0], q5=q5p, a3b=a3b1, formation_labels=weak_fl), dict(id="V2", combo=[40.0, 3, 3, 360, None], formation_labels=ok_fl)]
    spec = write_spec(d, V)
    D = 2468
    outs = []
    for th in (1, 3):
        out = os.path.join(d, "out%d" % th)
        r = cli(["run", "--spec", spec, "--test-world", tw, "--test-boundary", str(D), "--out", out, "--threads", str(th)])
        assert r.returncode == 0, r.stderr[-1500:] + r.stdout[-500:]
        outs.append(out)
    # baseline: conv_run series + grid с теми же комбинациями, класспараметрами и q5
    cp = os.path.join(d, "classpar_full.json")
    base = os.path.join(d, "base")
    bdirs = {}
    for v in V:
        cf = os.path.join(d, "combos_%s.json" % v["id"])
        json.dump([v["combo"]], open(cf, "w"))
        o = os.path.join(base, v["id"])
        common = ["--test-world", tw, "--out", o, "--last-day", "2478", "--threads", "2", "--first-sig-day", str(D)]
        r = TC.run_cli(["series"] + common)
        assert r.returncode == 0, r.stderr[-800:]
        r = TC.run_cli(["grid"] + common + ["--tag", "b", "--combos", cf, "--classpar", cp, "--trades-out"] + (["--q5", v["q5"]] if v.get("q5") else []))
        assert r.returncode == 0, r.stderr[-800:]
        bdirs[v["id"]] = os.path.join(o, "calib", "grid_b")
    # перекрёстный прогон A3b окна (V1: c_H0 на парах H1 и c_H1 на парах H0) против conv_run grid --pairs-file
    import conv_agg as AGm
    import conv_window as WNm
    import conv_world as Wm
    U = [p_ for p_ in Wm.World(tw, test=True).universe(check_sha=False) if p_["cls"] == "crypto"]
    cross_base = {}
    for name, half in (("h1on0", 1), ("h0on1", 0)):
        pf_ = os.path.join(d, "pairs_%s.txt" % name)
        open(pf_, "w").write(chr(10).join(p_["pid"] for p_ in U if WNm.half_of(p_["key"]) == half) + chr(10))
        cf = os.path.join(d, "combos_%s.json" % name)
        json.dump([a3b1[name]["combo"]], open(cf, "w"))
        o = os.path.join(base, name)
        common = ["--test-world", tw, "--out", o, "--last-day", "2478", "--threads", "2", "--first-sig-day", str(D)]
        assert TC.run_cli(["series"] + common).returncode == 0
        r = TC.run_cli(["grid"] + common + ["--tag", "b", "--combos", cf, "--classpar", cph, "--pairs-file", pf_, "--q5", q5p])
        assert r.returncode == 0, r.stderr[-800:]
        gx = AGm.Grid(os.path.join(o, "calib", "grid_b"), "crypto")
        ser_ = AGm.y_series(gx, 0, np.arange(len(gx.pids)))["primary"]
        cross_base[name] = (sum(ser_) / len(ser_) if ser_ else None, int(gx.tab[0][:, :, AGm.FI["n"]].sum()))
    res0 = json.load(open(os.path.join(outs[0], "variants.json")))
    for name in ("h1on0", "h0on1"):
        got = res0[0]["a3b_window"][name]
        assert got["mean"] == cross_base[name][0] and got["trades"] == cross_base[name][1], (name, got["mean"], cross_base[name])
    assert res0[1]["a3b_window"] is None and "не посчитано" in res0[1]["window_labels"]["A3b"]
    assert res0[0]["window_labels"]["A3b"] in ("A3b окна: нет", "A3b окна: ок")
    for out in outs:
        r = cli(["gate9w", "--run", out] + sum([["--base", "%s=%s" % (k, v)] for k, v in sorted(bdirs.items())], []))
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
        rep = json.load(open(os.path.join(out, "g_c2_9w.json")))
        assert rep["ok"] and all(v["ok"] for v in rep["variants"].values())
    ntr = {vid: rep["variants"][vid]["trades_n"] for vid in ("V1", "V2")}
    assert ntr["V1"] > 0, "в синтетике у V1 нет сделок - сверка пуста: %s" % ntr
    # потоки 1 и 3 дают равные выходы
    for fn in ("variants.json", "class_status.csv", "trials_total.json", "daily_y.csv", "slices.csv", "a8.csv", "a3b_window.csv", "cells_table.csv"):
        assert open(os.path.join(outs[0], fn), "rb").read() == open(os.path.join(outs[1], fn), "rb").read(), "потоки 1/3: различается " + fn
    res = json.load(open(os.path.join(outs[0], "variants.json")))
    assert [r["id"] for r in res] == ["V1", "V2"]
    for r in res:
        assert r["series_from_trades_equals_grid"], "ряд из сделок не равен ряду сетки"
        assert r["holm"]["M"] == 2 and r["controls"]["random_entries"]["seed"] == 37 and r["status"]["label"] in WNm.RANK
        assert r["first_sig"] == D and r["d1"] == 2478
    tot = json.load(open(os.path.join(outs[0], "trials_total.json")))
    assert tot["M_window"] == 2 and tot["f8_cells"] == 133
    assert tot["total_tests_on_window"] == sum(g_["count"] for g_ in tot["groups"]) == 133 + 2 + tot["w2_rows"] + 7 * 2 + tot["a8_runs"] + tot["a3b_runs"] + tot["slices_rows"], tot
    assert tot["a3b_runs"] == 4 + 2 and tot["controls_total"] == 14
    # выходы только под --out, окно/токен не использовались
    meta = json.load(open(os.path.join(outs[0], "run_meta.json")))
    assert meta["mode"] == "test" and meta["boundary"] == D and meta["d1"] == 2478
    print("   сделок V1/V2: %s; сверка с conv_run - до бита; потоки 1/3 равны" % ntr)


@test
def w10_pair_table_starts_from_zero_at_boundary():
    n = 4000
    t_b = 2400
    lv = np.zeros(n)
    lv[1800:1810] = 100.0                      # до границы: эпизод и сделка
    lv[2390:2410] = 100.0                      # через границу: «хвост» эпизода не даёт входа
    lv[3000:3010] = 100.0                      # после границы: эпизод и сделка
    cA = 100.0 * np.exp(lv / 1e4)
    cB = np.full(n, 100.0)
    oA = np.concatenate([[cA[0]], cA[:-1]])
    oB = np.concatenate([[cB[0]], cB[:-1]])
    full = {r["theta"]: r for r in HI.pair_table(oA, cA, oB, cB, 0, thetas=(40,)) if r["var"] == "rel"}[40]
    same = {r["theta"]: r for r in HI.pair_table(oA, cA, oB, cB, 0, thetas=(40,), t_start=0) if r["var"] == "rel"}[40]
    assert full == same, "t_start=0 меняет прежний результат"
    win = {r["theta"]: r for r in HI.pair_table(oA, cA, oB, cB, 0, thetas=(40,), t_start=t_b) if r["var"] == "rel"}[40]
    assert full["n"] == 3 and full["n_ep"] == 3, full
    assert win["n"] == 1, ("в окне ровно одна сделка (после границы)", win)
    assert win["n_ep"] == 2, ("эпизоды окна: хвост через границу и эпизод после неё", win)
    # окно целиком до эпизода: нет ни сделок, ни входа по хвосту
    win2 = {r["theta"]: r for r in HI.pair_table(oA, cA, oB, cB, 0, thetas=(40,), t_start=2405) if r["var"] == "rel"}[40]
    assert win2["n"] == 1, win2
    win3 = {r["theta"]: r for r in HI.pair_table(oA, cA, oB, cB, 0, thetas=(40,), t_start=3500) if r["var"] == "rel"}[40]
    assert win3["n"] == 0 and win3["n_ep"] == 0, win3


@test
def w11_holm_r3_rule_by_interval():
    """r3 R3.2: M=1 - alpha 0,10; M=2 - (i) alpha 0,05 либо (ii) alpha 0,10 и другой по (i); M>2 - пошаговый Холм alpha_j = 0,10/(M-j+1); отдельно по t и по бутстрепу."""
    import conv_window as WN
    P, CS = WN.P, WN.CS

    def mk(series):
        return dict(series=dict(primary=series), main=WN.stat_block(series))
    a = [5.0, 5.2, 4.8, 5.1, 4.9, 5.0, 5.3] * 3                         # сильный плюс
    rng = np.random.default_rng(5)
    noise = [float(x) for x in rng.normal(0.0, 8.0, 21)]
    weak = [x + 3.2 for x in noise]                                      # слабый плюс: граница при alpha 0,10 около нуля
    res = [mk(a), mk(noise)]
    WN.holm_block(res)
    for r in res:
        assert r["holm"]["M"] == 2 and r["holm"]["t"]["rule"].startswith("M=2")
    assert res[0]["holm"]["rank"] == 1 and res[0]["holm"]["pass_both"] is True and res[1]["holm"]["pass_both"] is False
    lo05 = P.block_t_interval(a, 0.05, res[0]["main"]["block"])[1]
    assert abs(res[0]["holm"]["t"]["lo_a05"] - lo05) < 1e-12 and res[0]["holm"]["t"]["by_i"] is True
    lo05b = P.block_boot_t_interval(list(a), 0.05, CS.BOOT_B, CS.BOOT_SEED, res[0]["main"]["block"])[1]
    assert abs(res[0]["holm"]["boot"]["lo_a05"] - lo05b) < 1e-12
    # (ii): слабый вариант проходит при alpha 0,10, если другой выполняет (i); один и тот же слабый вариант с шумом - нет
    cand = mk(weak)
    lo10 = P.block_t_interval(weak, 0.10, cand["main"]["block"])[1]
    lo05w = P.block_t_interval(weak, 0.05, cand["main"]["block"])[1]
    res2 = [mk(a), cand]
    WN.holm_block(res2)
    want_t = bool(lo05w > 0 or (lo10 > 0 and True))                     # другой (a) выполняет (i)
    assert res2[1]["holm"]["t"]["passed"] is want_t, (lo10, lo05w, res2[1]["holm"]["t"])
    res3 = [mk(noise), cand]                                            # другой не выполняет (i): (ii) не работает
    WN.holm_block(res3)
    assert res3[1]["holm"]["t"]["by_ii"] is False
    assert res3[1]["holm"]["t"]["passed"] is bool(lo05w > 0)
    # M = 1: alpha 0,10
    one = [mk(a)]
    WN.holm_block(one)
    assert one[0]["holm"]["t"]["rule"].startswith("M=1") and one[0]["holm"]["pass_both"] is True
    lo = P.block_t_interval(weak, 0.10, cand["main"]["block"])[1]
    one2 = [mk(weak)]
    WN.holm_block(one2)
    assert one2[0]["holm"]["t"]["passed"] is bool(lo > 0)
    # M = 3: пошаговый Холм, alpha_j = 0,10 / (M - j + 1)
    three = [mk(a), mk(noise), mk([x - 1.0 for x in a])]
    WN.holm_block(three)
    ranks = sorted(three, key=lambda r: r["holm"]["rank"])
    assert [r["holm"]["rank"] for r in ranks] == [1, 2, 3]
    for r in three:
        aj = 0.10 / (3 - r["holm"]["rank"] + 1)
        assert abs(r["holm"]["t"]["alpha_j"] - aj) < 1e-15
    assert three[0]["holm"]["pass_both"] is True                        # сильный плюс проходит на первой ступени alpha 0,10/3
    # ступенчатость: шум занимает ранг 3 и не проходит; ряд a - 1 не проходит выше шума
    assert three[1]["holm"]["pass_both"] is False


@test
def w12_r3_status_ladder_and_thresholds():
    import conv_window as WN
    assert WN.thresholds_window(21) == (30, 7) and WN.thresholds_window(7) == (10, 3)

    def R(closed=40, assets=8, days=9, net=3.0, n_eff=7, holm=True):
        return dict(trades=dict(closed=closed, assets_closed=assets, days_with_trades=days, net_closed_trade=net), main=dict(n_eff=n_eff), holm=dict(pass_both=holm))
    ok = {k: {"ok": True, "text": k} for k in ("a3b", "a3_cells", "a8")}
    weak = dict(ok, a3_cells={"ok": False, "text": "A3 по клеткам не пройдено"})
    assert WN.r3_status(R(), 21, ok)["label"] == "кандидат"
    s = WN.r3_status(R(), 21, weak)
    assert s["confirmed"] and s["label"].startswith("подтверждено на окне при слабом формировании")
    for kw, why in ((dict(closed=29), "29 сделок"), (dict(assets=4), "4 актива"), (dict(days=6), "6 суток"), (dict(n_eff=2), "n_эфф 2"), (dict(net=0.0), "чистая 0"), (dict(net=None), "нет сделок"),
                    (dict(holm=False), "Холм")):
        st = WN.r3_status(R(**kw), 21, ok)
        assert st["label"] == "не подтверждено" and not st["confirmed"], why
    assert WN.r3_status(R(), 21, None)["label"].startswith("подтверждено на окне при слабом")          # меток формирования нет - «кандидатом» стать нельзя
    assert WN.r3_status(R(closed=10, days=3), 7, ok)["label"] == "кандидат"                      # окно 7 суток (проверка на формировании): пороги 10 и 3
    assert WN.RANK["кандидат"] > WN.RANK["подтверждено на окне при слабом формировании: повторить на новых сутках"] > WN.RANK["не подтверждено"]


@test
def w13_cli_token_covers_every_file_kind():
    """CLI: токен, выпущенный `token-body` для копии каталога кода, отвергается при ЛЮБОМ изменении (код, лишний/недостающий файл, spec, classpar, q5-соседи, jobs, params), код 3,
    токен не забирается. Запуск окна с валидным токеном здесь НЕ делается: проверяется только отказ и read-only `token-check`."""
    import shutil
    d = tempfile.mkdtemp()
    code = os.path.join(d, "conv")
    shutil.copytree(HERE, code, ignore=shutil.ignore_patterns("__pycache__", "c2_git_ok", "*.pyc"))
    sa = G.statarb_dir()
    assert sa, "не найден каталог statarb (portfolio.py, cell_status.py)"
    home = os.path.join(d, "c2")
    os.makedirs(home)
    jobs = os.path.join(d, "jobs_w2.csv")
    w(jobs, "name,ex1,sym1,ex2,sym2,in_universe,market,pid\n")
    kref = os.path.join(d, "pairs_all.csv")
    w(kref, "ex1,sym1,ex2,sym2,k\n")
    pf, af, r3 = (os.path.join(d, n) for n in ("P-38-params-2026-11-07.csv", "P-38-params-calib-2026-11-07.csv", "P-38-params-r3-2026-11-07.csv"))
    for p_ in (pf, af, r3):
        w(p_, "key,value\nk,v\n")
    w(os.path.join(d, "P-38-params-calib-extra.csv"), "key,value\nk,extra\n")
    q5 = os.path.join(d, "q5_full.json")
    w(q5, "{}\n")
    w(os.path.join(d, "q5_H0.json"), "{}\n")
    spec = write_spec(d, [dict(id="V1", combo=[25.2, 3, 1, 120, 2.0], q5=q5)], extra=dict(params=pf, addendum=af, params_r3=r3, w2_jobs=jobs, w2_k_ref=kref))
    env = dict(os.environ, C2_HOME=home, C2_STATARB=sa)

    def run(args):
        return subprocess.run([sys.executable, os.path.join(code, "conv_window.py")] + args, capture_output=True, text=True, env=env)
    r = run(["token-body", "--spec", spec, "--label", "GO 07.11, CEO"])
    assert r.returncode == 0, r.stderr[-500:]
    body = r.stdout
    lines = body.strip().split(chr(10))
    labels = [ln.split("  ", 1)[1] for ln in lines[:-1]]
    assert lines[-1] == "GO 07.11, CEO" and labels == sorted(labels), "строки по возрастанию пути, последняя - метка GO"
    for need in ("tools/compute/statarb/xdl9/conv/conv_window.py", "tools/compute/statarb/xdl9/conv/conv_guard.py", "tools/compute/statarb/portfolio.py",
                 "tools/compute/statarb/cell_status.py", "docs/research/P-38-params-2026-11-07.csv", "docs/research/P-38-params-r3-2026-11-07.csv",
                 "docs/research/P-38-params-calib-2026-11-07.csv", "docs/research/P-38-params-calib-extra.csv"):
        assert need in labels, need
    for frag in ("spec.json", "classpar_full.json", "q5_full.json", "q5_H0.json", "jobs_w2.csv", "pairs_all.csv"):
        assert any(l.endswith(frag) for l in labels), frag
    for f in os.listdir(code):
        if f.endswith(".py") or f.endswith(".sh"):
            assert "tools/compute/statarb/xdl9/conv/" + f in labels, "файл каталога кода вне токена: " + f
    tp = os.path.join(home, "go_window.token")
    w(tp, body)
    assert run(["token-check", "--spec", spec]).returncode == 0
    base_lines = body

    def refuse_case(label, mutate, restore):
        mutate()
        try:
            r1 = run(["token-check", "--spec", spec])
            r2 = run(["run", "--spec", spec, "--go-window", "--out", os.path.join(d, "out_never")])
            assert r1.returncode == 3 and r2.returncode == 3, (label, r1.returncode, r2.returncode, r2.stderr[-300:])
            assert os.path.exists(tp) and open(tp, encoding="utf-8").read() == base_lines, "токен забран/изменён при отказе: " + label
            assert not [f for f in os.listdir(home) if ".used." in f] and not os.path.exists(os.path.join(home, "window_reads.log")), label
            assert not os.path.exists(os.path.join(d, "out_never")), label
        finally:
            restore()
        assert run(["token-check", "--spec", spec]).returncode == 0, "после восстановления токен снова должен приниматься: " + label

    def edit(path, tail):
        old = open(path, "rb").read()
        return (lambda: w(path, old + tail, binary=True)), (lambda: w(path, old, binary=True))
    for fname in ("conv_lib.py", "conv_window.py", "conv_guard.py", "conv_agg.py", "test_conv.py"):
        m, rs = edit(os.path.join(code, fname), b"# tampered" + bytes([10]))
        refuse_case("изменён conv/" + fname, m, rs)
    extra_py = os.path.join(code, "extra_tool.py")
    refuse_case("лишний .py в каталоге кода", lambda: w(extra_py, "x = 1" + chr(10)), lambda: os.remove(extra_py))
    victim = os.path.join(code, "conv_sens.py")
    saved = open(victim, "rb").read()
    refuse_case("нет файла кода", lambda: os.remove(victim), lambda: w(victim, saved, binary=True))
    for path, lbl in ((os.path.join(d, "classpar_full.json"), "classpar"), (q5, "q5"), (os.path.join(d, "q5_H0.json"), "соседний q5"), (jobs, "jobs_w2.csv"), (kref, "эталон k"), (pf, "params G0.1"),
                      (af, "addendum"), (r3, "params r3"), (os.path.join(d, "P-38-params-calib-extra.csv"), "соседний addendum"), (spec, "spec"), (os.path.join(sa, "portfolio.py"), None), (os.path.join(sa, "cell_status.py"), None)):
        if lbl is None:
            continue                                                # файлы statarb - общие боевые: их не трогаем, покрыты API-тестом w02
        m, rs = edit(path, bytes([10]))
        refuse_case("изменён " + lbl, m, rs)
    # строка манифеста убрана / добавлена в самом токене
    short = chr(10).join(lines[:2] + lines[3:]) + chr(10)
    longer = chr(10).join(lines[:3] + ["0" * 64 + "  tools/compute/statarb/xdl9/conv/ghost.py"] + lines[3:]) + chr(10)
    for label, text in (("в токене нет строки", short), ("в токене лишняя строка", longer)):
        w(tp, text)
        r2 = run(["run", "--spec", spec, "--go-window", "--out", os.path.join(d, "out_never")])
        assert r2.returncode == 3 and open(tp, encoding="utf-8").read() == text and not os.path.exists(os.path.join(home, "window_reads.log")), label


def AG_ALPHA():
    import conv_agg as AG
    return AG.ALPHA


def main():
    names = sys.argv[1:]
    fails = 0
    sel = [f for f in OK if not names or f.__name__ in names or any(f.__name__.startswith(x) for x in names)]
    for fn in sel:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception:                                    # noqa: BLE001
            fails += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
    print("итог: %d из %d тестов не прошли" % (fails, len(sel)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
