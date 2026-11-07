#!/usr/bin/env python3
"""TK-493, П-38: тесты раннера окна и защиты (закрытие B1, m1, m2 ревизии 07.11): красные тесты защиты (CLI и API), токен v2 (манифест всего кода), одноразовость,
test-boundary, m1 (тестовый мир - предок ~/xdl9), git-токен по LF, старт окна «с нуля» в срезе W2, Холм, сверка раннера с conv_run на синтетике (G-C2-9w в миниатюре).
Дельта GW 07.11 (закрытие B1 ревизии: ресурсы, токен): w14 (токен возвращается при сбое до первого открытия файла окна и сгорает после), w15 (префлайт: код 75, токен не тронут),
w16 (блок «с Gate»: основной набор побайтно не зависит от блока; G-C2-9wg в миниатюре; сбой блока не теряет основной набор), w17 (окно «в процессе» на синтетике: отказ до чтения -> токен
возвращён -> повтор -> успех; сбой после чтения - токен сгорел), w18 (читатели помечают первое открытие файла окна), w19 (токен покрывает Gate-файлы, c2_git_ok, протокол), w20 (каталог выходов).
Запуск на Deck (в каталоге кода):  ~/xdl9/venv/bin/python test_conv_window.py [имя_теста ...]
Зависит от соседних модулей дерева (xdl9/asset_*.py, tools/xdl) через test_conv_gate: запускать из развёрнутого дерева `git archive` (tools/compute/statarb + tools/xdl).
Данные реальных панелей и окна не читаются (синтетика во временных каталогах; окно «в процессе» - на синтетическом мире, спец-режим window с подменой sha универсума и git-проверки)."""
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
import test_conv_gate as TG       # noqa: E402  (add_gate, write_manifest: синтетический Gate)

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
        # токен выдан заново (вторым решением CEO) - снова принимается один раз; сбой ПОСЛЕ первого открытия файла окна сжигает его (до открытия - возвращает: w14)
        e.token()
        G.check_days(2480)
        G.mark_window_read(2480, "panel X.sap")
        G.finish_window("failed")
        assert "event=first_window_read" in open(os.path.join(e.home, "window_reads.log"), encoding="utf-8").read()
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


def _variant_json(v, cp):
    return dict(id=v["id"], cls="crypto", combo=v["combo"], classpar=cp, q5=v.get("q5"), sigma_form=5.0, a3b=v.get("a3b"), formation_labels=v.get("formation_labels"))


def write_spec(d, variants, extra=None, gate=None, name="spec.json"):
    """spec для тестов; gate - словарь блока spec[gate] (manifest, pairs, w2_jobs, addendum) + variants (по умолчанию те же варианты)."""
    cp = os.path.join(d, "classpar_full.json")
    json.dump({"crypto": {"kstale": 30, "m_r": 5, "q_stale": 0.9}}, open(cp, "w"))
    spec = dict(name="тест", root=d, params=None, addendum=None, params_r3=None, w2_jobs=None, w2_k_ref=None, variants=[_variant_json(v, cp) for v in variants])
    spec.update(extra or {})
    if gate is not None:
        g = dict(gate)
        g["variants"] = [_variant_json(v, cp) for v in (g.pop("variants_src", None) or variants)]
        spec["gate"] = g
    p = os.path.join(d, name)
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


def log_events(home, skip=("run_start",)):
    """Последовательность значений event=... в window_reads.log (служебное run_start пропускается)."""
    p = os.path.join(home, "window_reads.log")
    if not os.path.isfile(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        ev = [x for x in ln.rstrip(chr(10)).split(chr(9)) if x.startswith("event=")]
        if ev and ev[0][6:] not in skip:
            out.append(ev[0][6:])
    return out


@test
def w14_token_restored_before_first_read_and_burned_after():
    """GW, B1: сбой раннера ДО первого открытия файла данных окна возвращает токен (event=aborted_before_read, журнал сбалансирован, повтор возможен);
    после первого открытия (mark_window_read) токен сгорает; успех токен удаляет; чужой файл на месте токена не затирается."""
    e = Env()
    try:
        tp = os.path.join(e.home, "go_window.token")
        e.cfg()
        e.token()
        good = open(tp, encoding="utf-8").read()
        for rnd in range(2):                                           # два сбоя подряд: токен каждый раз возвращается
            e.cfg()
            G.check_days(2479, what="старт")                           # захват
            assert not os.path.exists(tp) and G.log_unfinished_runs() == 1 and not G.window_was_read()
            assert G.finish_window("failed", "RuntimeError: тест") is True
            assert open(tp, encoding="utf-8").read() == good and not [f for f in os.listdir(e.home) if ".used." in f], "токен не возвращён"
            assert G.log_unfinished_runs() == 0
        ev = log_events(e.home)
        assert ev.count("aborted_before_read") == 2 and ev.count("token_claimed") == 2 and "run_end" not in ev and "first_window_read" not in ev, ev
        assert "reason=RuntimeError: тест" in open(os.path.join(e.home, "window_reads.log"), encoding="utf-8").read()
        # успех без чтения (outcome не failed) токен не возвращает
        e.cfg()
        G.check_days(2479)
        assert G.finish_window("done") is False and not os.path.exists(tp)
        # сбой ПОСЛЕ первого открытия файла окна: токен сгорает (повторные пометки в журнал не пишутся)
        e.token()
        e.cfg()
        G.check_days(2479)
        G.mark_window_read(2478, "формирование: не окно")             # чтение суток < 2479 не считается чтением окна
        assert not G.window_was_read()
        G.mark_window_read(2479, "turnover X.csv.gz")
        G.mark_window_read(2499, "panel Y.sap")
        assert G.window_was_read()
        assert G.finish_window("failed", "MemoryError") is False
        assert not os.path.exists(tp) and not [f for f in os.listdir(e.home) if ".used." in f]
        ev = log_events(e.home)
        assert ev.count("first_window_read") == 1 and ev[-1] == "run_end" and G.log_unfinished_runs() == 0, ev
        e.cfg()
        refused(G.check_days, 2479)                                    # токена нет
        # на месте токена уже лежит другой файл (CEO выдал новый): не затирается, прежний .used удалён
        e.token()
        e.cfg()
        G.check_days(2479)
        w(tp, "новый токен CEO" + chr(10))
        assert G.finish_window("failed") is False
        assert open(tp, encoding="utf-8").read() == "новый токен CEO" + chr(10) and not [f for f in os.listdir(e.home) if ".used." in f]
        assert log_events(e.home)[-1] == "aborted_before_read" and "token_restored=no" in open(os.path.join(e.home, "window_reads.log"), encoding="utf-8").read()
        assert G.log_unfinished_runs() == 0
    finally:
        e.clean()


def _preflight_dirs():
    d = tempfile.mkdtemp()
    return d, os.path.join(d, "c2")


@test
def w15_preflight_refuses_with_code_75_before_token_and_logs_snapshot():
    import conv_window as WN
    d, home = _preflight_dirs()
    os.environ["C2_HOME"] = home
    try:
        lp = os.path.join(home, "window_preflight.log")
        snap = WN.preflight(os.path.join(d, "out"), 1, 0.0, "window", log_path=lp)
        assert snap["ok"] and snap["disks"] and snap["threads"] == 1 and "free_gb" in snap["disks"][0]
        rec = json.loads(open(lp, encoding="utf-8").read().strip().split(chr(10))[-1])
        assert rec["ok"] is True and rec["mode"] == "window" and rec["min_free_gb"] == 0.0 and "mem_mb" in rec and "loadavg" in rec

        def refused75(fn, frag):
            try:
                fn()
            except SystemExit as ex:
                assert ex.code == 75 and frag in ex.msg, (ex.code, ex.msg)
                return
            raise AssertionError("отказа нет")
        refused75(lambda: WN.preflight(os.path.join(d, "out"), 1, 1e9, "window", log_path=lp), "свободно")
        rec = json.loads(open(lp, encoding="utf-8").read().strip().split(chr(10))[-1])
        assert rec["ok"] is False and rec["problems"], rec
        refused75(lambda: WN.preflight(os.path.join(d, "out"), 0, 0.0, "window", log_path=lp), "потоков")
        refused75(lambda: WN.preflight(os.path.join(d, "out"), (os.cpu_count() or 1) + 1, 0.0, "window", log_path=lp), "потоков")
        if WN._meminfo_mb().get("MemAvailable"):
            old = WN.MEM_PER_THREAD_MB
            WN.MEM_PER_THREAD_MB = 10 ** 9
            try:
                refused75(lambda: WN.preflight(os.path.join(d, "out"), 1, 0.0, "window", log_path=lp), "памяти")
            finally:
                WN.MEM_PER_THREAD_MB = old
        # несуществующий каталог выходов: проверяется ближайший существующий предок
        assert WN.preflight(os.path.join(d, "a", "b", "c"), 1, 0.0, "testb")["ok"]
    finally:
        os.environ.pop("C2_HOME", None)
        G.configure(mode="calib")


class WinEnv:
    """Окно «в процессе» на синтетическом мире (35 суток: формирование и «окно»): корень данных - синтетика, sha универсума и git-проверка params подменены (w07 проверяет git отдельно),
    токен выпускается тем же кодом, что `token-body`. Боевые данные не читаются."""

    def __init__(self, gate=True, n_assets=6, days=35):
        import conv_world as Wm
        self.W, self.tw = Wm, tempfile.mkdtemp()
        TC.write_world(self.tw, n_assets=n_assets, days=days)
        if gate:
            TG.add_gate(self.tw, n_assets=4, days=days)
        self.d = tempfile.mkdtemp()
        self.home = os.path.join(self.d, "c2")
        os.makedirs(self.home)
        w(os.path.join(self.home, "calib_start.json"), "{}")
        self.pf, self.af, self.r3 = (os.path.join(self.d, n) for n in ("P-38-params-2026-11-07.csv", "P-38-params-calib-2026-11-07.csv", "P-38-params-r3-2026-11-07.csv"))
        for p_ in (self.pf, self.af, self.r3):
            w(p_, "key,value\nk,%s\n" % os.path.basename(p_))
        V, self.cph, self.q5p = mk_variants(self.d, self.tw)
        gate_cfg = dict(manifest=os.path.join(self.tw, "gate_ready"), pairs=os.path.join(self.tw, "asset_pairs_gate.csv")) if gate else None
        self.spec = write_spec(self.d, V, extra=dict(root=self.tw, raw_root=os.path.join(self.d, "raw"), params=self.pf, addendum=self.af, params_r3=self.r3), gate=gate_cfg)
        self.out = os.path.join(self.d, "window")
        self.tp = os.path.join(self.home, "go_window.token")
        self._saved = (Wm.UNIVERSE_SHA_PREFIX, G.ensure_params_committed)
        Wm.UNIVERSE_SHA_PREFIX = G.sha256_file(os.path.join(self.tw, "asset_pairs.csv"))[:8]
        G.ensure_params_committed = lambda: None
        os.environ["C2_HOME"] = self.home

    def write_token(self):
        import conv_window as WN
        WN._cfg_for_token(type("A", (), dict(spec=self.spec))())
        w(self.tp, G.token_text("GO test, CEO"))
        G.configure(mode="calib")
        return open(self.tp, encoding="utf-8").read()

    def run(self, extra=()):
        import conv_window as WN
        return WN.main(["run", "--spec", self.spec, "--go-window", "--threads", "1", "--out", self.out, "--min-free-gb", "0"] + list(extra))

    def close(self):
        self.W.UNIVERSE_SHA_PREFIX, G.ensure_params_committed = self._saved
        os.environ.pop("C2_HOME", None)
        G.configure(mode="calib")


def mk_variants(d, tw):
    """V1 (Q5 включён, A3b окна, слабое формирование) и V2 - как в w09; q5 покрывает все пары мира (с Gate, если манифест валиден)."""
    pids = pids_of(tw)
    q5 = {p: (20.0 if i % 4 else None) for i, p in enumerate(pids)}
    q5p = os.path.join(d, "q5_full.json")
    json.dump(q5, open(q5p, "w"))
    cph = os.path.join(d, "classpar_H0.json")
    json.dump({"crypto": {"kstale": 25, "m_r": 4, "q_stale": 0.9}}, open(cph, "w"))
    a3b1 = {"h1on0": {"combo": [25.2, 3, 1, 120, 2.0], "classpar": cph}, "h0on1": {"combo": [40.0, 3, 1, 360, 2.0], "classpar": cph}}
    ok_fl = {k: {"ok": True, "text": "формирование: %s пройдено" % k} for k in ("a3b", "a3_cells", "a8")}
    weak_fl = dict(ok_fl, a3b={"ok": False, "text": "формирование: a3b не пройдено"})
    V = [dict(id="V1", combo=[25.2, 3, 1, 120, 2.0], q5=q5p, a3b=a3b1, formation_labels=weak_fl), dict(id="V2", combo=[40.0, 3, 3, 360, None], formation_labels=ok_fl)]
    return V, cph, q5p


MAIN_FILES = ("variants.json", "class_status.csv", "daily_y.csv", "slices.csv", "cells_table.csv", "a8.csv", "a3b_window.csv", "formation_labels.json", "trials_total.json", "summary_main.txt")


def read_bytes(p):
    return open(p, "rb").read()


def assert_main_equal(o1, o2, label):
    for fn in MAIN_FILES:
        assert read_bytes(os.path.join(o1, fn)) == read_bytes(os.path.join(o2, fn)), "%s: различается %s" % (label, fn)
    for vid in ("V1", "V2"):
        for root_, _, fs in os.walk(os.path.join(o1, "grid_" + vid)):
            for f in fs:
                if f == "meta.json":
                    continue                                           # время счёта
                p1 = os.path.join(root_, f)
                p2 = os.path.join(o2, os.path.relpath(p1, o1))
                assert read_bytes(p1) == read_bytes(p2), "%s: различается %s" % (label, os.path.relpath(p1, o1))


@test
def w16_gate_block_is_independent_and_equals_conv_run_with_gate():
    """GW: блок «с Gate» (spec[gate]) - описательные строки вне M_окно. Основной набор (без Gate) побайтно одинаков при наличии и при отсутствии блока; строки Gate равны
    `conv_run grid --with-gate` до бита (G-C2-9wg в миниатюре); пары с ногой Gate отдельно от пар 8 бирж; сводка: группа «описательные, вне M»; сбой блока не теряет основной набор."""
    tw = tempfile.mkdtemp()
    TC.write_world(tw, n_assets=8)
    TG.add_gate(tw, n_assets=6)
    d = tempfile.mkdtemp()
    V, cph, q5p = mk_variants(d, tw)
    gate_cfg = dict(manifest=os.path.join(tw, "gate_ready"), pairs=os.path.join(tw, "asset_pairs_gate.csv"))
    spec_n = write_spec(d, V, name="spec_nogate_only.json")
    spec_g = write_spec(d, V, gate=gate_cfg, name="spec_with_gate.json")
    D = 2468
    outs = {}
    for label, sp in (("N", spec_n), ("G", spec_g)):
        outs[label] = os.path.join(d, "out" + label)
        r = cli(["run", "--spec", sp, "--test-world", tw, "--test-boundary", str(D), "--out", outs[label], "--threads", "2"])
        assert r.returncode == 0, label + ": " + r.stderr[-1500:] + r.stdout[-500:]
    oN, oG = outs["N"], outs["G"]
    assert_main_equal(oG, oN, "основной набор с блоком Gate и без")
    assert read_bytes(os.path.join(oN, "summary.txt")) == read_bytes(os.path.join(oN, "summary_main.txt")), "без блока Gate summary.txt = summary_main.txt"
    sg = open(os.path.join(oG, "summary.txt"), encoding="utf-8").read()
    assert sg.startswith(open(os.path.join(oG, "summary_main.txt"), encoding="utf-8").read()) and "[с Gate, ОПИСАТЕЛЬНО" in sg and "описательных, вне M" in sg, sg[-600:]
    assert not os.path.exists(os.path.join(oN, "gate")) and not os.path.exists(os.path.join(oN, "trials_grand.json"))
    # выходы Gate: нет Холма и статусов; пары Gate / 8 бирж
    resG = json.load(open(os.path.join(oG, "gate", "variants.json"), encoding="utf-8"))
    resN = json.load(open(os.path.join(oG, "variants.json"), encoding="utf-8"))
    assert [r["id"] for r in resG] == ["V1", "V2"] and all("holm" not in r and "status" not in r and "split_gate" in r for r in resG)
    assert all("holm" in r and "status" in r and "split_gate" not in r for r in resN)
    for r in resG:
        sp_ = r["split_gate"]
        assert sp_["gate_leg"]["pairs"] == 18 and sp_["gate_leg"]["pairs"] + sp_["exch8"]["pairs"] == r["pairs"], sp_
        assert sp_["gate_leg"]["trades"] + sp_["exch8"]["trades"] == r["trades"]["all"]
        assert "gate" in r["a8"] and len(r["a8"]) == 4, list(r["a8"])        # aex, bex, cex, gate
    assert sum(r["split_gate"]["gate_leg"]["trades"] for r in resG) > 0, "в синтетике нет сделок с ногой Gate - сверка пуста"
    tg = json.load(open(os.path.join(oG, "gate", "trials_gate.json"), encoding="utf-8"))
    tot = json.load(open(os.path.join(oG, "trials_total.json"), encoding="utf-8"))
    grand = json.load(open(os.path.join(oG, "trials_grand.json"), encoding="utf-8"))
    assert grand["main"] == tot and grand["gate_descriptive"] == tg and grand["total_tests_all"] == tot["total_tests_on_window"] + tg["total_tests_gate"], grand
    assert tg["total_tests_gate"] == sum(g_["count"] for g_ in tg["groups"]) and tg["M_gate_variants"] == 2 and tot["M_window"] == 2, tg
    for fn in ("gate_lines.csv", "split_gate.csv", "daily_y.csv", "a8.csv", "a3b_window.csv", "summary_gate.txt", "trials_gate.json", "formation_labels_gate.json"):
        assert os.path.isfile(os.path.join(oG, "gate", fn)), fn
    # промежуточные массивы освобождены, итоговые таблицы и сделки остались
    meta = json.load(open(os.path.join(oG, "run_meta.json"), encoding="utf-8"))
    assert not os.path.isdir(os.path.join(oG, "work")) and not os.path.isdir(os.path.join(oG, "turn")) and meta["released_bytes"]["work"] > 0, meta["released_bytes"]
    assert os.path.isfile(os.path.join(oG, "gate", "grid_V1", "crypto", "trades.csv")) and isinstance(meta["gate_block"], dict) and meta["gate_block"]["pairs_gate_only"] == 18
    # G-C2-9wg: conv_run grid --with-gate на тех же сутках и числах
    cp = os.path.join(d, "classpar_full.json")
    base = os.path.join(d, "base_gate")
    bdirs = {}
    for v in V:
        cf = os.path.join(d, "combos_%s.json" % v["id"])
        json.dump([v["combo"]], open(cf, "w"))
        o = os.path.join(base, v["id"])
        common = ["--test-world", tw, "--out", o, "--last-day", "2478", "--threads", "2", "--first-sig-day", str(D), "--with-gate"]
        assert TC.run_cli(["series"] + common).returncode == 0
        r = TC.run_cli(["grid"] + common + ["--tag", "b", "--combos", cf, "--classpar", cp, "--trades-out"] + (["--q5", v["q5"]] if v.get("q5") else []))
        assert r.returncode == 0, r.stderr[-800:]
        bdirs[v["id"]] = os.path.join(o, "calib", "grid_b")
    r = cli(["gate9w", "--run", os.path.join(oG, "gate")] + sum([["--base", "%s=%s" % (k, v)] for k, v in sorted(bdirs.items())], []))
    assert r.returncode == 0 and "G-C2-9wg итог: PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
    rep = json.load(open(os.path.join(oG, "gate", "g_c2_9wg.json"), encoding="utf-8"))
    assert rep["ok"] and rep["gate"] == "G-C2-9wg" and all(v["ok"] for v in rep["variants"].values()) and rep["variants"]["V1"]["trades_n"] > 0
    # сбой блока Gate (нет файла classpar) не теряет основной набор: код 4, FAILED.txt, основные файлы те же
    sj = json.load(open(spec_g, encoding="utf-8"))
    sj["gate"]["variants"][0]["classpar"] = os.path.join(d, "нет_такого_classpar.json")
    spec_b = os.path.join(d, "spec_gate_broken.json")
    json.dump(sj, open(spec_b, "w"), ensure_ascii=False)
    oB = os.path.join(d, "outB")
    r = cli(["run", "--spec", spec_b, "--test-world", tw, "--test-boundary", str(D), "--out", oB, "--threads", "2"])
    assert r.returncode == 4, (r.returncode, r.stderr[-600:])
    assert os.path.isfile(os.path.join(oB, "gate", "FAILED.txt")) and "БЛОК НЕ ПОСЧИТАН" in open(os.path.join(oB, "summary.txt"), encoding="utf-8").read()
    assert_main_equal(oB, oN, "основной набор при сбое блока Gate")
    # release_intermediate: --keep-work оставляет, иначе work/ и turn/ уходят, grid_* остаётся
    import conv_window as WN
    t = tempfile.mkdtemp()
    for sub in ("work", "turn", "grid_V1"):
        os.makedirs(os.path.join(t, sub))
        w(os.path.join(t, sub, "x.bin"), "12345")
    assert WN.release_intermediate(t, True) == {"kept": True} and os.path.isdir(os.path.join(t, "work"))
    fr = WN.release_intermediate(t, False)
    assert fr == {"work": 5, "turn": 5} and not os.path.isdir(os.path.join(t, "work")) and os.path.isfile(os.path.join(t, "grid_V1", "x.bin"))
    print("   основной набор с блоком Gate и без - побайтно; G-C2-9wg в миниатюре PASS (сделок Gate V1 %d); сбой блока - код 4, основной набор цел" % rep["variants"]["V1"]["trades_n"])


def events_after(home, n0):
    return log_events(home)[n0:]


@test
def w17_window_in_process_restore_retry_success_and_burn():
    """GW, B1: режим окна «в процессе» на синтетике. (1) валидный токен, но Gate невалиден (дрейф вне токена: справочник gate.csv) - SystemExit ДО чтения: токен возвращён, журнал
    сбалансирован, каталог выходов пуст; (2) префлайт 75 - токен не тронут; (3) повтор с тем же токеном - полный прогон: основной набор + блок Gate, токен удалён, run_end done, массивы
    освобождены; (4) сбой ПОСЛЕ первого чтения (подмена stage_grid) - токен сгорел, run_end failed с причиной, повторный запуск отказан (код 3)."""
    import conv_window as WN
    we = WinEnv(gate=True)
    try:
        good = we.write_token()
        # (2) префлайт: токен не тронут
        try:
            we.run(["--min-free-gb", "1e9"])
            raise AssertionError("префлайт не отказал")
        except SystemExit as ex:
            assert ex.code == 75, ex.code
        assert open(we.tp, encoding="utf-8").read() == good and not os.path.exists(os.path.join(we.home, "window_reads.log")) and not os.path.exists(we.out)
        pre = json.loads(open(os.path.join(we.home, "window_preflight.log"), encoding="utf-8").read().strip().split(chr(10))[-1])
        assert pre["ok"] is False and pre["mode"] == "window"
        # (1) Gate невалиден после выпуска токена (справочник вне токена, sha256 в манифесте не совпадёт)
        gp = os.path.join(we.tw, "inst_gate", "gate.csv")
        orig = read_bytes(gp)
        w(gp, orig + b"G9_USDT,junk\n", binary=True)
        try:
            we.run()
            raise AssertionError("прогон с невалидным Gate не отказал")
        except SystemExit as ex:
            assert isinstance(ex.code, str) and "Gate невалиден" in ex.code, ex.code
        assert open(we.tp, encoding="utf-8").read() == good, "токен не возвращён"
        assert log_events(we.home) == ["token_claimed", "aborted_before_read"], log_events(we.home)
        assert G.log_unfinished_runs() == 0 and not [f for f in os.listdir(we.home) if ".used." in f]
        assert not os.path.isdir(we.out) or not os.listdir(we.out), "каталог выходов не очищен после возврата токена"
        # (3) исправлено - повтор тем же токеном: полный прогон
        w(gp, orig, binary=True)
        rc = we.run()
        assert rc == 0
        assert not os.path.exists(we.tp) and not [f for f in os.listdir(we.home) if ".used." in f], "токен не удалён после прогона"
        ev = log_events(we.home)
        assert ev == ["token_claimed", "aborted_before_read", "token_claimed", "first_window_read", "run_end"], ev
        assert "outcome=done" in open(os.path.join(we.home, "window_reads.log"), encoding="utf-8").read().strip().split(chr(10))[-1]
        assert G.log_unfinished_runs() == 0
        for fn in ("summary.txt", "summary_main.txt", "variants.json", "trials_total.json", "trials_grand.json", "class_status.csv", "SHA256.txt", "preflight.json", "run_meta.json"):
            assert os.path.isfile(os.path.join(we.out, fn)), fn
        assert os.path.isfile(os.path.join(we.out, "gate", "gate_lines.csv"))
        assert not os.path.isdir(os.path.join(we.out, "work")) and not os.path.isdir(os.path.join(we.out, "turn")) and os.path.isdir(os.path.join(we.out, "grid_V1"))
        meta = json.load(open(os.path.join(we.out, "run_meta.json"), encoding="utf-8"))
        assert meta["mode"] == "window" and meta["window_days"] == 21 and meta["d1"] == 2499 and meta["boundary"] == 2479 and meta["gate_block"]["pairs_gate_only"] == 12, meta["gate_block"]
        res = json.load(open(os.path.join(we.out, "variants.json"), encoding="utf-8"))
        assert [r["first_sig"] for r in res] == [2479, 2479] and [r["d1"] for r in res] == [2499, 2499] and all(r["status"]["label"] in WN.RANK for r in res)
        # повторный запуск: токена нет (код 3) - даже с новым каталогом выходов
        we.out = os.path.join(we.d, "window2")
        try:
            we.run()
            raise AssertionError("повторный запуск без токена не отказан")
        except SystemExit as ex:
            assert ex.code == 3, ex.code
        # (4) сбой после первого чтения сжигает токен: новый токен, подмена stage_grid
        n0 = len(log_events(we.home))
        we.write_token()
        we.out = os.path.join(we.d, "window3")
        orig_sg = WN.stage_grid

        def boom(*a, **k):
            raise RuntimeError("тестовый сбой после чтения")
        WN.stage_grid = boom
        try:
            we.run()
            raise AssertionError("сбой не проброшен")
        except RuntimeError as ex:
            assert "тестовый сбой" in str(ex)
        finally:
            WN.stage_grid = orig_sg
        assert not os.path.exists(we.tp) and not [f for f in os.listdir(we.home) if ".used." in f], "токен не сгорел после чтения"
        assert events_after(we.home, n0) == ["token_claimed", "first_window_read", "run_end"], events_after(we.home, n0)
        assert "reason=RuntimeError: тестовый сбой после чтения" in open(os.path.join(we.home, "window_reads.log"), encoding="utf-8").read()
        assert os.path.isfile(os.path.join(we.out, "preflight.json")), "каталог выходов после чтения не очищается"
        assert G.log_unfinished_runs() == 0
        we.out = os.path.join(we.d, "window4")
        try:
            we.run()
            raise AssertionError("после сгоревшего токена запуск не отказан")
        except SystemExit as ex:
            assert ex.code == 3, ex.code
    finally:
        we.close()


@test
def w18_readers_mark_first_window_read():
    """Читатели панелей, оборота и фандинга помечают первое открытие файла данных окна; чтения формирования, claim и отсутствующий файл фандинга - нет."""
    e = Env()
    try:
        e.cfg()
        w(os.path.join(e.home, "calib_start.json"), "{}")
        e.token()
        sap = os.path.join(e.d, "P.sap")
        TG.write_sap(sap, np.full(35 * 1440, 100.0), np.full(35 * 1440, 100.0))
        gz = os.path.join(e.d, "t.csv.gz")
        import gzip
        with gzip.open(gz, "wt", encoding="utf-8", newline="") as f:
            f.write("t_open_ms,o,h,l,c,v,quote_v\n")
        fund = os.path.join(e.d, "f.csv.gz")
        with gzip.open(fund, "wt", encoding="utf-8", newline="") as f:
            f.write("time_ms,rate\n")
        G.load_panel_range(sap, 2465, 2478, what="формирование")                     # claim не нужен, окно не читается
        assert not G.window_was_read() and not os.path.exists(os.path.join(e.home, "window_reads.log"))
        G.check_days(2499, what="claim")                                              # claim - ещё не чтение
        assert not G.window_was_read() and log_events(e.home) == ["token_claimed"]
        G.load_funding_range(os.path.join(e.d, "нет_такого.csv.gz"), 2465, 2499)      # нет файла - не чтение
        assert not G.window_was_read()
        assert G.finish_window("failed") is True and os.path.exists(os.path.join(e.home, "go_window.token"))      # сбой без чтения - токен возвращён
        for kind, fn in (("panel", lambda: G.load_panel_range(sap, 2465, 2499, what="w")), ("turnover", lambda: G.load_turnover_raw(gz, 2465, 2499)),
                         ("funding", lambda: G.load_funding_range(fund, 2465, 2499))):
            e.token()
            e.cfg()
            G.check_days(2499, what="claim")
            assert not G.window_was_read(), kind
            fn()
            assert G.window_was_read() and log_events(e.home)[-1] == "first_window_read", kind
            assert G.finish_window("failed") is False and not os.path.exists(os.path.join(e.home, "go_window.token")), kind
        # режимы не window (калибровка/тест) флаг не ставят
        e.cfg(mode="calib", go=False, runner=None)
        G.load_panel_range(sap, 2465, 2478, what="calib")
        assert not G.window_was_read()
    finally:
        e.clean()


@test
def w19_token_covers_gate_files_protocol_and_git_token():
    """Токен v2 покрывает файлы блока Gate (манифест gate_ready, таблица пар Gate и её .meta.json, jobs_w2 Gate, classpar/q5 набора с Gate и соседи), addendum набора окна («без Gate»),
    протокол и c2_git_ok каталога кода; любое изменение - отказ (код 3), токен не забирается."""
    import shutil
    d = tempfile.mkdtemp()
    code = os.path.join(d, "conv")
    shutil.copytree(HERE, code, ignore=shutil.ignore_patterns("__pycache__", "c2_git_ok", "*.pyc"))
    w(os.path.join(code, "c2_git_ok"), "params_sha256=aa\nhead=x\n")
    sa = G.statarb_dir()
    assert sa
    home = os.path.join(d, "c2")
    os.makedirs(home)
    gdir = os.path.join(d, "c2g_out", "calib")
    for fn, txt in (("classpar_full.json", "{}\n"), ("classpar_H0.json", "{}\n"), ("classpar_H1.json", "{}\n"), ("q5_full.json", "{}\n"), ("q5_detail_full.json", "{}\n")):
        w(os.path.join(gdir, fn), txt)
    ng = os.path.join(d, "ng_out", "calib")
    for fn in ("classpar_full.json", "q5_full.json"):
        w(os.path.join(ng, fn), "{}\n")
    files = {k: os.path.join(d, k) for k in ("gate_ready", "asset_pairs_gate.csv", "asset_pairs_gate.meta.json", "jobs_w2_gate.csv", "jobs_w2_nogate.csv", "pairs_all.csv", "P-38-convergence-xdl9.md",
                                              "P-38-params-2026-11-07.csv", "P-38-params-calib-gate-2026-11-07.csv", "P-38-params-calib-2026-11-07.csv", "P-38-params-r3-2026-11-07.csv")}
    for k, p_ in files.items():
        w(p_, "key,value\nk,%s\n" % k)
    cp_ng, q5_ng = os.path.join(ng, "classpar_full.json"), os.path.join(ng, "q5_full.json")
    cp_g, q5_g = os.path.join(gdir, "classpar_full.json"), os.path.join(gdir, "q5_full.json")
    spec_d = dict(name="тест", root=d, raw_root=d, params=files["P-38-params-2026-11-07.csv"], addendum=files["P-38-params-calib-gate-2026-11-07.csv"],
                  addendum_nogate=files["P-38-params-calib-2026-11-07.csv"], params_r3=files["P-38-params-r3-2026-11-07.csv"], protocol=files["P-38-convergence-xdl9.md"],
                  w2_jobs=files["jobs_w2_nogate.csv"], w2_k_ref=files["pairs_all.csv"],
                  variants=[dict(id="V1", cls="crypto", combo=[25.2, 3, 1, 120, 2.0], classpar=cp_ng, q5=q5_ng, sigma_form=5.0)],
                  gate=dict(addendum=files["P-38-params-calib-gate-2026-11-07.csv"], manifest=files["gate_ready"], pairs=files["asset_pairs_gate.csv"], w2_jobs=files["jobs_w2_gate.csv"],
                            variants=[dict(id="V1", cls="crypto", combo=[25.2, 3, 1, 120, 2.0], classpar=cp_g, q5=q5_g, sigma_form=5.0,
                                           a3b={"h1on0": dict(combo=[25.2, 3, 1, 120, 2.0], classpar=os.path.join(gdir, "classpar_H0.json")),
                                                "h0on1": dict(combo=[25.2, 3, 1, 120, 2.0], classpar=os.path.join(gdir, "classpar_H1.json"))})]))
    spec = os.path.join(d, "spec.json")
    json.dump(spec_d, open(spec, "w"), ensure_ascii=False)
    env = dict(os.environ, C2_HOME=home, C2_STATARB=sa)

    def run(args):
        return subprocess.run([sys.executable, os.path.join(code, "conv_window.py")] + args, capture_output=True, text=True, env=env)
    r = run(["token-body", "--spec", spec, "--label", "GO 07.11, CEO"])
    assert r.returncode == 0, r.stderr[-600:]
    body = r.stdout
    labels = [ln.split("  ", 1)[1] for ln in body.strip().split(chr(10))[:-1]]
    for frag in ("gate_ready", "asset_pairs_gate.csv", "asset_pairs_gate.meta.json", "jobs_w2_gate.csv", "jobs_w2_nogate.csv", "pairs_all.csv", "c2g_out/calib/classpar_full.json", "c2g_out/calib/classpar_H0.json",
                 "c2g_out/calib/classpar_H1.json", "c2g_out/calib/q5_full.json", "c2g_out/calib/q5_detail_full.json", "ng_out/calib/classpar_full.json", "ng_out/calib/q5_full.json",
                 "docs/research/P-38-params-calib-gate-2026-11-07.csv", "docs/research/P-38-params-calib-2026-11-07.csv", "docs/research/P-38-convergence-xdl9.md",
                 "tools/compute/statarb/xdl9/conv/c2_git_ok", "tools/compute/statarb/xdl9/conv/run_window.sh"):
        assert any(l.replace(chr(92), "/").endswith(frag) for l in labels), frag
    tp = os.path.join(home, "go_window.token")
    w(tp, body)
    assert run(["token-check", "--spec", spec]).returncode == 0

    def refuse_case(label, path):
        old = read_bytes(path)
        w(path, old + bytes([10]), binary=True)
        try:
            r1 = run(["token-check", "--spec", spec])
            r2 = run(["run", "--spec", spec, "--go-window", "--out", os.path.join(d, "out_never")])
            assert r1.returncode == 3 and r2.returncode == 3, (label, r1.returncode, r2.returncode, r2.stderr[-300:])
            assert open(tp, encoding="utf-8").read() == body and not [f for f in os.listdir(home) if ".used." in f], "токен забран/изменён: " + label
            assert not os.path.exists(os.path.join(d, "out_never")) and not os.path.exists(os.path.join(home, "window_reads.log")), label
        finally:
            w(path, old, binary=True)
        assert run(["token-check", "--spec", spec]).returncode == 0, "после восстановления токен снова принимается: " + label
    for lbl, path in (("манифест gate_ready", files["gate_ready"]), ("таблица пар Gate", files["asset_pairs_gate.csv"]), ("meta таблицы пар Gate", files["asset_pairs_gate.meta.json"]),
                      ("jobs_w2 Gate", files["jobs_w2_gate.csv"]), ("jobs_w2 основного набора", files["jobs_w2_nogate.csv"]), ("classpar набора с Gate", cp_g), ("q5 набора с Gate", q5_g),
                      ("соседний classpar_H1 набора с Gate", os.path.join(gdir, "classpar_H1.json")), ("соседний q5_detail набора с Gate", os.path.join(gdir, "q5_detail_full.json")),
                      ("classpar основного набора", cp_ng), ("addendum основного набора", files["P-38-params-calib-2026-11-07.csv"]),
                      ("addendum (слот git-токена)", files["P-38-params-calib-gate-2026-11-07.csv"]), ("протокол", files["P-38-convergence-xdl9.md"]), ("c2_git_ok", os.path.join(code, "c2_git_ok")),
                      ("run_window.sh", os.path.join(code, "run_window.sh"))):
        refuse_case(lbl, path)


@test
def w20_out_dir_allows_only_preexisting_token_draft():
    import conv_window as WN
    d = tempfile.mkdtemp()
    WN.check_out_dir(os.path.join(d, "нет"))                            # не существует - можно
    o = os.path.join(d, "window")
    os.makedirs(o)
    WN.check_out_dir(o)                                                 # пуст - можно
    w(os.path.join(o, "token_body.txt.pre"), "x")
    w(os.path.join(o, "token_body.txt.pre.sha256"), "y")
    WN.check_out_dir(o)                                                 # заготовка токена - можно
    w(os.path.join(o, "summary.txt"), "z")
    refused(WN.check_out_dir, o)                                        # любой другой файл - отказ
    WN.clean_out_dir(o)                                                 # очистка после возврата токена оставляет только заготовку
    assert sorted(os.listdir(o)) == ["token_body.txt.pre", "token_body.txt.pre.sha256"]
    os.makedirs(os.path.join(o, "work"))
    w(os.path.join(o, "work", "a"), "1")
    WN.clean_out_dir(o)
    assert sorted(os.listdir(o)) == ["token_body.txt.pre", "token_body.txt.pre.sha256"]


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
