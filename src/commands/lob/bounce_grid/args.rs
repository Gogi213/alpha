//! Аргументы `lob bounce-grid` (`BounceGridArgs`), разбор осей сетки из строк
//! (`parse_early_exits`/`parse_entry_ttls`/`parse_entry_forms`/`parse_exit_forms`),
//! перечисления `--side`/`--driver`/`--signal` и итог прогона
//! (`BounceGridSummary`). Вынесено из `bounce_grid` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use std::path::PathBuf;

use clap::Args;

use crate::book::Side;
use crate::commands::lob::backtest::{early_exit_ns_from_secs, EntryForm, EntryTtl};
use crate::commands::lob::H3Args;
use crate::lob::backtest::{ExecLatency, QueueModelKind};

use super::forms::ExitForm;
use super::sets::SetPaths;

/// Разбор повторяемого `--early-exit-secs` (аудит дизайна 22.09, В-85): пусто — ось
/// выключена (`[None]`, прежнее поведение); `off` — форма без выхода по «прилипанию» рядом с
/// включёнными; число — секунды из предрегистрированного набора В-58 (`EARLY_EXITS_S`).
/// Порядок — порядок флагов, повторы свёрнуты.
pub(crate) fn parse_early_exits(specs: &[String]) -> anyhow::Result<Vec<Option<i64>>> {
    if specs.is_empty() {
        return Ok(vec![None]);
    }
    let mut out: Vec<Option<i64>> = Vec::with_capacity(specs.len());
    for spec in specs {
        let v = if spec == "off" {
            None
        } else {
            let x: i64 = spec.parse().map_err(|_| {
                anyhow::anyhow!("--early-exit-secs {spec:?}: ожидается off или целые секунды")
            })?;
            early_exit_ns_from_secs(Some(x))?;
            Some(x)
        };
        if !out.contains(&v) {
            out.push(v);
        }
    }
    Ok(out)
}

/// Разбор повторяемого `--entry-ttl-secs` (F5, В-74): пусто — прежний режим
/// `touch`; значения — `touch` | `wall` | секунды из сетки замера В-74.
/// Порядок детерминирован (как у дедлайнов), повторы свёрнуты — иначе формы
/// сетки получили бы одинаковые имена и `run_bounce_grid` отказал бы.
pub(crate) fn parse_entry_ttls(specs: &[String]) -> anyhow::Result<Vec<EntryTtl>> {
    if specs.is_empty() {
        return Ok(vec![EntryTtl::Touch]);
    }
    let mut out = Vec::with_capacity(specs.len());
    for spec in specs {
        out.push(EntryTtl::parse(spec)?);
    }
    out.sort_by_key(|t| t.sort_key());
    out.dedup();
    Ok(out)
}

/// Разбор повторяемого `--entry-form` (F6, В-73): пусто — прежний вход
/// `single@fr` (гейт «те же круги»); иначе `single@fr` и/или
/// `ladder<N>x<from>..<to>[w<k>]`. Порядок — порядок флагов (он же порядок
/// осей сетки), повторы свёрнуты: иначе формы получили бы одинаковые имена и
/// `run_bounce_grid` отказал бы. Числа форм — из имён (предрегистрация),
/// умолчаний нет; пустой список — единственное исключение и оно прежнее
/// поведение команды.
pub(crate) fn parse_entry_forms(specs: &[String]) -> anyhow::Result<Vec<EntryForm>> {
    if specs.is_empty() {
        return Ok(vec![EntryForm::SingleFrontrun]);
    }
    let mut out: Vec<EntryForm> = Vec::with_capacity(specs.len());
    for spec in specs {
        let form = EntryForm::parse(spec)?;
        if !out.contains(&form) {
            out.push(form);
        }
    }
    Ok(out)
}

/// Разбор повторяемого `--exit-form` (F7, Б-75): пусто — прежний выход
/// `none` (гейт «те же круги»); иначе `none` и/или `eat<X>` / `gone<W>`.
/// Порядок — порядок флагов (он же порядок осей сетки), повторы свёрнуты.
/// Числа — из предрегистрации, умолчаний в коде нет.
pub(crate) fn parse_exit_forms(specs: &[String]) -> anyhow::Result<Vec<ExitForm>> {
    if specs.is_empty() {
        return Ok(vec![ExitForm::None]);
    }
    let mut out: Vec<ExitForm> = Vec::with_capacity(specs.len());
    for spec in specs {
        let form = ExitForm::parse(spec)?;
        if !out.contains(&form) {
            out.push(form);
        }
    }
    Ok(out)
}

/// Обязательные спутники условий F5 (В-74): «стена снята» без порога В-66
/// (`--h3-usd`) не проверить, а «цена ушла из полосы» без числа замера
/// (`--band-exit-bps`) — изобретённое число. Отказ, не молчаливый пропуск.
pub(super) fn ensure_entry_conditions_args(
    active: bool,
    h3_usd: Option<f64>,
    band_exit_bps: Option<f64>,
) -> anyhow::Result<()> {
    if !active {
        return Ok(());
    }
    anyhow::ensure!(
        h3_usd.is_some(),
        "условие «стена снята» (F5, В-74) требует --h3-usd: порог В-66 в лотах крейта — --h3-usd / цена"
    );
    anyhow::ensure!(
        band_exit_bps.is_some(),
        "условие «цена ушла из полосы» (F5, В-74) требует --band-exit-bps: полоса — число замера, умолчания нет"
    );
    Ok(())
}

#[derive(Debug, Args)]
pub struct BounceGridArgs {
    /// Корень записи (`<SYMBOL>-<день>[-pN].binlog`, `instruments.csv`,
    /// маркеры `verify-<SYMBOL>.status`).
    #[arg(long)]
    pub root: PathBuf,
    /// Символы (повторяемый флаг); пусто — весь пул `instruments.csv` корня.
    #[arg(long = "symbol")]
    pub symbols: Vec<String>,
    /// Сутки UTC `YYYY-MM-DD` (повторяемый флаг); пусто — все сутки записи.
    #[arg(long = "day")]
    pub days: Vec<String>,
    /// Задержка исполнения, нс: одно число (В-37, `assumed`) или тройка
    /// `place=..,cancel=..,taker=..` (В-68, измерено `lob latency`); обязателен.
    #[arg(long)]
    pub median_rtt_ns: ExecLatency,
    #[arg(long)]
    pub p95_rtt_ns: ExecLatency,
    /// Модель очереди и исполнения (F3 плана 2026-09-20, В-78):
    /// `risk-adverse` — прежний движок (`RiskAdverseQueueModel` +
    /// `NoPartialFillExchange`, числа прогонов не меняются) или `prob:<n>` —
    /// модель очереди по объёму (`ProbQueueModel<PowerProbQueueFunc(n)>` +
    /// `PartialFillExchange`, вход исполняется частично). **Обязательный
    /// флаг, умолчания в коде нет**: `n` — число предрегистрации, у крейта в
    /// примерах 3.0 — это не наше умолчание. Модель идёт в шапку
    /// `forms.csv`/`manifest.txt` (`queue=…`).
    #[arg(long = "queue-model")]
    pub queue_model: QueueModelKind,
    /// Лот в e9 — либо он, либо `--order-qty-from-pool`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
    /// Лот — `order_size_22a` от полей пула и цены **каждого** касания (R2).
    #[arg(long, default_value_t = false)]
    pub order_qty_from_pool: bool,
    /// Лот под номинал в долларах (владелец 20.09): `floor(usd / цена касания /
    /// шаг) × шаг`, не меньше лота 22а — модель очереди исполняет реальный
    /// размер; взаимоисключающе с `--order-qty-e9` и `--order-qty-from-pool`.
    #[arg(long)]
    pub order_usd: Option<f64>,
    /// Множитель лота (E7, дробный выход): позиция в `N` лотов пула, чтобы
    /// половину было чем выходить — с одним минимальным лотом половина
    /// округляется в ноль и формы `half1to1`/`eat<h>x<a>` выходят целиком.
    /// `1` — как было.
    #[arg(long, default_value_t = 1)]
    pub order_qty_mult: u32,
    /// Вход пост-онли (`GTX`) — решение владельца 20.09 (В-72): тейкерский
    /// вход в момент касания не наша сделка, поэтому **умолчание —
    /// включён**, и явный флаг нужен только для полноты записи в
    /// `manifest.txt`. Выключить можно лишь `--no-post-only` — это режим гейта
    /// «те же круги»: прежние `rounds.csv`/`forms.csv` сняты до F4, когда
    /// вход был обычным лимитом (`GTC`).
    #[arg(long, overrides_with = "no_post_only", default_value_t = false)]
    pub post_only: bool,
    /// Выключить пост-онли входа (режим гейта «те же круги», В-72) — вход
    /// обычным лимитом `GTC`, тейкером там, где пересекает спред. Флаги
    /// взаимоисключающие: применённый последним выигрывает.
    #[arg(
        long = "no-post-only",
        overrides_with = "post_only",
        default_value_t = false
    )]
    pub no_post_only: bool,
    /// Формы стопа базы (повторяемый флаг; В-65, умолчаний нет):
    /// `before|at|behind|midfr|stack2|pct<x>|s<a>`.
    #[arg(long = "stop-form", required = true)]
    pub stop_form: Vec<String>,
    /// Формы тейка (повторяемый флаг): `1to1` и/или `t<b>`.
    #[arg(long = "take-form", required = true)]
    pub take_form: Vec<String>,
    /// Пол σ-тейка в кругах комиссий (`costs::ROUNDTRIP_FEES_BPS`); нужен
    /// только формам `t<b>`.
    #[arg(long)]
    pub take_floor_fees: Option<f64>,
    /// Режим срока жизни входа (F5, В-74) — повторяемый флаг, формы сетки —
    /// декартово произведение: `touch` — прежний «до конца касания» (условия
    /// F5 выключены, гейт «те же круги»), `wall` — только условия рынка без
    /// потолка, либо секунды из сетки замера В-74 {60, 300, 1800}. Пусто —
    /// `touch`. С условиями (кроме `touch`) обязательны `--h3-usd` (порог
    /// «стена снята») и `--band-exit-bps`.
    #[arg(long = "entry-ttl-secs")]
    pub entry_ttl_secs: Vec<String>,
    /// Полоса ухода цены, bps (F5, В-74): лучшая цена нашей стороны дальше
    /// дальней ноги лестницы больше чем на это число — вход снимается. Число
    /// замера (`2·D`, полосу `D` выбирает предрегистрация F10), **умолчания в
    /// коде нет**: с `--entry-ttl-secs` кроме `touch` флаг обязателен.
    #[arg(long = "band-exit-bps")]
    pub band_exit_bps: Option<f64>,
    /// E3 базы: входить только от фронтрана — касания без `frontrun_tick`
    /// пропускаются у всех форм («впритык — редко» [S 07:37; D 05:18]).
    #[arg(long, default_value_t = false)]
    pub frontrun_only: bool,
    /// Главный фильтр базы (владелец 19.09): плотность стоит не меньше `N` с к
    /// моменту касания («стоит ≥ 1 ч» [D 01:57; K 14:35]). Число — владельца.
    #[arg(long)]
    pub min_age_secs: Option<i64>,
    /// Сила «×поток» не ниже `S` % в момент касания: размер плотности /
    /// оборот монеты за последний час (определение владельца 19.09, как у
    /// сторонних сканеров). Без оборота за час — пропуск.
    #[arg(long)]
    pub min_flow_pct: Option<f64>,
    /// Ось стороны (этап 1 `alpha-roadmap-2026-09-19.md`, намёк
    /// `side-asymmetry-2026-09-19.md`: лонги от бид-стен +12…+17 bps на
    /// круг, шорты от аск-стен −16…−19): гнать только касания бид- (`bid`,
    /// лонг) или аск-стен (`ask`, шорт). Без флага — обе стороны, как прежде.
    #[arg(long, value_enum)]
    pub side: Option<SideArg>,
    /// Касания из CSV вместо реплея книги: `<dir>/<сутки>/touches-<SYMBOL>.csv`
    /// (раскладка ночного H3, `study/touches/`) или `<dir>/touches-<SYMBOL>.csv`
    /// (одна папка на всю запись, строки по `day_utc`). Порог плотности и
    /// трекер обязаны быть теми же, что у `lob touches` (проверить нечем —
    /// шапки у CSV нет; стандарт — `--h3-mode notional --h3-usd 10000`,
    /// умолчания прогрева/окна повтора). Символ, у которого в кэше нет всех
    /// суток корня, идёт реплеем (со счётчиком и строкой в stderr).
    /// С `--signal approach` здесь читаются `approaches-<SYMBOL>.csv` (F1).
    #[arg(long)]
    pub touches_from: Option<PathBuf>,
    /// Сигнал входа (F6 этапа F, В-73): `touch` — касание (умолчание — гейт
    /// «те же круги»), `approach` — запись подхода F1: лимитка ставится на
    /// взводе (`t0 = arm_ms`) и живёт до касания/снятия/потолка, а не с
    /// касания. `approach` требует кэша `--touches-from` с
    /// `approaches-<SYMBOL>.csv`: реплей подходов сетка не считает, полосу `D`
    /// задаёт прогон `lob touches --approach-bps`.
    #[arg(long, value_enum, default_value_t = SignalArg::Touch)]
    pub signal: SignalArg,
    /// Форма входа (F6 этапа F, В-73) — повторяемый флаг, ось сетки:
    /// `single@fr` (умолчание — прежний вход у фронтранера, гейт «те же
    /// круги») или `ladder<N>x<from>..<to>[w<k>]` — `N` ног от `from` до `to`
    /// bps над стеной (для аска — под), равными долями либо весом нижней ноги
    /// `k`. Числа — из имени формы (предрегистрация), умолчаний нет.
    #[arg(long = "entry-form")]
    pub entry_form: Vec<String>,
    /// Форма выхода (F7 этапа F, Б-75) — повторяемый флаг, ось сетки:
    /// `none` (умолчание — прежнее поведение, гейт «те же круги»), `eat<X>`
    /// (стена съедена сделками ≥ X %) или `gone<W>` (стена снята без сделок,
    /// размер < (1−W %) от входа, съедение < половины падения). Числа — из
    /// предрегистрации, умолчаний в коде нет.
    #[arg(long = "exit-form")]
    pub exit_form: Vec<String>,
    /// Выход по «прилипанию» (B4, В-58 п. 5; ось сетки — аудит дизайна 22.09, В-85) —
    /// повторяемый флаг: `off` или секунды из предрегистрированного набора В-58 (1, 2, 3).
    /// Без флага ось выключена — прежние формы и имена (гейт «те же круги»).
    #[arg(long = "early-exit-secs")]
    pub early_exit_secs: Vec<String>,
    /// Набор фильтров касаний одним процессом (повторяемый): `<имя>:<k=v,…>`,
    /// ключи `age=<с>` (возраст ≥, как `--min-age-secs`), `flow=<%>` (сила
    /// ×поток ≥, как `--min-flow-pct`), `side=bid|ask`, `frontrun` (только
    /// от фронтрана); пустой список ключей — без фильтров (`name:`). Артефакты
    /// набора — `<out-dir>/<имя>/`; имя — буквы, цифры, `.`, `_`, `-`. С
    /// `--set` флаги `--min-age-secs/--min-flow-pct/--side/--frontrun-only`
    /// у команды — отказ.
    #[arg(long = "set")]
    pub sets: Vec<String>,
    /// Режим по минутам для ключей `pool*`/`btc*` наборов (S3/S4 плана по
    /// сторонам): каталог `study/regime` с `<сутки>.csv` от `regime.py`.
    /// Без него эти ключи — отказ; сутки без файла — отказ.
    #[arg(long)]
    pub regime_from: Option<PathBuf>,
    /// Дедлайны сетки, секунды (повторяемый; умолчание — В-58 `60 600 3600
    /// 7200`); разрешены ещё `1800` и `14400` (S8, предрегистрация 20.09) —
    /// иное число отказ.
    #[arg(long = "deadline-secs")]
    pub deadline_secs: Vec<u64>,
    #[command(flatten)]
    pub h3: H3Args,
    #[arg(long)]
    pub h3_k: Option<f64>,
    #[arg(long)]
    pub warmup_ms: Option<i64>,
    #[arg(long)]
    pub repeat_window_ms: Option<i64>,
    /// Потоков на сетку (умолчание — число ядер).
    #[arg(long)]
    pub threads: Option<usize>,
    /// Драйвер: `setups` — биржа только от касания до конца круга (умолчание,
    /// решение владельца 2026-09-18); `full` — сплошной прогон суток на форму,
    /// эталон для гейта «побайтово те же круги».
    #[arg(long, value_enum, default_value_t = DriverArg::Setups)]
    pub driver: DriverArg,
    /// Память кругов (G10, владелец 2026-09-23: «ускорить бэктест без потерь»): круг формы, уже
    /// посчитанный для одного набора, остальные наборы берут готовым — итог побайтово тот же.
    /// `off` — прежний счёт каждого набора с нуля (гейт «те же байты»). Только `--driver setups`.
    #[arg(long = "round-memo", default_value = "on", value_parser = ["on", "off"])]
    pub round_memo: String,
    /// Каталог артефактов (`rounds.csv`, `forms.csv`, `manifest.txt`).
    #[arg(long)]
    pub out_dir: PathBuf,
    /// Снять требование маркера сверки (отладочные данные; в `runs.csv` не идёт).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
    /// Переносить возраст уровней через смежную полночь в реплее касаний (аудит дизайна 22.09
    /// Т3; как `lob touches --carry-age`): кэш, посчитанный с переносом, и реплей обязаны
    /// совпадать по режиму. Без флага — прежние байты.
    #[arg(long, default_value_t = false)]
    pub carry_age: bool,
    /// Кэш касаний (`--touches-from`) — единственный источник: сутки символа без файла кэша
    /// пропускаются, реплея книги нет (22.09: монета со сверкой `ok` только за последние сутки
    /// уходила в реплей всех суток корня — ~4 ГБ на процесс и OOM на деке; кэш ночи строится по
    /// маркерам K1 **своих** суток, поэтому пропуск — это и есть K1 по суткам). Без флага — прежнее
    /// поведение (кэш неполон — реплей).
    #[arg(long, default_value_t = false)]
    pub touches_cache_only: bool,
    /// Перенос круга через полночь (измерено на кандидате 23.09: без него теряется 35 из 189
    /// круга истории и 8 из 43 записи — круг, ещё открытый на конце суток D, молча падает из
    /// `rounds.csv`/P&L как `EndOfData`, а у живого бота полуночи нет — расхождение бэктеста с
    /// ботом). Значение — корень со **всеми** сутками записи (`root/`, не студийный `--root
    /// study/root-<день>` с одними символьными ссылками дня D): части суток D+1 читаются им же
    /// резолвером, что и собственный корень (`session_parts_for`), и только в окне времени после
    /// полуночи D+1 (`entry-ttl` формы + наибольший `--deadline-secs` сетки + запас `p95`
    /// тейкера) — не всей записью (тот же риск OOM, что уже решает `day_events`). Сигналы дня —
    /// только его собственные касания; довесок несёт лишь события книги/сделок, дочитывающие уже
    /// открытые круги. Части D+1 не требуют маркера сверки (K1 здесь не про новые сигналы), но
    /// `forms.csv` отмечает их отсутствие (`carry_unverified`). Нет частей D+1 (последние сутки
    /// записи) — поведение прежнее (`incomplete`). Без флага — байт в байт прежний вывод: колонки
    /// `n_carried`/`carry_unverified` в шапке `forms.csv` не пишутся вовсе (33, не 35), не только
    /// печатаются нулями (R6, ревью 23.09 — до исправления `e5c8847` их писал безусловно, гейт
    /// «те же байты», `gate-g10.sh`, был сломан для всех, кто флаг не передавал).
    #[arg(long)]
    pub carry_root: Option<PathBuf>,
}

impl BounceGridArgs {
    /// Пост-онли входа у планов прогона (В-72): умолчание — **включён**,
    /// `--no-post-only` — режим гейта «те же круги». Разрешение флагов здесь,
    /// а не в `run_bounce_grid`: `clap` уже свёл пару `--post-only` /
    /// `--no-post-only` к «последний выигрывает».
    pub fn entry_post_only(&self) -> bool {
        !self.no_post_only
    }
}

/// Сторона стены для `--side`: `bid` — лонг от бид-стены, `ask` — шорт от аск-стены.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum SideArg {
    Bid,
    Ask,
}

impl SideArg {
    pub fn label(self) -> &'static str {
        match self {
            SideArg::Bid => "bid",
            SideArg::Ask => "ask",
        }
    }
}

impl From<SideArg> for Side {
    fn from(s: SideArg) -> Self {
        match s {
            SideArg::Bid => Side::Bid,
            SideArg::Ask => Side::Ask,
        }
    }
}

/// Как гонять форму над сутками: сплошным прогоном или по сетапам.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum DriverArg {
    Full,
    Setups,
}

/// По какому сигналу входит сделка (F6 этапа F, В-73): касание (прежнее,
/// гейт) или запись подхода F1 (лимитка ставится заранее, на взводе).
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum SignalArg {
    Touch,
    Approach,
}

impl SignalArg {
    pub fn label(self) -> &'static str {
        match self {
            Self::Touch => "touch",
            Self::Approach => "approach",
        }
    }
}

impl DriverArg {
    pub fn label(self) -> &'static str {
        match self {
            DriverArg::Full => "full",
            DriverArg::Setups => "setups",
        }
    }
}

/// Итог прогона — то, что печатает `lob bounce-grid`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BounceGridSummary {
    pub forms: usize,
    pub symbols_done: usize,
    pub symbols_skipped_unverified: usize,
    pub symbols_without_touches: usize,
    /// Символов, чьи касания пришли из кэша `--touches-from` (остальные — реплей).
    pub symbols_from_cache: usize,
    pub symbol_days: usize,
    pub rounds: u64,
    /// Пути первого (или единственного) набора — как раньше.
    pub rounds_path: PathBuf,
    pub forms_path: PathBuf,
    /// Пути каждого набора `--set` (имя, `rounds.csv`, `forms.csv`); без
    /// `--set` — один элемент с пустым именем.
    pub sets: Vec<SetPaths>,
}
