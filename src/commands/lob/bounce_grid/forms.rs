//! Формы сделки-отскока: выход (`ExitForm`, F7), одна форма сетки
//! (`GridForm`) и построение сетки `stops × takes × DEADLINE_SECS` с осями
//! срока жизни входа, формы входа, формы выхода и «прилипания»
//! (`grid_forms*`). Вынесено из `bounce_grid` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use crate::commands::lob::backtest::{BounceForm, EntryForm, EntryTtl, StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::form_label_with_entry;

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
        }
    }

    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        if spec == "none" {
            return Ok(ExitForm::None);
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
        anyhow::bail!("--exit-form {spec:?}: ожидается none | eat<X> | gone<W>");
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
