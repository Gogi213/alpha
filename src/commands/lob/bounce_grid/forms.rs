//! Формы сделки-отскока: выход (`ExitForm`, F7), одна форма сетки
//! (`GridForm`) и построение сетки `stops × takes × DEADLINE_SECS` с осями
//! срока жизни входа, формы входа, формы выхода и «прилипания»
//! (`grid_forms*`). Вынесено из `bounce_grid` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use crate::commands::lob::backtest::{BounceForm, EntryForm, EntryTtl, StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::form_label_with_entry;
pub use crate::lob::strategy::WallEatMode;
use crate::lob::strategy::{BtcMinutes, WallStopMode};

/// Форма выхода (F7 этапа F, Б-75): как закрывать позицию.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ExitForm {
    /// `none` — как сейчас (стоп/тейк/дедлайн/трейл/съедание от максимума).
    None,
    /// `eat<X>` — стена съедена сделками: накопленные сделки в стену ≥ X % от
    /// размера стены на входе. Выход по рынку всего остатка.
    Eat { pct: f64 },
    /// `gone<W>` — стена снята без сделок: размер упал ниже (1 − W %) от
    /// размера на входе, И съедение сделками < половины падения.
    Gone { pct: f64 },
    /// `gone<W>tr<T>` — то же снятие, но позиция не закрывается сразу: взводится трейл,
    /// выход — откат на `T` % (от входа) от лучшей цены после снятия (владелец 2026-09-23:
    /// «сразу выход по снятию — глупость, но снятие — это уже риск, нужна защита»).
    GoneTrail { pct: f64, trail_pct: f64 },
    /// `gone<W>be` / `gone<W>bex` — снятие переносит стоп в безубыток (мягко: когда позиция у
    /// безубытка; жёстко: позиция хуже безубытка закрывается сразу), дальше базовый трейл плана
    /// (владелец 2026-09-23: «снятие — стоп в ноль, дальше базовый трейлинг»).
    GoneBe { pct: f64, hard: bool },
    /// `gone<W>wall<B>` / `gone<W>wallx<B>` / `gone<W>wallk<B>` — снятие переносит стоп на уровень
    /// стены с буфером `B` bps за ним (`0` — ровно на уровне); стоп только поднимается. Если
    /// снятие застало позицию хуже нового стопа: `wall` — перенос, когда цена вернётся к нему;
    /// `wallx` — выход сразу; `wallk` — стоп плана до конца сделки (`WallStopMode`). Трейл и
    /// дедлайн плана — как обычно (владелец 2026-09-26: «не безубыток а стоп на то место где
    /// была плотность»; «оба варианта мягкого режима прогнать»).
    GoneWall {
        pct: f64,
        mode: WallStopMode,
        buffer_bps: f64,
    },
    /// `weat<X>s<W>{m|l|a}<Y>` (TK-014, Г-55 правилом выхода, владелец 28.09): после входа за окно
    /// `W` с по цене стены исполнено ≥ `X` % наибольшего видимого размера стены в том же окне;
    /// режим `m` — выход, только если BTC за окно ≤ −`Y` bps, `l` — только если BTC > −`Y` bps,
    /// `a` — при любом BTC. Дизайн — `docs/findings/tk014-design-2026-09-28.md`.
    WallEat {
        pct: f64,
        secs: u32,
        mode: WallEatMode,
        btc_bps: f64,
        /// Ряд BTC (`--btc-minutes`), подставляется после разбора; в имени не участвует.
        btc: Option<&'static BtcMinutes>,
    },
    /// `pyeat<N>` (TK-065, Г-94): вход частями — стена делится на `N` долей, добавка `Q0/N` на
    /// съедании каждой из первых `N − 1` (spec r2-spec §1; `pyeat3` = «трети»).
    PyrEat { parts: u8 },
}

impl ExitForm {
    /// Имя формы несёт число ровно тем, чем считали (`eat33.7`, не `eat34`) —
    /// как `pct{x}`/`tk{x}` у стопа и тейка: имя в `forms.csv`/`runs.csv` и
    /// порог предрегистрации обязаны совпадать (ревью 21.09).
    pub fn label(&self) -> String {
        match self {
            ExitForm::None => "none".to_string(),
            ExitForm::Eat { pct } => format!("eat{pct}"),
            ExitForm::Gone { pct } => format!("gone{pct}"),
            ExitForm::GoneTrail { pct, trail_pct } => format!("gone{pct}tr{trail_pct}"),
            ExitForm::GoneBe { pct, hard } => {
                format!("gone{pct}be{}", if *hard { "x" } else { "" })
            }
            ExitForm::GoneWall {
                pct,
                mode,
                buffer_bps,
            } => {
                let m = match mode {
                    WallStopMode::Soft => "",
                    WallStopMode::Hard => "x",
                    WallStopMode::Keep => "k",
                };
                format!("gone{pct}wall{m}{buffer_bps}")
            }
            ExitForm::PyrEat { parts } => format!("pyeat{parts}"),
            ExitForm::WallEat {
                pct,
                secs,
                mode,
                btc_bps,
                ..
            } => {
                let m = match mode {
                    WallEatMode::Market => "m",
                    WallEatMode::Local => "l",
                    WallEatMode::Any => "a",
                };
                format!("weat{pct}s{secs}{m}{btc_bps}")
            }
        }
    }

    /// Та же форма с подставленным рядом BTC (только у `weat*`).
    pub fn with_btc(self, series: &'static BtcMinutes) -> Self {
        match self {
            ExitForm::WallEat {
                pct,
                secs,
                mode,
                btc_bps,
                ..
            } => ExitForm::WallEat {
                pct,
                secs,
                mode,
                btc_bps,
                btc: Some(series),
            },
            other => other,
        }
    }

    /// Форма `weat*` (TK-014) — ей нужен ряд BTC (`--btc-minutes`).
    pub fn needs_btc(&self) -> bool {
        matches!(self, ExitForm::WallEat { .. })
    }

    fn parse_wall_eat(spec: &str, rest: &str) -> anyhow::Result<Self> {
        let bad = || {
            anyhow::anyhow!(
                "--exit-form {spec:?}: ожидается weat<X>s<W>{{m|l|a}}<Y> (X — % в (0, 100], W — с в [1, 3600], Y — bps ≥ 0)"
            )
        };
        let (x, tail) = rest.split_once('s').ok_or_else(bad)?;
        let pos = tail.find(['m', 'l', 'a']).ok_or_else(bad)?;
        let (w, tail) = tail.split_at(pos);
        let mode = match &tail[..1] {
            "m" => WallEatMode::Market,
            "l" => WallEatMode::Local,
            _ => WallEatMode::Any,
        };
        let y = &tail[1..];
        let pct: f64 = x.parse().map_err(|_| bad())?;
        let secs: u32 = w.parse().map_err(|_| bad())?;
        let btc_bps: f64 = y.parse().map_err(|_| bad())?;
        anyhow::ensure!(
            pct.is_finite() && pct > 0.0 && pct <= 100.0,
            "weat<X>: X ∈ (0, 100]"
        );
        anyhow::ensure!((1..=3600).contains(&secs), "weat<X>s<W>: W ∈ [1, 3600] с");
        anyhow::ensure!(
            btc_bps.is_finite() && btc_bps >= 0.0,
            "weat…<Y>: Y — bps ≥ 0"
        );
        let form = ExitForm::WallEat {
            pct,
            secs,
            mode,
            btc_bps,
            btc: None,
        };
        anyhow::ensure!(
            form.label() == spec,
            "--exit-form {spec:?}: имя не каноническое (ожидалось {})",
            form.label()
        );
        Ok(form)
    }

    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        if spec == "none" {
            return Ok(ExitForm::None);
        }
        if let Some(rest) = spec.strip_prefix("pyeat") {
            let parts: u8 = rest.parse().map_err(|_| {
                anyhow::anyhow!("--exit-form {spec:?}: ожидается pyeat<N>, N ∈ [2, 10]")
            })?;
            anyhow::ensure!((2..=10).contains(&parts), "pyeat<N>: N ∈ [2, 10]");
            let form = ExitForm::PyrEat { parts };
            anyhow::ensure!(
                form.label() == spec,
                "--exit-form {spec:?}: имя не каноническое (ожидалось {})",
                form.label()
            );
            return Ok(form);
        }
        if let Some(rest) = spec.strip_prefix("weat") {
            return Self::parse_wall_eat(spec, rest);
        }
        if let Some(rest) = spec.strip_prefix("eat") {
            let pct: f64 = rest.parse()?;
            anyhow::ensure!(
                pct.is_finite() && pct > 0.0 && pct <= 100.0,
                "eat<X>: X ∈ (0, 100]"
            );
            return Ok(ExitForm::Eat { pct });
        }
        if let Some(rest) = spec.strip_prefix("gone") {
            if let Some((w, tail)) = rest.split_once("wall") {
                let pct: f64 = w.parse()?;
                anyhow::ensure!(
                    pct.is_finite() && pct > 0.0 && pct <= 100.0,
                    "gone<W>wall<B>: W ∈ (0, 100]"
                );
                let (mode, b) = if let Some(b) = tail.strip_prefix('x') {
                    (WallStopMode::Hard, b)
                } else if let Some(b) = tail.strip_prefix('k') {
                    (WallStopMode::Keep, b)
                } else {
                    (WallStopMode::Soft, tail)
                };
                let buffer_bps: f64 = b.parse().map_err(|_| {
                    anyhow::anyhow!(
                        "--exit-form {spec:?}: ожидается gone<W>wall<B>, gone<W>wallx<B> или gone<W>wallk<B> (B — bps)"
                    )
                })?;
                anyhow::ensure!(
                    buffer_bps.is_finite() && (0.0..10_000.0).contains(&buffer_bps),
                    "gone<W>wall<B>: буфер B — bps за стеной в [0, 10000)"
                );
                let form = ExitForm::GoneWall {
                    pct,
                    mode,
                    buffer_bps,
                };
                anyhow::ensure!(
                    form.label() == spec,
                    "--exit-form {spec:?}: имя не каноническое (ожидалось {})",
                    form.label()
                );
                return Ok(form);
            }
            if let Some((w, mode)) = rest.split_once("be") {
                let pct: f64 = w.parse()?;
                anyhow::ensure!(
                    pct.is_finite() && pct > 0.0 && pct <= 100.0,
                    "gone<W>be: W ∈ (0, 100]"
                );
                let hard = match mode {
                    "" => false,
                    "x" => true,
                    _ => anyhow::bail!("--exit-form {spec:?}: ожидается gone<W>be или gone<W>bex"),
                };
                let form = ExitForm::GoneBe { pct, hard };
                anyhow::ensure!(
                    form.label() == spec,
                    "--exit-form {spec:?}: имя не каноническое (ожидалось {})",
                    form.label()
                );
                return Ok(form);
            }
            if let Some((w, t)) = rest.split_once("tr") {
                let pct: f64 = w.parse()?;
                let trail_pct: f64 = t.parse()?;
                anyhow::ensure!(
                    pct.is_finite() && pct > 0.0 && pct <= 100.0,
                    "gone<W>tr<T>: W ∈ (0, 100]"
                );
                anyhow::ensure!(
                    trail_pct.is_finite() && trail_pct > 0.0 && trail_pct < 100.0,
                    "gone<W>tr<T>: откат T — % от входа в (0, 100)"
                );
                let form = ExitForm::GoneTrail { pct, trail_pct };
                anyhow::ensure!(
                    form.label() == spec,
                    "--exit-form {spec:?}: имя не каноническое (ожидалось {})",
                    form.label()
                );
                return Ok(form);
            }
            let pct: f64 = rest.parse()?;
            anyhow::ensure!(
                pct.is_finite() && pct > 0.0 && pct <= 100.0,
                "gone<W>: W ∈ (0, 100]"
            );
            return Ok(ExitForm::Gone { pct });
        }
        anyhow::bail!(
            "--exit-form {spec:?}: ожидается none | eat<X> | gone<W> | weat<X>s<W>{{m|l|a}}<Y>"
        );
    }
}

/// Одна форма сетки (В-65): имя колонки, форма сделки, дедлайн (он же окно `σ`),
/// режим срока жизни входа (F5, В-74), форма входа (F6, В-73) и форма выхода (F7, Б-75).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GridForm {
    pub label: &'static str,
    pub form: BounceForm,
    pub deadline_secs: i64,
    /// Режим срока жизни входа (F5, В-74): `touch` — прежний, секунды/`wall` —
    /// условия рынка плюс потолок. Имя формы у нетронутого режима не меняется
    /// (`form_label`), у остальных несёт суффикс `-ttl<значение>` — имена
    /// обязаны различаться, иначе `run_bounce_grid` отказывает на повторе.
    pub entry_ttl: EntryTtl,
    /// Форма входа (F6, В-73): `single@fr` — прежнее имя формы без поля входа
    /// (гейт «те же круги»), лестница — поле `<вход>` первым в имени.
    pub entry_form: EntryForm,
    /// Форма выхода (F7, Б-75): `none` (прежнее поведение), `eat<X>` или `gone<W>`.
    pub exit_form: ExitForm,
    /// Выход по «прилипанию» (B4, В-58 п. 5; ось сетки — аудит дизайна 22.09 §6 п. 2, В-85):
    /// `None` — выключен (прежнее поведение и имена), `Some(x)` — через `x` с после входа
    /// уровень всё ещё лучшая цена → выход по рынку (`ExitReason::Early`). Значения — только из
    /// предрегистрированного набора В-58 (`EARLY_EXITS_S`).
    pub early_exit_secs: Option<i64>,
}

/// Формы сетки в порядке `stops × takes × DEADLINE_SECS`; имена —
/// `bounce_verdict::form_label`, их же читает вердикт. Повторы имён отвергает
/// `run_bounce_grid`. Это — прежний режим входа (F5, В-74): `touch`,
/// прежняя форма выхода (F7, Б-75): `none`.
pub fn grid_forms(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
) -> Vec<GridForm> {
    grid_forms_with_entry_ttl(
        stops,
        takes,
        take_floor_fees,
        deadlines,
        &[EntryTtl::Touch],
        &[ExitForm::None],
    )
}

/// Та же сетка, но с осями срока жизни входа (F5, В-74) и формы выхода
/// (F7, Б-75): значения `entry_ttl`/`exit_form` — **внутренние** множители,
/// поэтому при `&[EntryTtl::Touch]` и `&[ExitForm::None]` порядок
/// и имена форм те же, что у `grid_forms` (гейт «те же круги»).
pub fn grid_forms_with_entry_ttl(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
    ttls: &[EntryTtl],
    exits: &[ExitForm],
) -> Vec<GridForm> {
    grid_forms_with_axes(
        stops,
        takes,
        take_floor_fees,
        deadlines,
        ttls,
        &[EntryForm::SingleFrontrun],
        exits,
    )
}

/// Полная сетка F7 (Б-75): к осям F6 добавлена ось **формы выхода**
/// (`--exit-form`, повторяемый). Форма выхода — самый внешний множитель, поэтому
/// при `&[ExitForm::None]` порядок и имена форм те же, что у
/// `grid_forms_with_axes` (гейт «те же круги»): у `none` поле выхода в
/// имени не пишется (`form_label_with_entry`).
pub fn grid_forms_with_axes(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
    ttls: &[EntryTtl],
    entries: &[EntryForm],
    exits: &[ExitForm],
) -> Vec<GridForm> {
    let mut out = Vec::with_capacity(
        exits.len() * entries.len() * stops.len() * takes.len() * deadlines.len() * ttls.len(),
    );
    for &exit_form in exits {
        for &entry_form in entries {
            for &stop in stops {
                for &take in takes {
                    for &deadline in deadlines {
                        let base = form_label_with_entry(
                            &entry_form.label(),
                            &stop.label(),
                            &take.label(),
                            deadline,
                        );
                        for &entry_ttl in ttls {
                            let label_with_ttl: &'static str = match entry_ttl {
                                // Прежний режим — прежнее имя: вердикт и «золото» F3/F4
                                // читают `form_label` как есть.
                                EntryTtl::Touch => Box::leak(base.clone().into_boxed_str()),
                                other => Box::leak(
                                    format!("{base}-ttl{}", other.label()).into_boxed_str(),
                                ),
                            };
                            let label = if matches!(exit_form, ExitForm::None) {
                                label_with_ttl
                            } else {
                                Box::leak(
                                    format!("{label_with_ttl}-{}", exit_form.label())
                                        .into_boxed_str(),
                                )
                            };
                            out.push(GridForm {
                                label,
                                form: BounceForm {
                                    stop,
                                    take,
                                    take_floor_fees,
                                },
                                deadline_secs: deadline as i64,
                                entry_ttl,
                                entry_form,
                                exit_form,
                                early_exit_secs: None,
                            });
                        }
                    }
                }
            }
        }
    }
    out
}

/// Ось выхода по «прилипанию» (аудит дизайна 22.09, В-85) поверх полной сетки F7 — самый
/// внешний множитель: при `&[None]` порядок и имена форм те же, что у `grid_forms_with_axes`
/// (гейт «те же круги»); включённое значение добавляет к имени хвост `-early<x>`.
#[allow(clippy::too_many_arguments)]
pub fn grid_forms_with_early(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
    ttls: &[EntryTtl],
    entries: &[EntryForm],
    exits: &[ExitForm],
    earlies: &[Option<i64>],
) -> Vec<GridForm> {
    let base = grid_forms_with_axes(
        stops,
        takes,
        take_floor_fees,
        deadlines,
        ttls,
        entries,
        exits,
    );
    let mut out = Vec::with_capacity(base.len() * earlies.len());
    for &early in earlies {
        for f in &base {
            let mut g = *f;
            if let Some(x) = early {
                g.label = Box::leak(format!("{}-early{x}", f.label).into_boxed_str());
                g.early_exit_secs = Some(x);
            }
            out.push(g);
        }
    }
    out
}
