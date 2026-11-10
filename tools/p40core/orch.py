#!/usr/bin/env python3
"""TK-500: оркестратор по (актив, сутки) для ядра П-40 (сервер 5.231.25.224, Linux). Один проход сырья бокса -> таблицы эпизодов; `.bin` и сырьё живут только в /dev/shm и удаляются после суток.
Запуск: orch.py <pairs.csv> <out> --days 2026-09-17,2026-09-18 [--jobs 2] [--fetch-p 10] [--shm /dev/shm/p40o] [--assets 1,2] [--chunk K] [--n-usdt 1000]
pairs.csv: asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB   (по строке на пару; ноги с одним (биржа,символ) в актив-сутки читаются и конвертируются ОДИН раз;
  mult — множитель размера Gate (quanto_multiplier), step — шаг количества очереди, пусто = 1 и 1e-9; биржи: binance okx bitget aster hyperliquid bybit gate;
  lot пусто/auto -> lot = N/(mid_open*ct): N = --n-usdt (умолч. 1000, П-40 §8), mid_open = середина первого снимка книги ноги в сутках, ct = монет в единице количества книги (колонки ctA/ctB в конце, пусто = 1; OKX = ctVal))
Выход: <out>/<asset>/<day>/<pair>{,.fills.csv,.exits.csv,.features.csv,.path.csv,.aux.csv}, <out>/specs.txt (строки для assemble.py), <out>/orch.csv (замеры по актив-суткам), метка DONE (повтор пропускает).
TK-510: бюджет /dev/shm. Пик актива×чанка = все .bin чанка (они в 2–37 раз больше сырья: 0,07–0,38 ГБ на ногу×сутки) + сырьё одних суток. Задание берёт «пилот» (PILOT_LEG_B на ногу), считает первые сутки,
  меряет du(wd) и бронирует оценку пика ((оставшееся + .bin K суток) × SHM_MARGIN); сумма броней ≤ SHM_FRAC (0,8) от размера shm минус чужое на старте, иначе ждёт; оценка одного задания > бюджета -> отказ сразу.
  Сбой ENOSPC (rsync rc 11, pq2bin/ядро с «No space left») — отдельное исключение ShmFull, строка «СБОЙ ENOSPC», код выхода 28 (умолч. сбой = 1); mark/OI-дыры поставщика (aux_feats) копятся в <out>/auxholes.csv.
Флаги ядра P40_* (NOTRADE, THETAS, VARIANTS, TRUNC, K2, SHIFT_B_MS ...) передаются средой как есть. Binance-стакан читается с локального диска (BINANCE_BOOK), остальное — rsync --files-from с бокса в P постоянных соединениях.
TK-518 (В-116): ноги Bybit/Gate — НЕ сырьё (его на боксе убирает reaper), а L1 бокса `p40l1/<биржа>_futures/<символ>/<сутки>/` (`<ЧЧ>.l1.bin.zst` + `.ok`), распаковка zstd -> `<ЧЧ>.l1.bin`; нет суток/метки/файлов по метке = сбой задания.
  Нужен P40_L1=1 на весь прогон (признаки без глубины, В-115) и ядро TK-515+ (P40_CORE; старое ядро с P40_L1 — отказ при старте). `--raw-legs` — прежний путь из сырья. Хвост суток после 00:00:00 не берётся: прежний конвертер (bybit::scan) его тоже отбрасывал — разбор в тикете TK-518."""
import argparse, calendar, concurrent.futures as cf, csv, gzip, os, shutil, subprocess, sys, tempfile, threading, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CHFT = {"binance", "okx", "bitget", "aster", "hyperliquid"}
KINDS = ["orderbook", "trades", "mark_price", "open_interest", "liquidations"]
BINANCE_BOOK = os.environ.get("BINANCE_BOOK", "/data/stakan/chft/binance_futures")
CORE = os.environ.get("P40_CORE", "/root/p40core/rs/target/release/p40core")
PQ2BIN = os.environ.get("P40_PQ2BIN", "/root/p40core/pq2bin/target/release/pq2bin")
SSH = "ssh -c aes128-gcm@openssh.com -o Compression=no"
L1_SRC = os.environ.get("P40_L1_SRC", "box:alpha/stakan")   # TK-522: откуда брать p40l1/...; для гейта до выкладки L1 на бокс — локальный /data (L1 раздела srv)
LOG_LOCK = threading.Lock()
EXIT_SHM = 28                  # errno ENOSPC: хотя бы одно задание упало или отказано по месту в /dev/shm
SHM_FRAC = float(os.environ.get("P40_SHM_FRAC", "0.8"))  # доля shm под брони (D1c: 0.3 — shm это память, OOM 10.11)
SHM_MARGIN = 1.25              # запас оценки пика на разброс суток (оценка и факт пишутся в orch.log: «shm: …»)
PILOT_LEG_B = 600 * 1024 ** 2  # бронь на ногу в первые сутки: замер 09.11 (AAVE 17.09) — .bin+сырьё ≤ 0,43 ГБ на ногу×сутки
SEEN_AUXH = set()
SEEN_GAPS = set()
SEEN_VAL = set()
L1_EX = {ex: f"{ex}_futures" for ex in sorted(CHFT | {"bybit", "gate"})}   # TK-518/TK-522 (В-116): вход ВСЕХ ног = L1 бокса alpha/stakan/p40l1/<биржа>_futures/<символ>/<сутки>/ ({ЧЧ}.l1.bin.zst + {ЧЧ}.tr.zst сделки CHFT + .ok)


class L1Missing(RuntimeError):
    """L1-суток ноги на боксе нет (нет каталога/метки, у CHFT нет сделок); с --l1-fallback нога CHFT идёт из сырья (TK-522), иначе сбой задания"""


class ShmFull(RuntimeError):
    """/dev/shm не хватило (факт ENOSPC или оценка пика больше бюджета) — не «обычный» сбой"""


class ShmBudget:
    """брони /dev/shm: сумма ≤ cap = frac·размер − чужое на старте; set() ждёт, пока влезет; оценка > cap -> ShmFull сразу.
    Без взаимной блокировки: если ни у кого другого нет полной брони (все в пилоте), задание идёт, пока оно само ≤ cap."""

    def __init__(self, shm, frac=SHM_FRAC, total=None, foreign=None):
        os.makedirs(shm, exist_ok=True)
        st = os.statvfs(shm)
        total = st.f_blocks * st.f_frsize if total is None else total
        foreign = total - st.f_bavail * st.f_frsize if foreign is None else foreign
        self.cap = int(total * frac) - foreign
        self.res, self.full, self.cv = {}, set(), threading.Condition()

    def set(self, key, need, full=False, wait=60.0):
        if need > self.cap:
            raise ShmFull(f"оценка пика {need / 2**30:.1f} ГБ > бюджета shm {self.cap / 2**30:.1f} ГБ ({SHM_FRAC:.0%} размера минус чужое): уменьшите --chunk")
        with self.cv:
            while sum(v for k, v in self.res.items() if k != key) + need > self.cap and (self.full - {key}):
                self.cv.wait(wait)
            self.res[key] = need
            if full:
                self.full.add(key)
            self.cv.notify_all()

    def release(self, key):
        with self.cv:
            self.res.pop(key, None)
            self.full.discard(key)
            self.cv.notify_all()


def peak_estimate(peak1, kept1, k_days):
    """peak1 = du(wd) первых суток до drop_heavy, kept1 = после (≈ .bin суток + малое сырьё); пик K суток = K·kept1 + тяжёлое сырьё одних суток"""
    return int((k_days * kept1 + (peak1 - kept1)) * SHM_MARGIN)


def du(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs if os.path.exists(os.path.join(d, f)))


def nospace(log, shm=None):
    """упал ли шаг из-за места: «No space left»/StorageFull в хвосте лога или shm почти полон"""
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            tail = f.read().decode("utf-8", "replace")
        if "No space left" in tail or "StorageFull" in tail:
            return True
    except OSError:
        pass
    if shm:
        st = os.statvfs(shm)
        return st.f_bavail * st.f_frsize < 64 * 1024 ** 2
    return False


def run(cmd, env=None, log=None):
    """cmd -> (rc, wall_s, cpu_s); cpu = user+sys самого процесса и его детей (wait4), не всей программы"""
    t = time.time()
    f = open(log, "ab") if log else subprocess.DEVNULL
    p = subprocess.Popen(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    _, st, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(st)
    if log:
        f.close()
    return p.returncode, time.time() - t, ru.ru_utime + ru.ru_stime


def leg_key(ex, sym):
    return f"{ex}_{sym}"


def box_files(ex, sym, day, aux_only=False, trades=False):
    """пути на боксе от alpha/stakan/ (то, что нужно ядру и aux_feats); aux_only (TK-522, нога из L1) — только mark/OI/ликвидации CHFT для aux_feats, стакан и сделки идут из L1; trades (L1 без .tr.zst, --l1-trades-raw) — ещё и parquet сделок для pq2bin trades"""
    if aux_only:
        return [f"chft/{ex}_futures/{day}/{h:02d}/{sym}_{k}.parquet" for h in range(24) for k in (KINDS[1:] if trades else KINDS[2:])] if ex in CHFT else []
    if ex == "bybit":
        return [f"bybit-linear-ob200/{day}/{sym}_{day}_ob200.data.zip"]
    if ex == "gate":
        d = day.replace("-", "")
        return [f"gate-futures/{sym}/{sym}-{d}{h:02d}.csv.gz" for h in range(24)]
    kinds = KINDS[1:] if ex == "binance" else KINDS
    return [f"chft/{ex}_futures/{day}/{h:02d}/{sym}_{k}.parquet" for h in range(24) for k in kinds]


BOX_SEM = None          # --box-conn N: не более N rsync к боксу разом на весь процесс (jobs × l1-p × fetch-p); None = без предела
BOX_RETRY_RC = (12, 255)   # rsync «protocol data stream» / ssh: бокс закрыл соединение при перегрузе (24 rsync шли, 48 — рвались: 08:18 10.11, 8 из 10 заданий)
BOX_TRIES = 4


def box_rsync(cmd, log):
    """rsync к боксу: слот BOX_SEM, при rc 12/255 (соединение закрыто) до BOX_TRIES попыток с паузой 5·k с; остальные коды (23 — нет файла, 11 — ENOSPC) сразу вызывающему"""
    for k in range(BOX_TRIES):
        if BOX_SEM:
            BOX_SEM.acquire()
        try:
            rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=open(log, "ab"))
        finally:
            if BOX_SEM:
                BOX_SEM.release()
        if rc not in BOX_RETRY_RC or k == BOX_TRIES - 1:
            return rc
        with open(log, "a") as f:
            f.write(f"rsync rc {rc}, повтор {k + 1}/{BOX_TRIES - 1}: {cmd[-2]}" + chr(10))
        time.sleep(5 * (k + 1))


def fetch(files, raw, p, log):
    os.makedirs(raw, exist_ok=True)
    cache = os.environ.get("P40_L1_CACHE")
    if cache:   # TK-522: prefetch_l1.py --aux положил parquet бокса в <кэш>/raw (rsync кладёт файл целиком, через rename) -> копия с диска, с бокса только недостающее
        hit = [f for f in files if os.path.exists(f"{cache}/raw/{f}")]
        if hit:
            open(f"{raw}.hit", "w").write(chr(10).join(hit) + chr(10))
            subprocess.call(["rsync", "-q", "-a", "-R", f"--files-from={raw}.hit", f"{cache}/raw/", raw + "/"])
            os.remove(f"{raw}.hit")
            hs = set(hit)
            files = [f for f in files if f not in hs]
    lst = f"{raw}.list"
    chunks = [c for c in (files[i::p] for i in range(p)) if c]
    cmds = []
    for i, c in enumerate(chunks):
        open(f"{lst}{i}", "w").write(chr(10).join(c) + chr(10))
        cmds.append(["rsync", "-q", "-R", "--ignore-missing-args", f"--files-from={lst}{i}", "-e", SSH, "box:alpha/stakan/", raw + "/"])
    with cf.ThreadPoolExecutor(max(1, len(cmds))) as tp:
        bad = list(tp.map(lambda cmd: box_rsync(cmd, log), cmds))
    for i in range(len(chunks)):
        if os.path.exists(f"{lst}{i}"):
            os.remove(f"{lst}{i}")
    if any(rc not in (0, 23, 24) for rc in bad):
        if 11 in bad and nospace(log, os.path.dirname(raw)):
            raise ShmFull(f"ENOSPC /dev/shm при rsync rc {bad}")
        raise RuntimeError(f"rsync rc {bad}")
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(raw) for f in fs)


def l1_src(ex):
    """источник L1 биржи: P40_L1_SRC_<БИРЖА> (TK-522: HL пересчитан на srv, на боксе его L1 нет — P40_L1_SRC_HYPERLIQUID=/data/p40l1-hl) либо общий L1_SRC"""
    return os.environ.get(f"P40_L1_SRC_{ex.upper()}", L1_SRC)


def l1_rel(ex, sym, day):
    return f"p40l1/{L1_EX[ex]}/{sym}/{day}"


def l1_leg(args, ex):
    return ex in L1_EX and not args.raw_legs and ex not in args.raw_ex


def fetch_l1(ex, sym, day, raw, log, trades_raw=False):
    """L1-сутки ноги с бокса (каталог целиком, несколько малых файлов) -> список `<ЧЧ>.l1.bin.zst`. СТРОГО (TK-518): нет каталога, метки .ok, `files=0`,
    число файлов или их размер не по метке -> RuntimeError (нога не пропускается молча: rsync --ignore-missing-args здесь не используется)"""
    rel = l1_rel(ex, sym, day)
    dst = f"{raw}/{rel}"
    os.makedirs(dst, exist_ok=True)
    cache = os.environ.get("P40_L1_CACHE")
    if cache and os.path.exists(f"{cache}/{rel}/.ok"):   # TK-522: prefetch_l1.py положил сутки ноги на диск srv целиком (rename из .part) -> с бокса не тянем; дальше те же проверки метки
        rc = subprocess.call(["rsync", "-q", "-a", f"{cache}/{rel}/", dst + "/"])
    else:
        rc = box_rsync(["rsync", "-q", "-a", "-e", SSH, f"{l1_src(ex)}/{rel}/", dst + "/"], log)
    if rc != 0:
        if rc == 11 and nospace(log, os.path.dirname(raw)):
            raise ShmFull(f"ENOSPC /dev/shm при rsync L1 {rel}")
        raise (L1Missing if rc == 23 else RuntimeError)(f"L1 {rel}: rsync rc {rc} (нет суток на боксе?)")
    try:
        kv = dict(x.split("=", 1) for x in open(f"{dst}/.ok").read().split() if "=" in x)
        size = int(kv["l1"])
        nf = int(kv["files"].split("/")[0]) if "files" in kv else -1   # CHFT: files=48/48 (l1+tr), 404=<число часов без файла поставщика>; Bybit/Gate: files=<число l1>, метки старого вида (raw= l1= tr=) без files= — число файлов берём с диска, размер сверяем
        tsize = int(kv.get("tr", 0))
    except (OSError, KeyError, ValueError) as e:
        raise (L1Missing if isinstance(e, OSError) else RuntimeError)(f"L1 {rel}: нет метки .ok или она не разобрана ({e})") from e
    fs = sorted(f for f in os.listdir(dst) if f.endswith((".l1.bin.zst", ".tr.zst")))
    l1 = [f for f in fs if f.endswith(".l1.bin.zst")]
    tr = [f for f in fs if f.endswith(".tr.zst")]
    bare404 = "files" not in kv and "404" in open(f"{dst}/.ok").read().split()   # Bybit/Gate: метка «raw=0 l1=0 tr=0 404» (голый 404, без files=) — у поставщика на сутки нет данных контракта
    if not fs and size == 0 and tsize == 0 and (bare404 or kv.get("files", "").split("/")[0] == "0" and int(kv.get("404", 0)) > 0):
        return []   # TK-522 (Судья 10.11, класс 3): у поставщика на сутки нет ни одного файла (метка files=0 404=N) -> дыра на все 24 часа, не RuntimeError; часы дыры негодны (job() пишет их в P40_HOLES и в l1_gaps.csv)
    if nf == -1:
        nf = len(fs)
    nd = len(l1) if kv.get("src") == "rawcopy" else len(fs)   # метка src=rawcopy (raw-conv, копия srv->бокс): files= — число l1-файлов, а .tr.zst лежат рядом (BTC/ETH/AAVE OKX 17.09: files=24, на диске 48); байты l1 и tr сверяются как всегда
    if nf <= 0 or not l1 or nd != nf or sum(os.path.getsize(f"{dst}/{f}") for f in l1) != size or sum(os.path.getsize(f"{dst}/{f}") for f in tr) != tsize:
        raise RuntimeError(f"L1 {rel}: по метке файлов {nf}, байт l1 {size}, tr {tsize}; на диске файлов {len(fs)} (l1 {len(l1)}, tr {len(tr)})")
    if ex in CHFT and not tr and not trades_raw:   # у CHFT сутки без сделок = L1 собран без них (Binance TK-521: tr=0) -> ядру нужны сделки: сырьё ноги или (--l1-trades-raw) сделки из parquet бокса при книге из L1
        raise L1Missing(f"L1 {rel}: у CHFT нет .tr.zst (tr={tsize})")
    return [f"{dst}/{f}" for f in fs]


def unpack_l1(ex, sym, day, raw, bins, log, fs=None):
    """-> (fetch_s, байт L1, wall, cpu распаковки): `<ЧЧ>.l1.bin.zst` -> `<bins>/B/<ЧЧ>.l1.bin`, `<ЧЧ>.tr.zst` (сделки CHFT, формат `pq2bin trades` 25 Б) -> `<bins>/T/<ЧЧ>.tr`; merge() линкует обоих как `<idx>.bin`"""
    t = time.time()
    fs = fetch_l1(ex, sym, day, raw, log) if fs is None else fs   # fs — уже забранные сутки (job() пробует L1 до выбора файлов сырья)
    tf, nb = time.time() - t, sum(os.path.getsize(f) for f in fs)
    os.makedirs(f"{bins}/B", exist_ok=True)
    os.makedirs(f"{bins}/T", exist_ok=True)
    w = c = 0.0
    for sub, suf in (("B", ".l1.bin.zst"), ("T", ".tr.zst")):   # сделок у Bybit/Gate нет — `T` пуст, как у прежнего конвертера
        part = [f for f in fs if f.endswith(suf)]
        if part:
            rc, w1, c1 = run(["zstd", "-d", "-q", "-f", "--output-dir-flat", f"{bins}/{sub}", *part], log=log)
            if rc != 0:
                fail(f"zstd -d L1 {l1_rel(ex, sym, day)} rc {rc}", log, bins)
            w, c = w + w1, c + c1
    return tf, nb, w, c


def fail(msg, log, shm_path):
    if nospace(log, os.path.dirname(shm_path)):
        raise ShmFull("ENOSPC /dev/shm: " + msg)
    raise RuntimeError(msg)


def convert(ex, sym, day, raw, bins, mult, log):
    """-> (wall, cpu); bins/A или B: <bins>/B (книга), <bins>/T (сделки; пусто, если сделок нет)"""
    os.makedirs(f"{bins}/T", exist_ok=True)
    w = c = 0.0
    if ex == "bybit":
        z = f"{raw}/bybit-linear-ob200/{day}/{sym}_{day}_ob200.data.zip"
        r = run([PQ2BIN, "bybit", z, f"{bins}/B"], log=log)
    elif ex == "gate":
        r = run([PQ2BIN, "gate", f"{raw}/gate-futures/{sym}", sym, day.replace("-", ""), f"{bins}/B", str(mult), "2"], log=log)
    else:
        root = f"{raw}/chft/{ex}_futures/{day}"
        book_root = f"{BINANCE_BOOK}/{day}" if ex == "binance" else root
        r = run([PQ2BIN, "book", book_root, f"{sym}_orderbook.parquet", f"{bins}/B", "2"], log=log)
        w, c = r[1], r[2]
        if r[0] != 0:
            fail(f"pq2bin book {ex} {sym} rc {r[0]}", log, bins)
        r2 = run([PQ2BIN, "trades", root, f"{sym}_trades.parquet", f"{bins}/T", "2"], log=log)
        r = (r2[0], w + r2[1], c + r2[2])
    if r[0] != 0:
        fail(f"pq2bin {ex} {sym} rc {r[0]}", log, bins)
    return r[1], r[2]


REC = np.dtype([("et", "<i8"), ("fu", "<i8"), ("px", "<f8"), ("qt", "<f8"), ("fl", "u1")])


def mid_open(path):
    """середина первого снимка книги ноги (первый <ЧЧ>.bin каталога; .bin 33 Б/запись: бит0 = снимок, бит1 = bid); нет снимка в начале -> медиана цен первых 20000 записей"""
    hs = sorted(f for f in os.listdir(path) if f.endswith(".bin"))
    r = np.fromfile(f"{path}/{hs[0]}", dtype=REC, count=400000) if hs else np.empty(0, REC)
    if len(r) == 0:
        raise RuntimeError(f"пустая книга {path}")
    n = int(np.argmin(r["fl"] & 1)) if (r["fl"][0] & 1) and (r["fl"] & 1 == 0).any() else (len(r) if r["fl"][0] & 1 else 0)
    g = r[:n]
    if n and (g["fl"] & 2).any() and (~g["fl"] & 2).any():
        bid, ask = g["px"][(g["fl"] & 2) > 0], g["px"][(g["fl"] & 2) == 0]
        return (bid.max() + ask.min()) / 2
    return float(np.median(r["px"][:20000]))


VEX = {"okx": "okx_futures", "bitget": "bitget_futures", "aster": "aster_futures", "hyperliquid": "hyperliquid_futures", "binance": "binance_futures"}


def load_validity(paths, seen=None):
    """validity.csv.gz G0 (ex,contract,date,hour,status,...) -> {(ex,contract,date): {негодные часы}}; статус не ok = негодный час, 'day' не ok = все 24 часа; строки, которых нет в таблице, негодными не считаются.
    `seen` (dict) — П-40 §16.5: заполняется {(ex,contract,date): {часы, у которых есть строка}} ('day' — любая строка суток = все 24 часа), для проверки покрытия"""
    bad = {}
    for p in paths:
        for r in csv.reader(gzip.open(p, "rt")):
            if r[3] == "hour":
                continue
            if seen is not None:
                seen.setdefault((r[0], r[1], r[2]), set()).update(range(24) if r[3] == "day" else [int(r[3])])
            if r[4] != "ok":
                bad.setdefault((r[0], r[1], r[2]), set()).update(range(24) if r[3] == "day" else [int(r[3])])
    return bad


def validity_gaps(seen, pairs, days):
    """П-40 §16.5: (биржа, контракт, сутки) плана -> число часов без строки validity (час без строки негоден; счёт при непустом результате не запускается); пусто = покрытие 100 %"""
    gaps = {}
    for p in pairs:
        for s_ in "AB":
            ex, sym = VEX.get(p["ex" + s_], p["ex" + s_]), p["sym" + s_]
            for day in days:
                n = 24 - len(seen.get((ex, sym, day), ()))
                if n:
                    gaps[(ex, sym, day)] = n
    return gaps


CROSSED_MAX = 0.01   # G0 r2 §2.3: доля кроссированных состояний в часе > 1 % -> час негоден (как в validity.csv.gz G0: статус `crossed>1%`)
VAL_HEAD = ["ex", "contract", "date", "hour", "status", "crossed_share", "prev_gaps", "checked", "age_p99_ms", "age_max_ms"]


def l1_validity_rows(ex, sym, days, stats):
    """TK-522, Судья 10.11 §2: validity по L1 — той же книге, что считается (§16.1). `stats` — вывод `p40core validity` слитого каталога ноги: {номер часа чанка: (состояний, кроссированных, age_p99_мс, age_max_мс)}.
    Строка на (сутки, час) КАЖДОГО часа плана: нет файла/нет состояний -> `no_data` (дыра поставщика, `404`/gap_hours), одно состояние -> `no_diffs`, кросс > 1 % -> `crossed>1%`, иначе `ok`; `prev_gaps` = `NA` (непрерывности update_id в L1 нет — не бракует час, В-115)"""
    rows = []
    for di, day in enumerate(days):
        for h in range(24):
            n, c, p99, mx = stats.get(24 * di + h, (0, 0, 0, 0))
            share = c / n if n else 0.0
            st = "no_data" if n == 0 else ("no_diffs" if n == 1 else ("crossed>1%" if share > CROSSED_MAX else "ok"))   # n == 1: в часе одно состояние = только снимок без изменений (Gate 29.09 вечер–08.10: часовые снимки; в G0 `no_diffs`), по умолчанию негоден (§16.5)
            rows.append([VEX.get(ex, ex), sym, day, f"{h:02d}", st, round(share, 6), "NA", n, p99, mx])
    return rows


def run_l1_validity(core, mdir, env, log):
    """`p40core validity <mdir>/B` -> {час чанка: (состояний, кроссированных, age_p99_мс, age_max_мс)}"""
    r = subprocess.run([core, "validity", f"{mdir}/B"], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"p40core validity {mdir}: rc {r.returncode} {r.stderr[-300:]}")
    return {int(x[0]): tuple(map(int, x[1:])) for x in (ln.split() for ln in r.stdout.splitlines()) if len(x) == 5}


def run_l1_age(core, legdir, t0s, env, shift_b=False):
    """`p40core age <каталог>/B <t0>` -> {t0_мс: возраст лучших цен, мс; -1 = нет состояний до t0}; shift_b — нога B варианта K1 (env с P40_SHIFT_B_MS)"""
    with tempfile.NamedTemporaryFile("w", suffix=".t0", delete=False) as f:
        f.write(chr(10).join(map(str, t0s)) + chr(10))
    try:
        r = subprocess.run([core, "age", f"{legdir}/B", f.name] + (["B"] if shift_b else []), env=env, capture_output=True, text=True)
    finally:
        os.unlink(f.name)
    if r.returncode != 0:
        raise RuntimeError(f"p40core age {legdir}: rc {r.returncode} {r.stderr[-300:]}")
    return {int(x[0]): int(x[1]) for x in (ln.split() for ln in r.stdout.splitlines()) if len(x) == 2}


def write_age(core, base, vnames, a, b, env, venvs):
    """TK-522 (Судья 10.11 §2 п. 2): `<base>[.<вариант>].age.csv` (t0_ms,ageA_ms,ageB_ms) — возраст лучших цен обеих ног на каждом t0 эпизода (t_start в features.csv), по тому же ряду, что считает ядро. Ноги читаются один раз на все варианты; K1 (сдвиг B) — отдельный ряд B со сдвигом.
    assemble --stale-smax сверяет с S_max контракта (smax_l1.csv, после прогона)"""
    def t0s(names):
        out = set()
        for n in names:
            pth = f"{base}{'.' + n if n else ''}.features.csv"
            if os.path.exists(pth):
                with open(pth) as f:
                    out.update(int(r["t_start"]) for r in csv.DictReader(f))
        return sorted(out)

    shifted = [n for n in vnames if "P40_SHIFT_B_MS" in venvs[n]]
    plain = [""] + [n for n in vnames if n not in shifted]
    tp = t0s(plain)
    agea = run_l1_age(core, a, tp, env)
    groups = [(plain, tp, run_l1_age(core, b, tp, env), agea)]
    for n in shifted:
        tk = t0s([n])
        groups.append(([n], tk, run_l1_age(core, b, tk, dict(env, **venvs[n]), shift_b=True), run_l1_age(core, a, tk, env)))
    for names, ts, ab, aa in groups:
        for n in names:
            with open(f"{base}{'.' + n if n else ''}.age.csv", "w") as f:
                f.write("t0_ms,ageA_ms,ageB_ms" + chr(10))
                f.writelines(f"{t},{aa.get(t, -1)},{ab.get(t, -1)}" + chr(10) for t in ts)


def record_validity(out, rows):
    """validity_l1.csv: строки validity по L1 (ноги общие для пар актива -> без повторов по (биржа, контракт, сутки))"""
    with LOG_LOCK:
        dst = f"{out}/validity_l1.csv"
        head = not os.path.exists(dst)
        with open(dst, "a", newline="") as f:
            w = csv.writer(f)
            if head:
                w.writerow(VAL_HEAD)
            for r in rows:
                if (r[0], r[1], r[2]) not in SEEN_VAL:
                    w.writerow(r)
            SEEN_VAL.update((r[0], r[1], r[2]) for r in rows)


def write_holes(path, bad, legs, days):
    """дыры пары = объединение негодных часов обеих ног по суткам чанка -> строки `начало_мс,конец_мс` (P40_HOLES), склеены"""
    iv = []
    for i, day in enumerate(days):
        t0 = calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000
        for ex, sym in legs:
            iv += [(t0 + h * 3600000, t0 + (h + 1) * 3600000) for h in bad.get((VEX.get(ex, ex), sym, day), ())]
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    open(path, "w").write("".join(f"{a},{b}" + chr(10) for a, b in out))
    return len(out)


def gap_hours(fs):
    """часы суток без файла `<ЧЧ>.l1.bin.zst` среди файлов L1 ноги (пустые сутки поставщика, fs=[], — все 24)"""
    return sorted(set(range(24)) - {int(os.path.basename(f)[:2]) for f in fs if f.endswith(".l1.bin.zst")})


def record_gap(out, ex, sym, day, miss):
    """l1_gaps.csv (Судья 10.11): биржа, контракт, сутки, часы без L1, причина (404 — у поставщика на сутки нет файлов; gap_hours — часть часов)"""
    with LOG_LOCK:
        if (ex, sym, day) in SEEN_GAPS:
            return
        SEEN_GAPS.add((ex, sym, day))
        dst = f"{out}/l1_gaps.csv"
        head = not os.path.exists(dst)
        with open(dst, "a", newline="") as f:
            w = csv.writer(f)
            if head:
                w.writerow(["exchange", "contract", "date", "hours_missing", "reason", "hours"])
            w.writerow([ex, sym, day, len(miss), "404" if len(miss) == 24 else "gap_hours", " ".join(map(str, miss))])


def record_nodata(out, asset, cid, p):
    """l1_nodata.csv: пара, у ноги которой за чанк нет ни одного часа L1 (нет данных поставщика) — в таблицу «план / посчитано» (§16.9), не упавшая"""
    with LOG_LOCK:
        dst = f"{out}/l1_nodata.csv"
        head = not os.path.exists(dst)
        with open(dst, "a", newline="") as f:
            w = csv.writer(f)
            if head:
                w.writerow(["asset", "chunk", "pair", "exA", "symA", "exB", "symB"])
            w.writerow([asset, cid, p["pair"], p["exA"], p["symA"], p["exB"], p["symB"]])


def drop_heavy(raw):
    """после конвертации сырьё книги/сделок не нужно (aux читает только mark/OI/ликвидации CHFT; у ног Bybit/Gate таких рядов нет); Bybit/Gate — целиком"""
    for d, _, fs in os.walk(raw):
        for f in fs:
            if f.endswith(("_orderbook.parquet", "_trades.parquet", ".zip", ".csv.gz", ".l1.bin.zst", ".tr.zst")):
                os.remove(os.path.join(d, f))


def merge(srcs, dst, sub):
    """часовые .bin суток подряд -> <dst>/<sub>/<idx>.bin симлинками (idx = 24*номер суток + час; ядро читает 0..P40_HOURS-1)"""
    os.makedirs(f"{dst}/{sub}", exist_ok=True)
    for di, src in enumerate(srcs):
        for h in range(24):
            f = f"{src}/{sub}/{h:02d}.bin"
            if not os.path.exists(f):
                f = f"{src}/{sub}/{h:02d}.l1.bin" if sub == "B" else f"{src}/{sub}/{h:02d}.tr"   # L1 (TK-518/522): имя другое, содержимое то же (33-байтные строки книги / 25-байтные сделки)
            if os.path.exists(f):
                os.symlink(f, f"{dst}/{sub}/{24 * di + h:02d}.bin")


def collect_auxholes(out, path):
    """дыры mark/OI/ликвидаций поставщика по ногам пары -> <out>/auxholes.csv без повторов (ноги общие для пар актива)"""
    with LOG_LOCK:
        new = [r for r in csv.reader(open(path)) if r and r[0] != "exchange" and tuple(r) not in SEEN_AUXH]
        SEEN_AUXH.update(map(tuple, new))
        dst = f"{out}/auxholes.csv"
        head = not os.path.exists(dst)
        with open(dst, "a", newline="") as f:
            w = csv.writer(f)
            if head:
                w.writerow(["exchange", "contract", "date", "hour", "kind"])
            w.writerows(new)


def purge_aux_cache(legs, days):
    """TK-522: после DONE актива parquet его ног из <кэш>/raw стирается (метки .aux остаются — повторно не качать); L1 в кэше остаётся"""
    cache = os.environ.get("P40_L1_CACHE")
    for (ex, sym) in legs if cache else ():
        for day in days:
            for f in box_files(ex, sym, day, aux_only=True, trades=True):
                try:
                    os.remove(f"{cache}/raw/{f}")
                except OSError:
                    pass


def job(args, asset, days, pairs, outlog):
    t_all = time.time()
    cid = days[0] if len(days) == 1 else f"{days[0]}+{len(days)}"
    out_d = f"{args.out}/{asset}/{cid}"
    if os.path.exists(f"{out_d}/DONE") and (not args.age or os.path.exists(f"{out_d}/AGE")):   # с --age задание, готовое без возрастов (до TK-522 п. 2), пересчитывается: L1 ног нужен для возраста на t0
        return
    os.makedirs(out_d, exist_ok=True)
    wd = f"{args.shm}/{asset}_{cid}"
    shutil.rmtree(wd, ignore_errors=True)
    log = f"{out_d}/orch.log"
    try:
        legs = {}
        for p in pairs:
            for s in "AB":
                legs.setdefault((p["ex" + s], p["sym" + s]), float(p["mult" + s] or 1))
        nbytes = 0
        t_fetch = t_conv = c_conv = 0.0
        key = f"{asset}_{cid}"
        t_wait = t_merge = t_aux = 0.0
        tw = time.time()
        args.budget.set(key, PILOT_LEG_B * len(legs))
        t_wait += time.time() - tw
        est = peak = 0
        t = time.time()
        gapbad = {}   # (VEX-биржа, контракт, сутки) -> часы без L1 (дыры поставщика); к G0-негодным часам args.bad
        l1res = {}   # (день, ex, sym) -> файлы L1 или L1Missing; TK-522 v2: все ноги×сутки одним пулом (--l1-p), а не по одной ноге подряд (rsync-латентность: 52 из 65 с куска)
        with cf.ThreadPoolExecutor(args.l1_p) as tp:
            fl = {tp.submit(fetch_l1, ex, sym, day, f"{wd}/raw/{day}", log, args.l1_trades_raw or args.naive_only): (day, ex, sym) for day in days for (ex, sym) in legs if l1_leg(args, ex)}
            for f in cf.as_completed(fl):
                try:
                    l1res[fl[f]] = f.result()
                except L1Missing as e:
                    l1res[fl[f]] = e
        t_fetch += time.time() - t
        for di, day in enumerate(days):
            raw = f"{wd}/raw/{day}"
            t = time.time()
            l1fs = {}   # (ex, sym) -> файлы L1 суток; нет L1: с --l1-fallback нога CHFT -> сырьё (convert), иначе сбой задания, не пропуск ноги
            for (ex, sym) in legs:
                if l1_leg(args, ex):
                    r = l1res[(day, ex, sym)]
                    if isinstance(r, L1Missing):
                        if not (args.l1_fallback and ex in CHFT):
                            raise r
                        with open(log, "a") as f:
                            f.write(f"L1 нет -> сырьё: {r}" + chr(10))
                    else:
                        l1fs[(ex, sym)] = r
            trraw = set() if args.naive_only else {k for k, fs in l1fs.items() if k[0] in CHFT and fs and not any(f.endswith(".tr.zst") for f in fs)}   # книга из L1, сделок в L1 нет (Binance TK-521)
            nbytes += fetch([] if args.naive_only else [f for (ex, sym) in legs for f in box_files(ex, sym, day, aux_only=(ex, sym) in l1fs, trades=(ex, sym) in trraw)], raw, args.fetch_p, log)   # --naive-only: ни сделок, ни mark/OI/ликвидаций — только книга L1
            t_fetch += time.time() - t
            for (ex, sym), mult in legs.items():
                bins = f"{wd}/bin/{leg_key(ex, sym)}/{day}"
                if (ex, sym) in l1fs:
                    tf, nb, w, c = unpack_l1(ex, sym, day, raw, bins, log, fs=l1fs[(ex, sym)])
                    t_fetch, nbytes = t_fetch + tf, nb + nbytes
                    if (ex, sym) in trraw:   # сделки той же сутки — тем же pq2bin trades, что в сырьевом пути convert()
                        os.makedirs(f"{bins}/T", exist_ok=True)
                        r2 = run([PQ2BIN, "trades", f"{raw}/chft/{ex}_futures/{day}", f"{sym}_trades.parquet", f"{bins}/T", "2"], log=log)
                        if r2[0] != 0:
                            fail(f"pq2bin trades {ex} {sym} rc {r2[0]}", log, bins)
                        w, c = w + r2[1], c + r2[2]
                else:
                    w, c = convert(ex, sym, day, raw, bins, mult, log)
                t_conv, c_conv = t_conv + w, c_conv + c
            for (ex, sym), fs in l1fs.items():   # часы дыры = часы суток без файла L1 (пустые сутки поставщика — все 24; частичные — недостающие); негодны, эпизоды в них не создаются, позиция усекается (§2.2)
                miss = gap_hours(fs)
                if miss:
                    gapbad[(VEX.get(ex, ex), sym, day)] = set(miss)
                    record_gap(args.out, ex, sym, day, miss)
            peak = max(peak, du(wd))
            if di == 0:
                p1 = peak
            drop_heavy(raw)
            if di == 0 and len(days) > 1:
                est = peak_estimate(p1, du(wd), len(days))
                tw = time.time()
                args.budget.set(key, est, full=True)
                t_wait += time.time() - tw
            elif di == 0:
                est = int(p1 * SHM_MARGIN)
        with open(log, "a") as f:
            f.write(f"shm: оценка пика {est} Б, факт {peak} Б, бюджет {args.budget.cap} Б" + chr(10))
        tm = time.time()
        mrg = {}
        for (ex, sym) in legs:
            k = leg_key(ex, sym)
            mrg[(ex, sym)] = f"{wd}/mrg/{k}"
            for sub in "BT":
                merge([f"{wd}/bin/{k}/{day}" for day in days], mrg[(ex, sym)], sub)
        t_merge = time.time() - tm
        env = dict(os.environ, P40_HOURS=str(24 * len(days)), **({"P40_NAIVE": "1"} if args.naive_only else {}))
        if args.validity_l1:   # TK-522 (Судья 10.11 §2): validity по L1 всех ног плана ДО эпизодов пар; негодные часы (no_data, crossed>1%) -> к дырам; покрытие (нога, час) = 100 % по построению (строка на каждый час плана)
            for (ex, sym) in legs:
                if not l1_leg(args, ex):
                    raise RuntimeError(f"--validity-l1: нога {ex} {sym} не из L1 (§16.1 одна книга)")
                st = l1_validity_rows(ex, sym, days, run_l1_validity(CORE, mrg[(ex, sym)], env, log))
                record_validity(args.out, st)
                for r in st:
                    if r[4] != "ok":
                        gapbad.setdefault((r[0], r[1], r[2]), set()).add(int(r[3]))
        t_core = c_core = c_aux = 0.0
        for p in pairs:
            a, b = (mrg[(p["ex" + s], p["sym" + s])] for s in "AB")
            if not all(os.path.isdir(f"{x}/B") and os.listdir(f"{x}/B") for x in (a, b)):   # нога без единого годного часа за чанк = «нет данных поставщика» (Судья 10.11): не упавшая пара и не молчаливое выпадение — строка в l1_nodata.csv
                record_nodata(args.out, asset, cid, p)
                continue
            base = f"{out_d}/{p['pair']}"
            lots = []
            for s_, bd in zip("AB", (a, b)):
                lot = (p["lot" + s_] or "").strip()
                if lot in ("", "auto"):
                    lot = repr(float(args.n_usdt / (mid_open(f"{bd}/B") * float(p.get("ct" + s_) or 1))))
                lots.append(lot)
            if args.naive_only:   # TK-522 (В-125…В-127): только эпизоды + <база>.naive.csv — без заявок/сделок/признаков (ядро с 3 аргументами = без проходов 2–4)
                cmd = [CORE, base, f"{a}/B", f"{b}/B"]
            else:
                with LOG_LOCK, open(f"{args.out}/lots.csv", "a") as f:
                    f.write(f"{asset},{cid},{p['pair']},{lots[0]},{lots[1]}" + chr(10))
                cmd = [CORE, base, f"{a}/B", f"{b}/B", f"{a}/T", f"{b}/T", lots[0], lots[1], str(args.l_ms), p["stepA"] or "1e-9", p["stepB"] or "1e-9"]
            penv = env
            pleg = [(p["exA"], p["symA"]), (p["exB"], p["symB"])]
            if args.validity or args.validity_l1 or any((VEX.get(ex, ex), sym, day) in gapbad for ex, sym in pleg for day in days):
                jbad = {k: set(v) for k, v in args.bad.items()}
                for k, v in gapbad.items():
                    jbad.setdefault(k, set()).update(v)
                nh = write_holes(f"{base}.holes", jbad, pleg, days)
                penv = dict(env, P40_TRUNC="1", P40_HOLES=f"{base}.holes", P40_HOLE_WARM="1", **({"P40_END_MS": str(args.end_ms)} if args.end_ms else {}))
            if not args.no_base:   # --no-base (TK-522, Судья 11.11): досчёт одного варианта без повторного базового прохода
                rc, w, c = run(cmd, env=penv, log=log)
                if rc != 0:
                    raise RuntimeError(f"ядро rc {rc} пара {p['pair']}")
                t_core, c_core = t_core + w, c_core + c
            vruns = []   # TK-522 v2: варианты (K1 сдвиг, K2, NOTRADE) тем же слиянием рядов — без нового fetch/распаковки; выход `<base>.<имя>`, спецы в specs_<имя>.txt
            for vname, venv in args.variants:
                vcmd = [cmd[0], f"{base}.{vname}"] + cmd[2:]
                rc, w, c = run(vcmd, env=dict(penv, **venv), log=log)
                if rc != 0:
                    raise RuntimeError(f"ядро (вариант {vname}) rc {rc} пара {p['pair']}")
                t_core, c_core = t_core + w, c_core + c
                vruns.append(vname)
            if args.naive_only:
                continue
            if True:   # TK-522: aux.csv нужен assemble у КАЖДОЙ пары, в т.ч. Bybit×Gate (оба корня «-» -> пустые признаки, srcA=srcB=0); раньше aux не писали -> assemble падал на FileNotFoundError
                ra, rb = (",".join(f"{wd}/raw/{day}/chft/{p['ex' + s]}_futures/{day}" for day in days) if p["ex" + s] in CHFT else "-" for s in "AB")   # Bybit/Gate: рядов mark/OI/ликвидаций на боксе нет -> «-»
                rc, w, c = run([sys.executable, f"{HERE}/aux_feats.py", base, ra, p["symA"], rb, p["symB"]], log=log)
                if rc != 0:
                    raise RuntimeError(f"aux rc {rc} пара {p['pair']}")
                c_aux += c
                t_aux += w
                collect_auxholes(args.out, f"{base}.auxholes.csv")
                for vname in vruns:   # assemble читает `<base>.aux.csv`; aux не зависит от варианта ядра (ряды рынка, не заявки)
                    shutil.copy(f"{base}.aux.csv", f"{base}.{vname}.aux.csv")
            if args.age:   # Судья 10.11 §2 п. 2: возраст лучших цен на t0 обеих ног — для assemble --stale-smax (L1 ног ещё в shm)
                write_age(CORE, base, vruns, a, b, env, dict(args.variants))
            with LOG_LOCK, open(f"{args.out}/specs.txt", "a") as f:
                byb = int("bybit" in (p["exA"], p["exB"]))
                f.write(f"{asset}:{days[0].replace('-', '') if len(days) == 1 else 't0'}:{p['pair']}:{byb}:{base}" + chr(10))
            for vname in vruns:
                with LOG_LOCK, open(f"{args.out}/specs_{vname}.txt", "a") as f:
                    f.write(f"{asset}:{days[0].replace('-', '') if len(days) == 1 else 't0'}:{p['pair']}:{int('bybit' in (p['exA'], p['exB']))}:{base}.{vname}" + chr(10))
        if args.age:
            open(f"{out_d}/AGE", "w").write("")
        open(f"{out_d}/DONE", "w").write("")
        purge_aux_cache(legs, days)
        row = [asset, cid, len(pairs), len(legs), nbytes, f"{t_fetch:.1f}", f"{t_conv:.1f}", f"{c_conv:.1f}", f"{t_core:.1f}", f"{c_core:.1f}", f"{c_aux:.1f}", f"{time.time() - t_all:.1f}", f"{t_wait:.1f}", f"{t_merge:.1f}", f"{t_aux:.1f}"]
        with LOG_LOCK, open(outlog, "a") as f:
            f.write(",".join(map(str, row)) + chr(10))
        print("готово", *row, flush=True)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
        args.budget.release(f"{asset}_{cid}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs"), ap.add_argument("out")
    ap.add_argument("--days", required=True)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--fetch-p", type=int, default=10)
    ap.add_argument("--shm", default="/dev/shm/p40o")
    ap.add_argument("--assets", default="")
    ap.add_argument("--l-ms", type=int, default=500)
    ap.add_argument("--n-usdt", type=float, default=1000.0)
    ap.add_argument("--validity", default="", help="validity.csv.gz G0 (через запятую): негодные часы ног -> P40_HOLES пары + P40_TRUNC=1 (усечение §2.2); без флага усечения нет")
    ap.add_argument("--naive-only", action="store_true", help="TK-522 (В-125…В-127): ядро без проходов 2–4 (4 аргумента) и с P40_NAIVE=1 — выход <база>[.k2.k2|.k1p60].naive.csv (цены/размеры сторон ног на входе, выходе, пике; pnl на t0+600 с и на конец данных); без lots/aux/age/specs; сборка — naive_collect.py")
    ap.add_argument("--no-base", action="store_true", help="TK-522 (Судья 11.11): только --naive-only и с --variant — базовый проход ядра пропустить (досчёт варианта, напр. K1 −60 с: k1m60:P40_SHIFT_B_MS=-60000); без --variant — отказ")
    ap.add_argument("--age", action="store_true", help="TK-522 (Судья 10.11 §2 п. 2): возраст лучших цен обеих ног на t0 эпизодов -> <база>[.<вариант>].age.csv (p40core age); assemble --stale-smax помечает эпизоды stale")
    ap.add_argument("--validity-l1", action="store_true", help="TK-522 (Судья 10.11 §2): validity по L1 каждой ноги задания (p40core validity) -> <out>/validity_l1.csv; негодные часы -> P40_HOLES; заменяет G0-таблицу и --validity-strict (покрытие 100 % по построению)")
    ap.add_argument("--validity-strict", action="store_true", help="П-40 §16.5: покрытие validity 100 % (нога, час) плана — иначе отказ до счёта (код 3, список в <out>/validity_gaps.csv); требует --validity")
    ap.add_argument("--end-ms", type=int, default=0, help="конец данных для усечения (P40_END_MS, мс UTC; умолч. — последний момент рядов чанка)")
    ap.add_argument("--l1-fallback", action="store_true", help="TK-522: нет L1 суток у ноги CHFT на боксе (или L1 без сделок) -> эта нога из сырья (pq2bin book/trades), а не сбой задания; Bybit/Gate без сырья — по-прежнему сбой")
    ap.add_argument("--l1-trades-raw", action="store_true", help="TK-522: у ноги CHFT L1 без сделок (Binance: tr=0) — книга из L1, сделки pq2bin trades из parquet бокса (вместо всей ноги из сырья)")
    ap.add_argument("--raw-ex", default="", help="TK-522: биржи (через запятую), ноги которых читать из сырья, а не из L1 бокса (L1 биржи ещё неверен: Bitget до P40_L1_CARRY=1, E-17); остальные — L1")
    ap.add_argument("--raw-legs", action="store_true", help="TK-518/522: ВСЕ ноги читать из сырья бокса (прежний путь; для сверок и смоков, Bybit/Gate сырья на боксе уже нет). Умолчание — L1 бокса p40l1 (строго: нет суток = сбой)")
    ap.add_argument("--l1-p", type=int, default=1, help="TK-522 v2: параллельных rsync L1 на задание (ноги×сутки одним пулом; умолч. 1 = последовательно, как прежде)")
    ap.add_argument("--box-conn", type=int, default=0, help="TK-522: потолок одновременных rsync к боксу на весь процесс (jobs × l1-p × fetch-p; бокс закрывает соединения при перегрузе, rc 12); 0 = без предела, как прежде")
    ap.add_argument("--variant", action="append", default=[], metavar="ИМЯ:К=З,К=З", help="TK-522 v2: доп. прогон ядра на тех же рядах с добавочным окружением, напр. k2:P40_K2=1 | k1p60:P40_SHIFT_B_MS=60000 | drain:P40_NOTRADE=drain; повторяемый; выход <пара>.<имя>")
    ap.add_argument("--chunk", type=int, default=1, help="сутки подряд в одном прогоне ядра (умолч. 1 = как раньше); K>1: уровень P̄_L и эпизоды идут сквозь полночь, колонка day в assemble — по t0 (spec day=t0)")
    args = ap.parse_args()
    args.raw_ex = {x for x in args.raw_ex.split(",") if x}
    args.variants = []
    for v in args.variant:
        name, _, kv = v.partition(":")
        if not name or not kv or not name.replace("_", "").isalnum():
            sys.exit(f"--variant {v!r}: ожидалось ИМЯ:К=З[,К=З]")
        args.variants.append((name, dict(x.split("=", 1) for x in kv.split(","))))
    if args.no_base and not (args.naive_only and args.variants):
        sys.exit("--no-base: нужны --naive-only и хотя бы один --variant")
    os.makedirs(args.out, exist_ok=True)
    if args.validity_strict and not args.validity:
        sys.exit("--validity-strict требует --validity")
    seen = {}
    args.bad = load_validity(args.validity.split(","), seen) if args.validity else {}
    if args.validity_strict:
        plan = [r for r in csv.DictReader(open(args.pairs)) if not args.assets or r["asset"] in args.assets.split(",")]
        gaps = validity_gaps(seen, plan, args.days.split(","))
        if gaps:
            with open(f"{args.out}/validity_gaps.csv", "w") as f:
                f.write("ex,contract,day,hours_without_row" + chr(10) + "".join(f"{k[0]},{k[1]},{k[2]},{n}" + chr(10) for k, n in sorted(gaps.items())))
            sys.exit(f"П-40 §16.5: у {len(gaps)} (нога, сутки) плана нет строк validity (часов без строки {sum(gaps.values())}) — счёт не запущен, список {args.out}/validity_gaps.csv")
    outlog = f"{args.out}/orch.csv"
    if not os.path.exists(outlog):
        open(outlog, "w").write("asset,day,pairs,legs,bytes,fetch_s,convert_s,convert_cpu_s,core_s,core_cpu_s,aux_cpu_s,total_s,wait_s,merge_s,aux_s\n")
    by_asset = {}
    for r in csv.DictReader(open(args.pairs)):
        by_asset.setdefault(r["asset"], []).append(r)
    keep = set(filter(None, args.assets.split(",")))
    if not args.raw_legs and "P40_L1" not in os.environ and any(r[f"ex{s}"] in L1_EX for r in sum(by_asset.values(), []) for s in "AB"):
        sys.exit("TK-522: ноги идут из L1 (без глубины) -> признаки depth_slip/bookimb невозможны; задайте P40_L1=1 на весь прогон (ядро берёт l1_notional/bookimb1 по лучшим ценам) или --raw-legs")
    ds = args.days.split(",")
    todo = [(a, ds[i:i + args.chunk], ps) for i in range(0, len(ds), args.chunk) for a, ps in by_asset.items() if not keep or a in keep]
    if "P40_L1" in os.environ and b"P40_L1" not in open(CORE, "rb").read():
        sys.exit(f"TK-518: P40_L1 задан, но ядро {CORE} его не знает (сборка до TK-515: бит2 L1 и признаки без глубины не поддержаны) — задайте P40_CORE на сборку TK-515+")
    err = shm_err = 0
    args.budget = ShmBudget(args.shm)
    global BOX_SEM
    BOX_SEM = threading.BoundedSemaphore(args.box_conn) if args.box_conn > 0 else None
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(job, args, a, d, ps, outlog): (a, d) for a, d, ps in todo}
        for f in cf.as_completed(futs):
            try:
                f.result()
            except ShmFull as e:
                err += 1
                shm_err += 1
                print("СБОЙ ENOSPC", futs[f][0], futs[f][1][0], e, file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001
                err += 1
                print("СБОЙ", futs[f], e, file=sys.stderr, flush=True)
    sys.exit(EXIT_SHM if shm_err else 1 if err else 0)


if __name__ == "__main__":
    main()
