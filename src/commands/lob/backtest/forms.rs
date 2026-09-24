//! Формы сделки-отскока по касаниям (таск 38, В-44), общие с `lob
//! bounce-grid`: стоп (`StopForm`), тейк (`TakeForm`), форма сделки одной
//! структурой (`BounceForm`), форма входа — одиночная нога или лестница
//! (`EntryForm`, `SINGLE_ENTRY_LABEL`, `ladder_legs`), форма плана CLI
//! (`PlanShape`) и режим срока жизни входа (`EntryTtl`, `ENTRY_TTL_SECS`,
//! F5, В-74). Вынесено из `backtest` при разрезке B3 (ревью 23.09), поведение
//! не менялось.

use crate::lob::strategy::{EntryLadder, MAX_ENTRY_LEGS};

// ---------------------------------------------------------------------------
// Сделка-отскока по касаниям (таск 38, В-44)
// ---------------------------------------------------------------------------

/// Форма стопа сделки-отскока — **база из файлов «отскок»** (E6, В-64/В-65)
/// плюс σ (В-62) для сравнения. Позиционные формы считаются от цены уровня
/// `P` и цены входа (первый фронтранер, иначе `P ± 1` тик). Форма, которую
/// для касания построить нельзя (нет фронтрана, нет второй плотности завала,
/// нет `σ`, стоп совпал бы со входом или лёг по ту же сторону), сигнала не
/// даёт — `bounce_plan` возвращает `None`, вызывающий считает пропуск.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum StopForm {
    /// Перед плотностью: `P + 1` тик для бида — «остопиться в плотность, а
    /// лучше перед ней за один тик» [D 12:56], «стоп я кидал перед этой
    /// крупной заявкой» [S 31:26]. Нужен вход от фронтрана дальше тика.
    Before,
    /// В плотность: `P` [D 12:56].
    At,
    /// За плотностью: `P − 1` (форма T38; у практиков — только «мёртвая
    /// монета» [D 12:20]).
    Behind,
    /// Середина фронтрана: посередине между входом и `P` (пять положений
    /// стопа, [K 18:00]); нужен фронтран дальше двух тиков.
    MidFrontrun,
    /// За второй плотностью завала: тик за `stack_next_tick` — «стоп за
    /// первую-вторую плотность» [D 13:41]; нужна вторая плотность в окне.
    BehindStack,
    /// Процент от входа: «от фронтрана до стопа 0,5–2 %» [D 06:33].
    Pct(f64),
    /// `a × σ_H` bps от входа, не ближе тика за плотностью (В-62, наша форма).
    Sigma(f64),
}

impl StopForm {
    /// Имя формы в сетке и в `runs.csv`: `before|at|behind|midfr|stack2|pct<x>|s<a>`.
    pub fn label(self) -> String {
        match self {
            Self::Before => "before".to_string(),
            Self::At => "at".to_string(),
            Self::Behind => "behind".to_string(),
            Self::MidFrontrun => "midfr".to_string(),
            Self::BehindStack => "stack2".to_string(),
            Self::Pct(x) => format!("pct{x}"),
            Self::Sigma(a) => format!("s{a}"),
        }
    }

    /// Разбор имени; неканоническое (`s1.0`, `pct01`) — отказ.
    pub fn parse(label: &str) -> anyhow::Result<Self> {
        let form = match label {
            "before" => Self::Before,
            "at" => Self::At,
            "behind" => Self::Behind,
            "midfr" => Self::MidFrontrun,
            "stack2" => Self::BehindStack,
            _ => {
                if let Some(rest) = label.strip_prefix("pct") {
                    Self::Pct(parse_mult(rest, label)?)
                } else if let Some(rest) = label.strip_prefix('s') {
                    Self::Sigma(parse_mult(rest, label)?)
                } else {
                    anyhow::bail!(
                        "{label}: форма стопа не из базы (before|at|behind|midfr|stack2|pct<x>|s<a>)"
                    )
                }
            }
        };
        anyhow::ensure!(
            form.label() == label,
            "{label}: имя формы стопа не каноническое (ожидалось {})",
            form.label()
        );
        form.validate()?;
        Ok(form)
    }

    fn validate(self) -> anyhow::Result<()> {
        match self {
            Self::Pct(x) => anyhow::ensure!(
                x.is_finite() && x > 0.0 && x < 100.0,
                "pct{x}: процент стопа обязан быть в (0, 100)"
            ),
            Self::Sigma(a) => anyhow::ensure!(
                a.is_finite() && a >= 0.0,
                "s{a}: множитель σ стопа — конечное число ≥ 0"
            ),
            _ => {}
        }
        Ok(())
    }
}

/// Форма тейка: `1:1` от входа — единственная общая у практиков [D 16:17]
/// («тейк частями» — E7, после стопов), `b × σ_H` (В-62) или **трейл**
/// (владелец 19.09: «что трейла нет — это плохо»): активируется при ходе
/// `activate_pct` % от входа в плюс, дальше тянется на `trail_pct` % от лучшей
/// цены; фиксированного тейка у трейла нет (`strategy`: при `trail_bps > 0`
/// тейк-лимит не работает).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TakeForm {
    OneToOne,
    /// Тейк в процентах **от входа**, независимо от стопа (S8 плана по
    /// сторонам, 20.09): у лонгов от старых стен 83 выхода из 89 — по часу,
    /// тейк 1:1 при стопе 2 % недостижим; `tk0.5` — +0.5 % от входа в
    /// сторону отскока.
    Pct(f64),
    Sigma(f64),
    Trail {
        activate_pct: f64,
        trail_pct: f64,
    },
    /// E7 «частями» (Z 1:07:37 «половина на середине хода»): половина
    /// позиции лимитом на 1:1, остаток — до стопа, дедлайна, трейла (если
    /// задан флагами) или съедания; второй раз на 1:1 не закрывается.
    HalfOneToOne,
    /// E5/E7 по чужим ботам (`density-bots-2026-09-19.md`, их дефолты
    /// 50/80): тейк 1:1 целиком, плюс выход по съеданию плотности от
    /// максимума с входа — `half_pct` % → половина по рынку, `all_pct` % →
    /// весь остаток по рынку. Имя `eat<half>x<all>`.
    Eaten {
        half_pct: f64,
        all_pct: f64,
    },
}

impl TakeForm {
    /// Имя: `1to1`, `t<b>` или `tr<активация>x<откат>` в процентах (`tr0.5x0.3`).
    pub fn label(self) -> String {
        match self {
            Self::OneToOne => "1to1".to_string(),
            Self::Pct(x) => format!("tk{x}"),
            Self::Sigma(b) => format!("t{b}"),
            Self::Trail {
                activate_pct,
                trail_pct,
            } => format!("tr{activate_pct}x{trail_pct}"),
            Self::HalfOneToOne => "half1to1".to_string(),
            Self::Eaten { half_pct, all_pct } => format!("eat{half_pct}x{all_pct}"),
        }
    }

    pub fn parse(label: &str) -> anyhow::Result<Self> {
        let form = if label == "1to1" {
            Self::OneToOne
        } else if label == "half1to1" {
            Self::HalfOneToOne
        } else if let Some(rest) = label.strip_prefix("eat") {
            let (h, a) = rest
                .split_once('x')
                .ok_or_else(|| anyhow::anyhow!("{label}: съедание — eat<половина %>x<всё %>"))?;
            let half_pct = parse_mult(h, label)?;
            let all_pct = parse_mult(a, label)?;
            anyhow::ensure!(
                half_pct.is_finite()
                    && all_pct.is_finite()
                    && half_pct > 0.0
                    && half_pct < all_pct
                    && all_pct <= 100.0,
                "{label}: пороги съедания — 0 < половина < всё ≤ 100 %"
            );
            Self::Eaten { half_pct, all_pct }
        } else if let Some(rest) = label.strip_prefix("tk") {
            let x = parse_mult(rest, label)?;
            anyhow::ensure!(
                x.is_finite() && x > 0.0 && x < 100.0,
                "{label}: процент тейка обязан быть в (0, 100)"
            );
            Self::Pct(x)
        } else if let Some(rest) = label.strip_prefix("tr") {
            let (a, t) = rest
                .split_once('x')
                .ok_or_else(|| anyhow::anyhow!("{label}: трейл — tr<активация>x<откат>"))?;
            let activate_pct = parse_mult(a, label)?;
            let trail_pct = parse_mult(t, label)?;
            anyhow::ensure!(
                activate_pct.is_finite()
                    && activate_pct > 0.0
                    && trail_pct.is_finite()
                    && trail_pct > 0.0,
                "{label}: активация и откат трейла — конечные числа > 0"
            );
            Self::Trail {
                activate_pct,
                trail_pct,
            }
        } else if let Some(rest) = label.strip_prefix('t') {
            Self::Sigma(parse_mult(rest, label)?)
        } else {
            anyhow::bail!(
                "{label}: форма тейка не из базы (1to1|tk<x>|half1to1|eat<h>x<a>|t<b>|tr<a>x<t>)"
            )
        };
        anyhow::ensure!(
            form.label() == label,
            "{label}: имя формы тейка не каноническое (ожидалось {})",
            form.label()
        );
        if let Self::Sigma(b) = form {
            anyhow::ensure!(
                b.is_finite() && b >= 0.0,
                "t{b}: множитель σ тейка — конечное число ≥ 0"
            );
        }
        Ok(form)
    }
}

fn parse_mult(rest: &str, label: &str) -> anyhow::Result<f64> {
    rest.parse::<f64>()
        .map_err(|_| anyhow::anyhow!("{label}: число после префикса не разобралось"))
}

/// Форма сделки одной структурой: стоп, тейк и пол σ-тейка в кругах комиссий
/// (`take_floor_fees × ROUNDTRIP_FEES_BPS`; у `1to1` пола нет — как в файлах).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BounceForm {
    pub stop: StopForm,
    pub take: TakeForm,
    pub take_floor_fees: Option<f64>,
}

impl BounceForm {
    /// Сборка из имён CLI с проверкой: σ-тейк требует пол, пол — число > 0.
    pub fn parse(stop: &str, take: &str, take_floor_fees: Option<f64>) -> anyhow::Result<Self> {
        let form = Self {
            stop: StopForm::parse(stop)?,
            take: TakeForm::parse(take)?,
            take_floor_fees,
        };
        form.validate()?;
        Ok(form)
    }

    pub fn validate(self) -> anyhow::Result<()> {
        self.stop.validate()?;
        if let TakeForm::Sigma(b) = self.take {
            anyhow::ensure!(
                b.is_finite() && b >= 0.0,
                "t{b}: множитель σ тейка — конечное число ≥ 0"
            );
        }
        if let Some(k) = self.take_floor_fees {
            anyhow::ensure!(
                k.is_finite() && k > 0.0,
                "--take-floor-fees {k}: пол тейка — конечное число > 0 кругов комиссий"
            );
        }
        if matches!(self.take, TakeForm::Sigma(_)) {
            anyhow::ensure!(
                self.take_floor_fees.is_some(),
                "тейк {} требует --take-floor-fees (пол в кругах комиссий, В-62)",
                self.take.label()
            );
        }
        Ok(())
    }

    /// Нужна ли форме `σ_H` касания.
    pub fn needs_sigma(self) -> bool {
        matches!(self.stop, StopForm::Sigma(_)) || matches!(self.take, TakeForm::Sigma(_))
    }
}

/// Расстояние в bps от цены `entry_tick` (в тиках) — в целых тиках, не
/// меньше одного: `ceil(bps / 10⁴ × entry_tick)`. Округление вверх — чтобы
/// расстояние было **не меньше** заданного (стоп не ближе `a × σ`, тейк не
/// ниже `b × σ`), а цена всегда стояла на сетке тиков.
#[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
pub(super) fn bps_to_ticks_ceil(bps: f64, entry_tick: i64) -> i64 {
    let ticks = (bps / 10_000.0 * entry_tick as f64).ceil();
    (ticks as i64).max(1)
}

/// Имя прежнего входа (F6): одиночная нога у фронтранера касания, иначе
/// `P ± 1` тик. Трёхпольные имена форм (`<стоп>-<тейк>-<H>`, В-65) читаются как
/// `single@fr-…`, а сетка с этой формой входа пишет **прежнее** трёхпольное
/// имя — на нём стоит гейт «те же круги» (F3/F4/F5).
pub const SINGLE_ENTRY_LABEL: &str = "single@fr";

/// Форма входа (F6 этапа F, В-73): нога у фронтранера или **лестница** от
/// `from` до `to` bps над стеной (для аска — под). Числа `N`, `from`, `to`, `w`
/// задаёт предрегистрация **именем формы**; умолчаний в коде нет.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum EntryForm {
    /// `single@fr` — прежний вход: цена фронтранера касания, иначе `P ± 1`
    /// тик (T38, В-65). Гейт «те же круги».
    SingleFrontrun,
    /// `ladder<N>x<from>..<to>[w<k>]` — `N` ног от `from` до `to` bps над
    /// стеной равными долями (`$ / N`), либо **вес к стене** `k`: нижняя
    /// (ближайшая к стене) нога весит `k` долей — цитата практика «основной
    /// объём к сайзу» [T 1:31:42].
    Ladder {
        legs: u8,
        from_bps: f64,
        to_bps: f64,
        wall_weight: u32,
    },
}

impl EntryForm {
    /// Разбор имени формы. Каноничность проверяется как у `StopForm`/
    /// `TakeForm`: `ladder03x02..10` или `…w1` — не имя, а другая запись того
    /// же (и «попробовать ещё одно» здесь было бы лишним испытанием).
    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        let spec = spec.trim();
        if spec == SINGLE_ENTRY_LABEL {
            return Ok(Self::SingleFrontrun);
        }
        let rest = spec.strip_prefix("ladder").ok_or_else(|| {
            anyhow::anyhow!(
                "{spec}: форма входа — {SINGLE_ENTRY_LABEL} или ladder<N>x<from>..<to>[w<k>]"
            )
        })?;
        let (legs_s, tail) = rest
            .split_once('x')
            .ok_or_else(|| anyhow::anyhow!("{spec}: лестница — ladder<N>x<from>..<to>[w<k>]"))?;
        let legs: u8 = legs_s
            .parse()
            .map_err(|_| anyhow::anyhow!("{spec}: число ног {legs_s:?} не разобралось"))?;
        anyhow::ensure!(
            (2..=MAX_ENTRY_LEGS as u8).contains(&legs),
            "{spec}: ног {legs}, а сетка имён — от 2 до {MAX_ENTRY_LEGS}"
        );
        let (range, weight_s) = match tail.split_once('w') {
            Some((r, w)) => (r, Some(w)),
            None => (tail, None),
        };
        let wall_weight: u32 = match weight_s {
            Some(w) => w
                .parse()
                .map_err(|_| anyhow::anyhow!("{spec}: вес {w:?} не разобрался"))?,
            None => 1,
        };
        anyhow::ensure!(
            wall_weight >= 1,
            "{spec}: вес к стене — целое ≥ 1 (1 — равные доли)"
        );
        let (from_s, to_s) = range
            .split_once("..")
            .ok_or_else(|| anyhow::anyhow!("{spec}: полоса лестницы — <from>..<to> bps"))?;
        let from_bps: f64 = from_s
            .parse()
            .map_err(|_| anyhow::anyhow!("{spec}: from {from_s:?} не число"))?;
        let to_bps: f64 = to_s
            .parse()
            .map_err(|_| anyhow::anyhow!("{spec}: to {to_s:?} не число"))?;
        anyhow::ensure!(
            from_bps.is_finite() && to_bps.is_finite() && from_bps > 0.0 && to_bps > from_bps,
            "{spec}: полоса лестницы — конечные 0 < from < to bps"
        );
        let form = Self::Ladder {
            legs,
            from_bps,
            to_bps,
            wall_weight,
        };
        anyhow::ensure!(
            form.label() == spec,
            "{spec}: имя формы входа не каноническое (ожидалось {})",
            form.label()
        );
        Ok(form)
    }

    /// Имя формы: `single@fr` или `ladder3x2..10` / `ladder3x2..10w2`. Вес 1
    /// не пишется: равные доли — то же самое, а имя обязано быть одно.
    pub fn label(self) -> String {
        match self {
            Self::SingleFrontrun => SINGLE_ENTRY_LABEL.to_string(),
            Self::Ladder {
                legs,
                from_bps,
                to_bps,
                wall_weight,
            } => {
                let base = format!("ladder{legs}x{}..{}", fmt_bps(from_bps), fmt_bps(to_bps));
                if wall_weight > 1 {
                    format!("{base}w{wall_weight}")
                } else {
                    base
                }
            }
        }
    }
}

/// Число bps в имени формы — как печатает `f64::to_string` (кратчайшая запись,
/// читаемая обратно тем же числом): `2`, `0.5`, `10`.
fn fmt_bps(v: f64) -> String {
    format!("{v}")
}

/// Ноги лестницы формы (F6, В-73): `N` цен от `from` до `to` bps **над
/// стеной** (для аска — под, `away` = −1), каждая — целым числом тиков по
/// `bps_to_ticks_ceil` (расстояние не меньше заданного, цена на сетке тиков).
/// Совпавшие тики складываются в одну ногу с суммарной долей: биржа не
/// различает две заявки по одной цене, а доля — то, что видит позиция. Доли —
/// `$/N`, либо нижняя (ближайшая к стене) нога весит `wall_weight` долей.
///
/// Второе значение — средняя цена входа в тиках (по долям): от неё
/// `bounce_plan` считает стоп и тейк формы, как для одиночного входа от цены
/// фронтранера.
#[allow(clippy::cast_precision_loss)]
pub(super) fn ladder_legs(
    p_tick: i64,
    away: i64,
    legs: u8,
    from_bps: f64,
    to_bps: f64,
    wall_weight: u32,
) -> (EntryLadder, i64) {
    let n = usize::from(legs.max(2));
    let mut out = EntryLadder::NONE;
    // Доли: нижняя нога — `wall_weight`, остальные — по единице; сумма долей
    // нормируется на единицу.
    let total = wall_weight as f64 + (n - 1) as f64;
    let mut prev_tick: Option<i64> = None;
    for i in 0..n {
        let bps = from_bps + (to_bps - from_bps) * i as f64 / (n - 1) as f64;
        let tick = p_tick + away * bps_to_ticks_ceil(bps, p_tick);
        let frac = if i == 0 {
            wall_weight as f64 / total
        } else {
            1.0 / total
        };
        // Тики монотонны по `i` (`bps_to_ticks_ceil` не убывает), поэтому
        // совпавшие идут подряд: складываем долю в предыдущую ногу.
        if prev_tick == Some(tick) {
            let last = usize::from(out.n - 1);
            out.frac[last] += frac;
        } else {
            assert!(out.push(tick, frac), "ног не больше MAX_ENTRY_LEGS");
            prev_tick = Some(tick);
        }
    }
    // Средняя цена входа в тиках — ближайший тик к взвешенной сумме: цены
    // живут на сетке тиков (так же округляет `level_shift` стратегии).
    let avg = out.weighted_avg_tick().round() as i64;
    (out, avg)
}

/// Форма сделки, приходящая из CLI (`--post-only`, `--trail-*`, `--grid-*`),
/// одной структурой: у `bounce_plan` иначе стало бы восемь аргументов, а clippy
/// держит предел семи — тот же приём, что у `Session` в `bybit::conn`, где
/// аргументы собраны по смыслу, а не подогнаны под счётчик.
#[derive(Debug, Clone, Copy)]
pub(crate) struct PlanShape {
    /// Шаг лота в единицах крейта (`step_e9 / 1e9`): размер плотности на
    /// сигнале в план (E7 съедание) и округление дробного выхода.
    pub(crate) lot: f64,
    pub(crate) post_only: bool,
    pub(crate) trail_bps: f64,
    pub(crate) trail_activate_bps: f64,
    pub(crate) grid_legs: u8,
    pub(crate) grid_step_ticks: i64,
    /// Форма входа (F6, В-73): прежняя одиночная нога у фронтранера
    /// (`single@fr`, гейт «те же круги») или лестница `ladder<N>x<from>..<to>`
    /// с весом к стене. Числа формы — из её имени (предрегистрация).
    pub(crate) entry_form: EntryForm,
    /// Дедлайн сделки в наносекундах (B3): из предрегистрированной сетки
    /// В-58, приходит из `--deadline-secs`.
    pub(crate) deadline_ns: i64,
    /// Досрочный выход в наносекундах (B4): `0` — выключен, иначе `X` из
    /// набора {1, 2, 3} секунд.
    pub(crate) early_exit_ns: i64,
    /// Режим срока жизни входа (F5, В-74): `touch` — прежний «до конца
    /// касания», секунды — потолок из сетки замера, `wall` — только условия
    /// рынка без потолка. Режим `touch` выключает оба условия F5 — на нём
    /// стоит гейт «те же круги».
    pub(crate) entry_ttl: EntryTtl,
    /// Номинал порога В-66 в долларах (`--h3-usd`, режимы `notional`/`both`):
    /// из него `bounce_plan` считает `level_floor_qty = usd / цена уровня` —
    /// порог «стена снята» (F5, В-74). `None` — условия F5 без порога:
    /// вызывающий обязан отказать (умолчания у числа нет).
    pub(crate) h3_usd: Option<f64>,
    /// Полоса ухода цены, bps (F5, В-74): число замера, у сетки — флаг
    /// `--band-exit-bps`; `0` — условие выключено (режим `touch`).
    pub(crate) band_exit_bps: f64,
    /// Форма выхода (F7, Б-75): `none` — прежнее поведение, `eat<X>` — стена
    /// съедена сделками ≥ X %, `gone<W>` — стена снята без сделок.
    pub(crate) exit_form: crate::commands::lob::bounce_grid::ExitForm,
}

/// Режим срока жизни входа (F5, В-74): у сделки-отскока вход снимается по
/// событиям рынка (стена снята / цена ушла из полосы), а `entry_ttl` — только
/// предохранительный потолок. Прежний режим «до конца касания» остался
/// значением `Touch` — на нём стоит гейт «те же круги».
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryTtl {
    /// Прежний режим: вход живёт до конца касания (`entry_ttl_ns` = его
    /// длительность), условия F5 выключены.
    Touch,
    /// Только условия F5, без потолка-таймера (потолок — `i64::MAX`).
    Wall,
    /// Потолок срока жизни в секундах — из сетки замера В-74
    /// `ENTRY_TTL_SECS` {60, 300, 1800}.
    Secs(i64),
}

/// Предрегистрированная сетка потолков срока жизни входа, секунды (F5,
/// В-74): «предохранительный потолок — сетка замера {60, 300, 1800} с, не
/// решение». Числа — не умолчание команды (умолчания в коде нет), а
/// разрешённые значения `--entry-ttl-secs`.
pub const ENTRY_TTL_SECS: [i64; 3] = [60, 300, 1_800];

impl EntryTtl {
    /// Разбор значения `--entry-ttl-secs`: `touch` | `wall` | секунды из
    /// сетки В-74. Чужое число — отказ, а не молчаливое расширение сетки
    /// (тем же правилом, что `--deadline-secs`).
    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        match spec.trim() {
            "touch" => Ok(Self::Touch),
            "wall" => Ok(Self::Wall),
            other => {
                let secs: i64 = other.parse().map_err(|_| {
                    anyhow::anyhow!(
                        "--entry-ttl-secs {other:?}: ожидалось `touch`, `wall` или секунды"
                    )
                })?;
                anyhow::ensure!(
                    ENTRY_TTL_SECS.contains(&secs),
                    "--entry-ttl-secs {secs}: не из сетки замера В-74 {ENTRY_TTL_SECS:?}"
                );
                Ok(Self::Secs(secs))
            }
        }
    }

    /// Имя значения для шапки артефакта и имени формы.
    pub fn label(self) -> String {
        match self {
            Self::Touch => "touch".to_string(),
            Self::Wall => "wall".to_string(),
            Self::Secs(s) => s.to_string(),
        }
    }

    /// Порядок форм в сетке: `touch`, `wall`, затем секунды по возрастанию —
    /// тот же приём детерминированного порядка, что у дедлайнов.
    pub(crate) fn sort_key(self) -> (u8, i64) {
        match self {
            Self::Touch => (0, 0),
            Self::Wall => (1, 0),
            Self::Secs(s) => (2, s),
        }
    }
}
