#!/usr/bin/env python3
"""Переводчик для табло: человеческие строки из тикетов и хода работ — через Haiku, ТОЛЬКО при изменении входа.

Работает потоком внутри сборщика (`collect.py`, `Translator`): сборщик раз в 5 с спрашивает `get(вид, метка, вход)`;
ответ есть в кэше (`.claude/pulse/plain-auto.json`, ключ — хэш входа) — отдаётся сразу, модель не зовётся; нет —
вход ставится в очередь, фоновый поток делает ОДИН вызов `claude -p` (без инструментов, без хуков, без CLAUDE.md,
мысли выключены — дёшево) и кладёт ответ в кэш. Нет изменения входа — нет вызова; таймеров нет.
Сбой вызова (нет CLI, сеть, мусор вместо JSON) — тихо: сборщик остаётся на прежнем поведении (заголовок тикета,
первая фраза записи); повтор с паузой 1 → 5 → 30 мин, не больше 3 раз на один вход, до 60 вызовов в час.
Лог: `.claude/pulse/plainify.log` — строка на вызов (время, вид, метка, цена из ответа CLI, длительность, токены).

    python tools/pulse/plainify.py --demo    # перевести текущие тикеты и последние записи, показать строки
    python tools/pulse/plainify.py --stats   # сумма цены и среднее время по plainify.log

Виды: title (название задачи), entry (запись лога → news / question / next), step (шаг прогресса → step / next / unit),
proc (процесс без задачи → what), psum (строка-итог процесса табло → summary; хэш считается по названию и шагам с
состояниями, БЕЗ последней записи тикета — модель зовётся только при смене состояния шагов). Ручное переопределение — `.claude/pulse/plain.json` (главнее), слияние — в collect.py.
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "dispatcher"))
PULSE_DIR = ROOT / ".claude" / "pulse"
CACHE = PULSE_DIR / "plain-auto.json"
LOG = PULSE_DIR / "plainify.log"
TZ = timezone(timedelta(hours=4))  # GMT+4

MODEL = os.environ.get("ALPHA_PULSE_MODEL", "claude-haiku-4-5-20251001")
PROMPT_V = 4            # смена текста промптов — меняет хэши и пересчитывает кэш
CALL_TIMEOUT_S = 90
MAX_PER_HOUR = 60
MAX_ATTEMPTS = 3
RETRY_S = (60, 300, 1800)
CACHE_MAX = 600
ENTRY_MAX = 3000         # знаков записи лога на вход (голова + хвост)

SYSTEM = (
    "Ты пишешь строки для табло владельца бизнеса. Владелец не технарь. Правила: "
    "никаких номеров задач (TK-…), номеров решений (В-…), имён файлов и папок, процессов, флагов, команд, версий, "
    "сокращений и английских терминов — вместо них говори, что это значит по-человечески; "
    "коротко, обычными русскими словами, по делу: что сделано или что происходит и что это даёт; "
    "не выдумывай и не добавляй деталей, которых нет во входном тексте, числа из входа сохраняй; "
    "латинских слов и идентификаторов (gate_ab, ob200) не используй — опиши словами или опусти; "
    "ответ — ТОЛЬКО один JSON-объект с нужными полями, без пояснений и без markdown-оград; "
    "лимиты длины в знаках — жёсткие."
)
ROLE_RU = {"engineer": "Инженер", "researcher": "Исследователь", "judge": "Судья", "ceo": "CEO"}

# поле → предел знаков (жёсткая обрезка по границе фразы/слова). В промптах лимиты в СЛОВАХ: считать знаки модель не умеет.
LIMITS = {
    "title": {"title": 45},
    "entry": {"news": 90, "question": 140, "next": 70},
    "step": {"step": 50, "next": 50, "unit": 20},
    "proc": {"what": 60},
    "psum": {"summary": 90},
}
KEY_SKIP = {"psum": ("entry",)}  # поля входа, которые идут в промпт, но не в хэш кэша (не их смена — повод звать модель)


# --- промпты ------------------------------------------------------------------------------------------------------------
def mid_clip(text: str, n: int = ENTRY_MAX) -> str:
    """Длинная запись: голова и хвост (итог и «прошу решение» обычно в конце)."""
    t = (text or "").strip()
    if len(t) <= n:
        return t
    head = int(n * 0.62)
    return t[:head].rstrip() + "\n[…]\n" + t[-(n - head):].lstrip()


def build_prompt(kind: str, p: dict) -> str:
    g = lambda k: str(p.get(k) or "").strip() or "—"  # noqa: E731
    if kind == "title":
        return (f"Задача для табло. Заголовок: {g('title')}\nНачало описания (только для понимания): {g('start')}\n\n"
                'Верни JSON {"title": "…"}: название задачи — человеческий перефраз ЗАГОЛОВКА, не более 5 слов, без точки; '
                "деталей, которых нет в заголовке, не добавляй.\n"
                "Примеры: «Добор и проверка данных пула»; «Умная проверка данных по минутам и часам»; "
                "«Сверка полноты данных».")
    if kind == "entry":
        return (f"Запись в журнале задачи «{g('ticket')}». Автор записи: {g('author')}.\nТекст записи:\n<<<\n"
                f"{mid_clip(p.get('text'))}\n>>>\n\n"
                "Верни JSON с тремя полями; null — если нечего сказать.\n"
                '"news" — что произошло или стало известно, для ленты «что было»: не более 12 слов, одно короткое '
                "предложение-факт на языке владельца — результат или решение, а не процесс; без буквенных обозначений "
                "пунктов и названий проверок. Если запись служебная и владельцу неинтересна (ход работы без результата, "
                "споры и согласования исполнителей, перезапуски) или ты не уверен, что понял суть — null: пропуск "
                "лучше неверной строки.\n"
                "Примеры: «Докачаны 93 недостающих суток (4 — нет у самой Bybit)»; «Причина расхождений найдена: Bybit "
                "пишет стакан раз в 100 мс, данные целы»; «Полнота: данные есть по 25 007 из 25 107 монето-суток».\n"
                '"question" — вопрос ВЛАДЕЛЬЦУ, не более 18 слов, ТОЛЬКО если в записи явно просят решения именно у '
                "владельца (не у Судьи, не у CEO, не у инженера). Суть выбора простыми словами: что выбираем и чем "
                "грозит каждый вариант; без обозначений («пункт в», «признак», «тест 3»). Иначе null.\n"
                "Пример: «Остановить 2 старые задачи на сервере: заливку архива (висит с 02.10) и пересылку "
                "результатов (с 28.09)?»\n"
                '"next" — что делается дальше, не более 8 слов, по-человечески; null, если в записи не сказано.\n'
                "Пример: «Сверка: все ли данные на месте (~5 мин)».")
    if kind == "step":
        return (f"Ход выполнения задачи «{g('ticket')}» (строки из служебного файла прогресса).\n"
                f"Шаг: {g('step')}\nДальше: {g('next')}\nЕдиница счёта: {g('unit')}\n\n"
                "Верни JSON:\n"
                '"step" — что сейчас происходит, не более 6 слов, по-человечески (уже понятные слова вроде «готово» оставь как есть). '
                "Пример: «Проверяем старые данные внутри часа».\n"
                '"next" — что дальше, не более 7 слов, null если «—».\n'
                '"unit" — единица счёта в родительном падеже множественного числа для фразы «N из M …» '
                "(«монето-месяцев», «суток», «файлов»), до 20 знаков, null если «—».")
    if kind == "proc":
        return (f"Процесс на сервере, к которому нет задачи. Имя: {g('name')}. Командная строка: {g('args')}.\n\n"
                'Верни JSON {"what": "…"}: что это за процесс по-человечески, не более 7 слов, строчными буквами, без '
                "точки. Не гадай: если по имени и командной строке смысл неясен — «неопознанный фоновый процесс».\n"
                "Пример: «заливка архива на хранилище».")
    if kind == "psum":
        return (f"Процесс на табло владельца: «{g('title')}».\nШаги процесса и их состояние:\n{g('steps')}\n"
                f"Последняя запись исполнителя (только чтобы понять смысл; сверх шагов ничего не выдумывай):\n<<<\n"
                f"{mid_clip(p.get('entry'), 700)}\n>>>\n\n"
                'Верни JSON {"summary": "…"}: одна короткая фраза — что стало возможно или что получится, когда процесс '
                "закончится, и на каком он этапе; не более 12 слов, обычными словами, как для владельца бизнеса. Без "
                "номеров, без жаргона, без счётчиков вроде «3 из 8» (их допишут отдельно), без оценок близости к концу "
                "(«почти готово», «скоро»), без точки в конце.\n"
                "Примеры: «Данные пула собраны, идёт проверка на порчу»; «Бот принимает заявки, осталось проверить "
                "судье»; «Отчёт готов, ждёт вашего решения».")
    raise ValueError(kind)


def key_of(kind: str, prompt: str) -> str:
    return hashlib.sha1(f"{PROMPT_V}\n{MODEL}\n{kind}\n{prompt}".encode("utf-8")).hexdigest()[:16]


# --- разбор ответа ------------------------------------------------------------------------------------------------------
_ID_PAREN = re.compile(r"\s*\((?:TK|ТК|В|B|T|Т)-?\s?\d+[^)]*\)", re.I)
_ID_BARE = re.compile(r"\b(?:TK|ТК|В)-\d+\b", re.I)
_IDENT = re.compile(r"\b\w*_\w*\b|\b[A-Za-z]+\d+[A-Za-z\d]*\b")  # gate_ab, ob200, v171b
_TAIL_WORDS = {"и", "в", "во", "на", "по", "с", "со", "к", "ко", "а", "но", "для", "от", "из", "за", "до", "о", "об",
               "при", "что", "или", "как", "не", "же", "бы", "у", "над", "под", "про", "без"}


def clip_word(s: str, n: int) -> str:
    """По границе фразы (точка с запятой, тире, запятая), иначе по слову без висящего предлога; хвост — «…»."""
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    for sep in ("; ", " — ", ", ", ": "):
        i = cut.rfind(sep)
        if i >= n * 0.55:
            return cut[:i].rstrip(" ,.;:—-")
    if cut.count("(") > cut.count(")"):  # не оставляем открытую скобку
        cut = cut[: cut.rfind("(")].rstrip()
    sp = cut.rfind(" ")
    words = (cut[:sp] if sp >= n * 0.5 else cut).rstrip(" ,.;:—-").split(" ")
    while len(words) > 2 and words[-1].lower() in _TAIL_WORDS:
        words.pop()
    return " ".join(words).rstrip(" ,.;:—-") + "…"


def clean_str(v, n: int):
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = _IDENT.sub("", _ID_BARE.sub("", _ID_PAREN.sub("", str(v))))
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip(" \t\"'«»")
    if not s or s.lower() in ("null", "none", "нет", "—", "-"):
        return None
    return clip_word(s, n)


def extract_json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def looks_plain(s: str) -> bool:
    """Строка уже по-человечески (кириллица, цифры, знаки, до 45 знаков) — оставляем как есть, модели не доверяем."""
    return bool(s) and len(s) <= 45 and re.fullmatch(r"[А-Яа-яЁё0-9 ,.:;()%/+–—-]+", s) is not None


def norm(kind: str, obj: dict):
    """Поля вида → строки в пределах лимитов или None. Нужного поля нет совсем — ответ негоден (None)."""
    lim = LIMITS[kind]
    if not any(k in obj for k in lim):
        return None
    out = {k: clean_str(obj.get(k), n) for k, n in lim.items()}
    for k in out:  # строка табло с большой буквы (кроме единицы счёта и «что это за процесс» — они встают в середину фразы)
        if out[k] and k not in ("unit", "what"):
            out[k] = out[k][0].upper() + out[k][1:]
    if kind == "title" and not out["title"]:
        return None
    if kind == "proc" and not out["what"]:
        return None
    if kind == "psum" and not out["summary"]:
        return None
    return out


# --- вызов claude -------------------------------------------------------------------------------------------------------
def find_claude() -> str:
    for c in (os.environ.get("CLAUDE_BIN"), shutil.which("claude"),
              str(Path.home() / ".local" / "bin" / "claude.exe"), str(Path.home() / ".local" / "bin" / "claude")):
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    return "claude"


def _env() -> dict:
    env = dict(os.environ)
    for k in list(env):  # как у диспетчера: дочерний claude не должен принять себя за сессию CEO
        if "HOST_SESSION" in k.upper():
            env.pop(k, None)
    env["MAX_THINKING_TOKENS"] = "0"  # без «мыслей»: вызов вдвое дешевле, для таких строк качество то же
    return env


class Translator:
    def __init__(self, cache_path: Path = CACHE, log_path: Path = LOG):
        self.cache_path, self.log_path = Path(cache_path), Path(log_path)
        self.bin = find_claude()
        self.cwd = Path(tempfile.gettempdir()) / "alpha-plainify"  # вне проекта: CLAUDE.md проекта не подтягивается
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.cv = threading.Condition()
        self.heap: list = []
        self.seq = itertools.count()
        self.queued: set = set()   # хэши в очереди или в работе
        self.fails: dict = {}      # хэш → (число сбоев, не раньше какого времени)
        self.calls: deque = deque()
        self.cache: dict = {}
        try:
            d = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), dict):
                self.cache = d["items"]
        except (OSError, ValueError):
            pass
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="plainify")
            self.thread.start()
        return self

    def pending(self) -> int:
        with self.cv:
            return len(self.queued)

    def get(self, kind: str, label: str, payload: dict, prio: int = 1):
        """Ответ из кэша или None (тогда вход поставлен в очередь — ответ появится на одном из следующих тиков)."""
        prompt = build_prompt(kind, payload)
        skip = KEY_SKIP.get(kind)
        h = key_of(kind, build_prompt(kind, {**payload, **{k: "" for k in skip}}) if skip else prompt)
        with self.cv:
            it = self.cache.get(h)
            if it is not None:
                return it["out"]
            if h in self.queued:
                return None
            f = self.fails.get(h)
            if f and (f[0] >= MAX_ATTEMPTS or time.time() < f[1]):
                return None
            self.queued.add(h)
            heapq.heappush(self.heap, (prio, next(self.seq), h, kind, label, prompt))
            self.cv.notify()
        return None

    # --- фоновый поток
    def _loop(self):
        while True:
            with self.cv:
                while not self.heap:
                    self.cv.wait()
                _, _, h, kind, label, prompt = heapq.heappop(self.heap)
            try:
                self._throttle()
                out, meta = self._call(kind, prompt)
            except Exception as e:  # поток не умирает
                out, meta = None, {"err": f"{type(e).__name__}: {e}"}
            self._log(kind, label, out, meta)
            with self.cv:
                if out is not None:
                    self.cache[h] = {"kind": kind, "label": label, "out": out,
                                     "at": datetime.now(TZ).isoformat(timespec="seconds")}
                    self.fails.pop(h, None)
                    self._save_locked()
                else:
                    n = self.fails.get(h, (0, 0))[0] + 1
                    self.fails[h] = (n, time.time() + RETRY_S[min(n, len(RETRY_S)) - 1])
                self.queued.discard(h)

    def _throttle(self):
        while True:
            now = time.time()
            while self.calls and now - self.calls[0] > 3600:
                self.calls.popleft()
            if len(self.calls) < MAX_PER_HOUR:
                self.calls.append(now)
                return
            time.sleep(30)

    def _call(self, kind: str, prompt: str):
        cmd = [self.bin, "-p", "--model", MODEL, "--output-format", "json", "--tools", "", "--system-prompt", SYSTEM,
               "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
        t0 = time.time()
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=CALL_TIMEOUT_S, cwd=str(self.cwd), env=_env(),
                           creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        meta = {"dur": time.time() - t0}
        try:
            d = json.loads(r.stdout)
        except ValueError:
            meta["err"] = f"не JSON, код {r.returncode}: {(r.stderr or r.stdout)[:160].strip()}"
            return None, meta
        u = d.get("usage") or {}
        meta.update(cost=d.get("total_cost_usd"),
                    tin=(u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                    + (u.get("cache_creation_input_tokens") or 0), tout=u.get("output_tokens"))
        if d.get("is_error") or r.returncode != 0:
            meta["err"] = f"ошибка CLI: {str(d.get('result'))[:160]}"
            return None, meta
        obj = extract_json(str(d.get("result") or ""))
        out = norm(kind, obj) if obj else None
        if out is None:
            meta["err"] = f"негодный ответ: {str(d.get('result'))[:160]!r}"
        return out, meta

    def _log(self, kind, label, out, meta):
        try:
            tail = json.dumps(out, ensure_ascii=False) if out is not None else "ОШИБКА " + str(meta.get("err"))
            line = (f"{datetime.now(TZ).isoformat(timespec='seconds')} {kind} {label} ok={int(out is not None)} "
                    f"cost={float(meta.get('cost') or 0):.6f} dur={meta.get('dur', 0):.1f}s in={meta.get('tin')} "
                    f"out={meta.get('tout')} {tail}")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line.replace("\n", " ") + "\n")
        except OSError:
            pass

    def _save_locked(self):
        if len(self.cache) > CACHE_MAX:
            keep = sorted(self.cache.items(), key=lambda kv: kv[1].get("at", ""))[-CACHE_MAX:]
            self.cache = dict(keep)
        try:
            from ticket import atomic_write_text
            atomic_write_text(self.cache_path, json.dumps({"v": 1, "model": MODEL, "items": self.cache},
                                                          ensure_ascii=False, indent=1))
        except Exception:
            pass


# --- командная строка ---------------------------------------------------------------------------------------------------
def stats(since: str = "") -> int:
    n = ok = 0
    cost = dur = 0.0
    try:
        for ln in LOG.read_text(encoding="utf-8").splitlines():
            m = re.search(r" ok=(\d) cost=([\d.]+) dur=([\d.]+)s", ln)
            if m and ln >= since:
                n += 1
                ok += int(m.group(1))
                cost += float(m.group(2))
                dur += float(m.group(3))
    except OSError:
        pass
    print(f"вызовов {n} (удачных {ok}), цена {cost:.4f} $, средняя цена {cost / n if n else 0:.5f} $, "
          f"среднее время {dur / n if n else 0:.1f} с")
    return 0


def demo() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import collect as C
    tr = C.AUTO = Translator().start()
    st = C.read_status() or {}
    tickets = C.load_tickets()
    jobs_all = {r["id"]: r["jobs"] for r in st.get("tickets", []) if r.get("jobs")}
    machines = st.get("machines", [])
    t0 = time.time()
    C.auto_plain(tickets, {}, machines, jobs_all)
    while tr.pending() and time.time() - t0 < 600:
        time.sleep(1)
    auto = C.auto_plain(tickets, {}, machines, jobs_all)
    print(f"перевод занял {time.time() - t0:.0f} с")
    print(json.dumps(auto, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = sys.argv[1:]
    if "--stats" in a:
        raise SystemExit(stats(a[a.index("--stats") + 1] if len(a) > a.index("--stats") + 1 else ""))
    if "--demo" in a:
        raise SystemExit(demo())
    print(__doc__)
