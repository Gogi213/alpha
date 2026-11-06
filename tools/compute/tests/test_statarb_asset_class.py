"""TK-493 w4 (D-01, П-37 r3 §4.1 [Н42]): единая функция классификации контрактов (asset_class.py) для asset_lib.py и panels_xdl9.py.

Запуск: python -I tools/compute/tests/test_statarb_asset_class.py   (stdlib; проверки asset_lib - только если есть numpy).
Проверки на синтетических строках instruments.csv; последний класс - сверка с реальными data/xdl9/inst (пропуск, если каталога нет).
"""
import csv, importlib.util, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
XD = os.path.join(HERE, "..", "statarb", "xdl9")


def load(name, subdir=XD):
    spec = importlib.util.spec_from_file_location(name + "_t", os.path.join(subdir, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name + "_t"] = m
    spec.loader.exec_module(m)
    return m


sys.path.insert(0, XD)
import asset_class as AC  # noqa: E402

OUT = AC.OUT_OF_TEST


def rb(path):
    with open(path, "rb") as fh:
        return fh.read()


def row(ex, sym, base, cls="crypto", quote="USDT", status="Trading", class_src="", note=""):
    return dict(exchange=ex, symbol=sym, base=base, quote=quote, **{"class": cls}, class_src=class_src, status=status, note=note)


class PreListingPrimaryFields(unittest.TestCase):
    def test_bybit_status_and_token(self):
        self.assertEqual(AC.prelisting(row("bybit", "OPENAIUSDT", "OPENAI", "equity", status="PreLaunch")), (1, "bybit status=PreLaunch"))
        # токен isPreListing при другом статусе - тоже признак (кросс-проверка первичных полей)
        r = row("bybit", "XUSDT", "X", status="Trading", note="pre-listing (isPreListing=true; пред-рынок до листинга базового актива)")
        self.assertEqual(AC.prelisting(r)[0], 1)
        self.assertEqual(AC.prelisting(row("bybit", "BTCUSDT", "BTC", note="isPreListing=false"))[0], 0)
        self.assertEqual(AC.prelisting(row("bybit", "BTCUSDT", "BTC", status="Closed"))[0], 0)

    def test_binance_premarket_only_by_underlying_type(self):
        r = row("binance", "OPENAIUSDT", "OPENAI", "other", class_src="exchange:underlyingType=PREMARKET|sub=Pre-IPO+TradFi")
        self.assertEqual(AC.prelisting(r), (1, "binance underlyingType=PREMARKET"))
        # прежняя ошибка: «pre-market» в прозе note не подходила под подстроку «pre_market» - теперь признак берётся из поля, а не из прозы
        r = row("binance", "PREDICTUSDT", "PREDICT", class_src="exchange:underlyingType=PREMARKET|sub=Pre-Market", note="pre-market токен до листинга")
        self.assertEqual(AC.prelisting(r)[0], 1)
        # проза в note при обычном underlyingType - не признак
        r = row("binance", "FOOUSDT", "FOO", class_src="exchange:underlyingType=COIN|sub=Pre-IPO", note="pre_market pre-IPO pre-launch")
        self.assertEqual(AC.prelisting(r)[0], 0)
        # PENDING_TRADING - не пред-листинг по правилу r3
        self.assertEqual(AC.prelisting(row("binance", "GAIBUSDT", "GAIB", status="PENDING_TRADING", class_src="exchange:underlyingType=COIN|sub="))[0], 0)

    def test_okx_rule_type_token(self):
        r = row("okx", "OPENAI-USDT-SWAP", "OPENAI", "other", status="live", note="ruleType=pre_market: пред-IPO/приватная компания")
        self.assertEqual(AC.prelisting(r), (1, "okx ruleType=pre_market"))
        self.assertEqual(AC.prelisting(row("okx", "BTC-USDT-SWAP", "BTC", status="live", note="ruleType=normal"))[0], 0)
        self.assertEqual(AC.prelisting(row("okx", "FOO-USDT-SWAP", "FOO", note="это не pre_market без ключа"))[0], 0)

    def test_aster_prelaunch_in_subtype_list(self):
        for cs in ("exchange:underlyingSubType=pre-launch", "exchange:underlyingSubType=pre-launch+STOCK+AOS2+USD1-RWA",
                   "exchange:underlyingSubType=STOCK+pre-launch"):
            self.assertEqual(AC.prelisting(row("aster", "XUSDT", "X", class_src=cs))[0], 1, cs)
        for cs in ("exchange:underlyingSubType=STOCK", "exchange:underlyingSubType=Commodities+AOS2+name=pre-launch metals"):
            self.assertEqual(AC.prelisting(row("aster", "XUSDT", "X", class_src=cs))[0], 0, cs)

    def test_kraken_and_hyperliquid_extensions(self):
        self.assertEqual(AC.prelisting(row("kraken", "PF_OPENAIXUSD", "OPENAIx", "equity", class_src="kraken:category=Pre-IPO"))[0], 1)
        self.assertEqual(AC.prelisting(row("kraken", "PF_XBTUSD", "BTC", class_src="kraken:category=Layer 1"))[0], 0)
        self.assertEqual(AC.prelisting(row("hyperliquid", "xyz:OURA", "OURA", "other", class_src="hl:perpCategories=preipo"))[0], 1)
        self.assertEqual(AC.prelisting(row("hyperliquid", "xyz:AAPL", "AAPL", "equity", class_src="hl:perpCategories=stocks+ticker_map"))[0], 0)

    def test_exchanges_without_primary_field(self):
        # BingX/Bitget/Lighter первичного поля не отдают: признак пред-листинга у них только через ключ приватной базы
        for ex, cs, nt in (("bingx", "ticker_list", "частная компания, синтетический перп до IPO"),
                           ("bitget", "isRwa=YES+ticker_map", "частная/до-IPO компания"),
                           ("lighter", "lighter:strategy_index=7+ticker-table", "pre-IPO private company")):
            self.assertEqual(AC.prelisting(row(ex, "OPENAI", "OPENAI", "other", class_src=cs, note=nt))[0], 0, ex)

    def test_cs_fields_and_note_kv(self):
        self.assertEqual(AC.cs_fields("exchange:underlyingType=PREMARKET|sub=Pre-IPO+TradFi"),
                         {"underlyingType": ["PREMARKET"], "sub": ["Pre-IPO", "TradFi"]})
        self.assertEqual(AC.cs_fields("default(symbolType empty)"), {})
        self.assertEqual(AC.note_kv("region=US ticker=ANTHROPIC name=Anthropic; pre-listing (isPreListing=true; ..."),
                         {"region": ["US"], "ticker": ["ANTHROPIC"], "name": ["Anthropic"], "isPreListing": ["true"]})


class PrivateBases(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("bybit", "OPENAIUSDT", "OPENAI", "equity", status="PreLaunch"),
            row("bybit", "BPUSDT", "BP", "crypto", status="PreLaunch"),
            row("hyperliquid", "vntl:SPACEX", "SPACEX", "other", class_src="hl:perpCategories=preipo"),
            row("bingx", "NCSKBP2USD-USDT", "BP", "equity", quote="USD", class_src="ticker_list"),
            row("lighter", "SPACEX", "SPACEX", "equity", quote="USD", class_src="lighter:strategy_index=7+ticker-table"),
            row("kraken", "PF_OURAXUSD", "OURAx", "equity", quote="USD", class_src="kraken:category=Pre-IPO"),
            row("bitget", "OURAUSDT", "OURA", "other", class_src="isRwa=YES+ticker_map"),
            row("okx", "BTC-USDT-SWAP", "BTC", status="live"),
        ]
        self.pb = AC.derive_private_bases(self.rows)

    def test_seed_and_flagged(self):
        for k in AC.PRIVATE_SEED:
            self.assertIn((k, "company"), self.pb)
        self.assertIn(("OPENAI", "company"), self.pb)
        self.assertIn(("SPACEX", "company"), self.pb)          # из Hyperliquid (вне 8 минутных бирж), помечена пред-IPO
        self.assertIn(("OURA", "company"), self.pb)            # Kraken OURAx -> OURA (нормализация xStock)
        self.assertIn(("BP", "crypto"), self.pb)
        self.assertNotIn(("BP", "company"), self.pb)           # токен BP не делает приватной акцию BP plc
        self.assertNotIn(("BTC", "crypto"), self.pb)

    def test_key_excludes_everywhere_in_group(self):
        c = AC.classify(self.rows[4], self.pb)                  # Lighter SPACEX (equity) - помечена только на Hyperliquid
        self.assertEqual((c.big, c.kind, c.pre), (OUT, "private_base", 1))
        c = AC.classify(self.rows[6], self.pb)                  # Bitget OURA (other)
        self.assertEqual((c.big, c.kind), (OUT, "private_base"))
        c = AC.classify(row("bingx", "NCSKOURA2USD-USDT", "OURA", "equity", quote="USD", class_src="ticker_list"), self.pb)
        self.assertEqual((c.big, c.kind), (OUT, "private_base"))   # аудит D-01: BingX OURA был в тесте как main
        c = AC.classify(row("lighter", "OPENAI", "OPENAI", "equity", quote="USD"), self.pb)
        self.assertEqual(c.kind, "private_base")
        c = AC.classify(row("lighter", "rhOPENAI", "rhOPENAI", "equity", quote="USD"), self.pb)
        self.assertEqual(c.kind, "private_base")                   # приставка rh снимается нормализацией имени

    def test_token_flag_does_not_kill_equity_with_same_ticker(self):
        self.assertEqual(AC.classify(self.rows[1], self.pb).kind, "pre_listing")                  # Bybit BP-токен: по своему полю
        c = AC.classify(self.rows[3], self.pb)                                                     # BingX BP plc (акция)
        self.assertEqual((c.big, c.kind), ("акции+ETF", "ok"))

    def test_same_ticker_other_group_untouched(self):
        # крипта с ключом, совпадающим с приватной компанией, остаётся в тесте
        c = AC.classify(row("okx", "OURA-USDT-SWAP2", "OURA", "crypto"), self.pb)
        self.assertEqual((c.big, c.kind), ("крипта", "ok"))


class ClassifyOrderAndOther(unittest.TestCase):
    def test_priority_pre_then_private_then_dated_then_other_then_map(self):
        pb = AC.derive_private_bases([])
        self.assertEqual(AC.classify(row("bybit", "A", "A", status="PreLaunch"), pb).kind, "pre_listing")
        self.assertEqual(AC.classify(row("kraken", "FF_ETHUSD_261225", "ETH", quote="USD"), pb).kind, "dated")
        self.assertEqual(AC.classify(row("kraken", "PF_XBTUSD", "BTC", quote="USD"), pb).big, "крипта")
        self.assertEqual(AC.classify(row("okx", "AAPL-USDT-SWAP", "AAPL", "equity"), pb).big, "акции+ETF")
        self.assertEqual(AC.classify(row("okx", "XAU-USDT-SWAP", "XAU", "metal"), pb).big, "металлы+энергия")
        self.assertEqual(AC.classify(row("okx", "X", "X", "commodity"), pb).big, "металлы+энергия")
        self.assertEqual(AC.classify(row("okx", "X", "X", "fx"), pb).big, "фиат")
        self.assertEqual(AC.classify(row("okx", "X", "X", "index"), pb).big, "индексы")
        c = AC.classify(row("okx", "X", "X", "weird"), pb)
        self.assertEqual((c.big, c.kind), (OUT, "unknown_class"))

    def test_other_needs_explicit_decision(self):
        pb = AC.derive_private_bases([])
        r = row("bitget", "BNCUSDT", "BNC", "other", class_src="isRwa=YES (подкласс не установлен)")
        c = AC.classify(r, pb, {})
        self.assertEqual((c.big, c.kind), (OUT, "other_out"))
        c = AC.classify(r, pb, {("bitget", "BNCUSDT"): ("в тест", "equity", "тот же актив")})
        self.assertEqual((c.big, c.kind, c.pre), ("акции+ETF", "other_in", 0))
        c = AC.classify(r, pb, {("bitget", "BNCUSDT"): ("вне", "", "класс не назван")})
        self.assertEqual((c.big, c.kind), (OUT, "other_out"))
        # решение «в тест» без допустимого класса не работает
        c = AC.classify(r, pb, {("bitget", "BNCUSDT"): ("в тест", "other", "x")})
        self.assertEqual(c.kind, "other_out")

    def test_resolution_file_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.csv")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("exchange,symbol,decision,class_new,basis,evidence\nbitget,BNCUSDT,в тест,equity,основание,e\n")
            self.assertEqual(AC.load_resolution(p), {("bitget", "BNCUSDT"): ("в тест", "equity", "основание")})
            self.assertEqual(AC.load_resolution(os.path.join(d, "нет.csv")), {})

    def test_committed_resolution_file_is_consistent(self):
        res = AC.load_resolution()
        self.assertTrue(res, "asset_other_resolution.csv пуст или отсутствует")
        for k, (dec, cls, basis) in res.items():
            self.assertIn(dec, ("в тест", "вне"), k)
            self.assertTrue(basis, k)
            if dec == "в тест":
                self.assertIn(cls, AC.BIG, k)
            else:
                self.assertEqual(cls, "", k)


class OneFunctionForBothCallers(unittest.TestCase):
    def test_panels_p37_class_is_the_same_function(self):
        pm = load("panels_xdl9")
        pb = AC.derive_private_bases([row("bybit", "OPENAIUSDT", "OPENAI", "equity", status="PreLaunch")])
        samples = [row("bybit", "OPENAIUSDT", "OPENAI", "equity", status="PreLaunch"),
                   row("bingx", "NCSKOPENAI2USD-USDT", "OPENAI", "other", quote="USD", class_src="ticker_list"),
                   row("kraken", "FF_ETHUSD_261225", "ETH", quote="USD"), row("okx", "BTC-USDT-SWAP", "BTC"),
                   row("binance", "AAPLUSDT", "AAPL", "equity")]
        for r in samples:
            c = AC.classify(r, pb)
            self.assertEqual(pm.p37_class(r, pb), (c.big, c.pre, c.dated))
        self.assertEqual(pm.p37_class(samples[0], pb), (OUT, 1, 0))
        self.assertEqual(pm.p37_class(samples[2], pb), (OUT, 0, 1))
        self.assertEqual(pm.P37_CLASS, AC.BIG)

    def test_old_regexes_are_gone(self):
        with open(os.path.join(XD, "panels_xdl9.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("RE_PRE_IPO", src)
        with open(os.path.join(XD, "asset_lib.py"), encoding="utf-8") as fh:
            lib = fh.read()
        self.assertNotIn('"pre-IPO" in', lib)
        self.assertNotIn('"dated future" in', lib)

    def test_asset_lib_uses_it(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("нет numpy")
        al = load("asset_lib")
        self.assertIs(al.AC.classify, AC.classify)
        self.assertIs(al.norm_key, AC.norm_key)
        self.assertEqual(al.BIG, AC.BIG)
        al.PRIVATE_BASES = AC.derive_private_bases([row("bybit", "OPENAIUSDT", "OPENAI", "equity", status="PreLaunch")])
        al.RESOLUTION = {}
        self.assertEqual(al.big_class(row("bingx", "NCSKOPENAI2USD-USDT", "OPENAI", "other", quote="USD")), OUT)
        self.assertEqual(al.big_class(row("okx", "BTC-USDT-SWAP", "BTC")), "крипта")


class NormKey(unittest.TestCase):
    def test_norm_key_cases(self):
        k = lambda *a, **kw: AC.norm_key(row(*a, **kw))[0]    # noqa: E731
        self.assertEqual(k("binance", "1000SHIBUSDT", "1000SHIB"), "SHIB")
        self.assertEqual(k("bybit", "SHIB1000USDT", "SHIB1000"), "SHIB")
        self.assertEqual(k("kraken", "PF_AAPLXUSD", "AAPLx", "equity", quote="USD"), "AAPL")
        self.assertEqual(k("kraken", "PF_XBTUSD", "XBT", "crypto", quote="USD"), "XBT")
        self.assertEqual(k("kraken", "PF_SPCXXUSD", "SPCXx", "other", quote="USD", class_src="kraken:category=xStocks"), "SPCX")   # аудит: class other
        self.assertEqual(k("kraken", "PF_FOOXUSD", "FOOx", "other", quote="USD", class_src="kraken:category=Stablecoin"), "FOOX")
        self.assertEqual(k("lighter", "rhNVDA", "rhNVDA", "equity", quote="USD"), "NVDA")
        self.assertEqual(k("lighter", "AAPLUSD", "AAPLUSD", "equity", quote="USD"), "AAPL")
        self.assertEqual(k("bitget", "TENCENTHKDUSDT", "TENCENTHKD", "equity"), "TENCENT")
        self.assertEqual(k("binance", "ETHBTC", "ETH", quote="BTC"), "ETHBTC")


class InstColumnMaterializer(unittest.TestCase):
    def test_convert_roundtrip_crlf_idempotent_check(self):
        ic = load("inst_prelisting_col")
        head = "exchange,symbol,native_id,base,quote,settle,class,class_src,status,contract_size,tick_size,listed_ms,delisted_ms,funding_interval_h,taker_fee_bps,maker_fee_bps,note"
        lines = [head,
                 "bybit,OPENAIUSDT,OPENAIUSDT,OPENAI,USDT,USDT,equity,symbolType=stock,PreLaunch,1,0.01,1,,4.0,,,isPreListing=true",
                 "bybit,BTCUSDT,BTCUSDT,BTC,USDT,USDT,crypto,default,Trading,1,0.01,1,,4.0,,,"]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src", "bybit"))
            with open(os.path.join(d, "src", "bybit", "instruments.csv"), "w", encoding="utf-8", newline="") as fh:
                fh.write("\r\n".join(lines) + "\r\n")
            dst = os.path.join(d, "dst")
            n, fl, mm = ic.convert(os.path.join(d, "src", "bybit", "instruments.csv"), os.path.join(dst, "bybit.csv"))
            self.assertEqual((n, fl, mm), (2, 1, 0))
            raw = rb(os.path.join(dst, "bybit.csv")).decode("utf-8")
            self.assertTrue(raw.startswith(head + ",pre_listing,pre_listing_src\r\n"))
            self.assertIn("bybit,OPENAIUSDT,OPENAIUSDT,OPENAI,USDT,USDT,equity,symbolType=stock,PreLaunch,1,0.01,1,,4.0,,,isPreListing=true,1,bybit status=PreLaunch\r\n", raw)
            self.assertTrue(raw.endswith("Trading,1,0.01,1,,4.0,,,,0,\r\n"))
            n2, fl2, mm2 = ic.convert(os.path.join(dst, "bybit.csv"), os.path.join(dst, "again.csv"))
            self.assertEqual((n2, fl2, mm2), (2, 1, 0))
            self.assertEqual(rb(os.path.join(dst, "bybit.csv")), rb(os.path.join(dst, "again.csv")))


class RealInstruments(unittest.TestCase):
    """Сверка с реальными data/xdl9/inst (gitignored; пропуск, если нет)."""
    D = os.path.join(HERE, "..", "..", "..", "data", "xdl9", "inst")

    def rows(self):
        if not os.path.isdir(self.D):
            self.skipTest("нет data/xdl9/inst")
        out = []
        for fn in sorted(os.listdir(self.D)):
            if fn.endswith(".csv"):
                with open(os.path.join(self.D, fn), encoding="utf-8", newline="") as fh:
                    out.extend(csv.DictReader(fh))
        return out

    def test_column_matches_function_and_counts(self):
        rows = self.rows()
        if "pre_listing" not in rows[0]:
            self.skipTest("колонка pre_listing ещё не материализована")
        cnt = {}
        for r in rows:
            self.assertEqual((r["pre_listing"], r["pre_listing_src"]), tuple(str(x) for x in AC.prelisting(r)), (r["exchange"], r["symbol"]))
            cnt[r["exchange"]] = cnt.get(r["exchange"], 0) + int(r["pre_listing"])
        self.assertEqual(cnt, {"aster": 10, "binance": 5, "bingx": 0, "bitget": 0, "bybit": 7, "hyperliquid": 8, "kraken": 4, "lighter": 0, "okx": 4})

    def test_audit_seven_are_excluded(self):
        rows = self.rows()
        pb = AC.derive_private_bases(rows)
        res = AC.load_resolution()
        want = {("bybit", s) for s in ("ANTHROPICUSDT", "MOONSHOTUSDT", "OPENAIUSDT", "OURAUSDT", "POLYMARKETUSDT", "BPUSDT")} | {("bingx", "NCSKOURA2USD-USDT")}
        got = {(r["exchange"], r["symbol"]) for r in rows if AC.classify(r, pb, res).big == OUT}
        self.assertTrue(want <= got, want - got)
        # акция BP plc остаётся в тесте
        self.assertEqual(AC.classify(next(r for r in rows if r["exchange"] == "bitget" and r["symbol"] == "BPUSDT"), pb, res).big, "акции+ETF")



class RealTableConsistency(unittest.TestCase):
    """Обе вызывающие стороны (asset_lib -> asset_map.csv и panels_xdl9 -> манифест) дают один и тот же класс на реальных данных формирования.
    data/xdl9 в git не лежит (пропуск, если нет asset_map.csv)."""
    D = os.path.join(HERE, "..", "..", "..", "data", "xdl9")

    def setUp(self):
        if not os.path.exists(os.path.join(self.D, "asset_map.csv")) or not os.path.isdir(os.path.join(self.D, "inst")):
            self.skipTest("нет data/xdl9/asset_map.csv")
        self.inst = []
        for fn in sorted(os.listdir(os.path.join(self.D, "inst"))):
            with open(os.path.join(self.D, "inst", fn), encoding="utf-8", newline="") as fh:
                self.inst.extend(csv.DictReader(fh))
        self.pb = AC.derive_private_bases(self.inst)
        self.res = AC.load_resolution()
        self.pm = load("panels_xdl9")

    def test_asset_map_big_equals_panels_class(self):
        by = {(r["exchange"], r["symbol"]): r for r in self.inst}
        with open(os.path.join(self.D, "asset_map.csv"), encoding="utf-8", newline="") as fh:
            amap = list(csv.DictReader(fh))
        self.assertGreater(len(amap), 5000)
        bad = []
        for a in amap:
            r = by[(a["exchange"], a["symbol"])]
            if self.pm.p37_class(r, self.pb, self.res)[0] != a["big"]:
                bad.append((a["exchange"], a["symbol"]))
        self.assertEqual(bad, [])

    def test_counters_agree(self):
        """Единый счётчик «пред-листинг / приватная база»: panels (столбец pre_ipo) и asset_lib (kind) считают одно и то же."""
        n_pm = n_ac = 0
        for r in self.inst:
            if r["exchange"] == "hyperliquid" or not r["class"]:
                continue
            n_pm += self.pm.p37_class(r, self.pb, self.res)[1]
            n_ac += AC.classify(r, self.pb, self.res).kind in ("pre_listing", "private_base")
        self.assertEqual(n_pm, n_ac)
        self.assertEqual(n_pm, 44)           # 30 по первичным полям + 14 по ключу приватной базы (8 минутных бирж)

    def test_every_other_contract_has_explicit_fate(self):
        """Каждый контракт класса other на 8 минутных биржах: пред-листинг | приватная база | запись в asset_other_resolution.csv (в тест / вне)."""
        n = 0
        for r in self.inst:
            if r["exchange"] == "hyperliquid" or r["class"] != "other":
                continue
            c = AC.classify(r, self.pb, self.res)
            self.assertIn(c.kind, ("pre_listing", "private_base", "other_in", "other_out"), (r["exchange"], r["symbol"]))
            if c.kind in ("other_in", "other_out"):
                self.assertIn((r["exchange"], r["symbol"]), self.res, (r["exchange"], r["symbol"]))
            n += 1
        self.assertEqual(n, 74)

    def test_audit_scripts_truth_files(self):
        """Файлы суждения аудитов (other: 20 случайных; правило США: 66 помеченных + просмотренные) согласованы со своими скриптами."""
        oa = load("asset_other_audit")
        self.assertEqual(oa.SEED, 37)
        with open(oa.TRUTH, encoding="utf-8", newline="") as fh:
            tr = list(csv.DictReader(fh))
        self.assertEqual(len(tr), 20)
        self.assertTrue(all(t["truth"] in ("в тест", "вне", "неясно", "нет данных") and t["truth_basis"] for t in tr))
        ua = load("asset_us_rule_audit")
        with open(ua.TRUTH, encoding="utf-8", newline="") as fh:
            tu = list(csv.DictReader(fh))
        self.assertTrue(all(t["truth"] in ua.KNOWN + ("частная", "неизвестно") and t["truth_basis"] for t in tu))
        self.assertEqual(len({t["asset"] for t in tu}), len(tu))
        self.assertEqual(ua.agree_of("не-США", "не-США"), 1)
        self.assertEqual(ua.agree_of("США", "США (АДР)"), 1)
        self.assertEqual(ua.agree_of("не-США", "США"), 0)
        sel = os.path.join(self.D, "sel", "session_assets.csv")
        if os.path.exists(sel):
            with open(sel, encoding="utf-8", newline="") as fh:
                flagged = {r["asset_id"].split("|")[0] for r in csv.DictReader(fh) if r["decision"] == "не-США"}
            self.assertEqual(flagged - {t["asset"] for t in tu}, set())

if __name__ == "__main__":
    unittest.main(verbosity=1)
