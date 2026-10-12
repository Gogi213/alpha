//! Каталог механик (В-227, пакет A): имя формы ⇄ параметры ⇄ домены — одно место.
//! Холодный путь: читается при проверке имён и командой `lob mechanics --json`
//! (`cellhash.py`/`master.py` берут шаблоны и домены отсюда, а не копируют). Горячий путь
//! (`match ExitForm` в стратегии) не знает про каталог.
//!
//! Сейчас в каталоге формы выхода (`ExitForm`), стоп (`StopForm`) тейк (`TakeForm`, кроме
//! `eat<h>x<a>`) и вход (`EntryForm`, из лестниц — только σ-лестница Г-95); фильтры переносятся следующими шагами пакета A. Строка = одна каноническая запись имени (`name_template`); у формы с
//! несколькими записями (`pyre…u…` / `pyre…w…`, `wall2`/`wall2x`/`wall2m<n>`) строк несколько,
//! `id` общий (`ExitForm::variant_id`).

use super::super::backtest::{EARLY_EXITS_S, EARLY_EXIT_HALF_MS, ENTRY_TTL_SECS};
use serde_json::{json, Value};

/// Домен параметра имени. `None` у верхней границы — без верхнего предела.
#[derive(Debug, Clone, Copy)]
pub enum Dom {
    Int {
        lo: i64,
        hi: Option<i64>,
        /// Внутри `[lo, hi]`, но не каноническое имя (это другая запись той же формы).
        excl: &'static [i64],
    },
    Real {
        lo: f64,
        hi: Option<f64>,
        lo_open: bool,
        hi_open: bool,
    },
    /// Предрегистрированный набор значений (флаги прогона): берётся из констант Rust, не копируется.
    Set(fn() -> Vec<f64>),
}

#[derive(Debug, Clone, Copy)]
pub struct ParamSpec {
    pub name: &'static str,
    pub dom: Dom,
}

#[derive(Debug, Clone, Copy)]
pub struct MechSpec {
    /// Класс механики: `exit` (`ExitForm`), `stop` (`StopForm`), `take` (`TakeForm`), `entry` (`EntryForm`).
    pub kind: &'static str,
    /// Вариант формы (общий у записей одной формы); у стопа/тейка — с префиксом `Stop`/`Take`.
    pub id: &'static str,
    /// Начало имени до первого параметра.
    pub prefix: &'static str,
    /// Каноническое имя с `{param}`.
    pub name_template: &'static str,
    pub params: &'static [ParamSpec],
    /// Только в бинарнике с `--features r2`.
    pub is_r2: bool,
}

const fn int(name: &'static str, lo: i64, hi: Option<i64>, excl: &'static [i64]) -> ParamSpec {
    ParamSpec {
        name,
        dom: Dom::Int { lo, hi, excl },
    }
}

const fn real(
    name: &'static str,
    lo: f64,
    hi: Option<f64>,
    lo_open: bool,
    hi_open: bool,
) -> ParamSpec {
    ParamSpec {
        name,
        dom: Dom::Real {
            lo,
            hi,
            lo_open,
            hi_open,
        },
    }
}

const fn row(
    id: &'static str,
    prefix: &'static str,
    name_template: &'static str,
    params: &'static [ParamSpec],
    is_r2: bool,
) -> MechSpec {
    MechSpec {
        kind: "exit",
        id,
        prefix,
        name_template,
        params,
        is_r2,
    }
}

/// Запись флага прогона (`--entry-ttl-secs`, `--early-exit-secs`).
const fn flag(
    id: &'static str,
    prefix: &'static str,
    name_template: &'static str,
    params: &'static [ParamSpec],
) -> MechSpec {
    MechSpec {
        kind: "flag",
        id,
        prefix,
        name_template,
        params,
        is_r2: false,
    }
}

/// Запись стопа (`StopForm`, `--stop`).
const fn stop(
    id: &'static str,
    prefix: &'static str,
    name_template: &'static str,
    params: &'static [ParamSpec],
) -> MechSpec {
    MechSpec {
        kind: "stop",
        ..row(id, prefix, name_template, params, false)
    }
}

/// Запись тейка (`TakeForm`, `--take`).
const fn take(
    id: &'static str,
    prefix: &'static str,
    name_template: &'static str,
    params: &'static [ParamSpec],
) -> MechSpec {
    MechSpec {
        kind: "take",
        ..row(id, prefix, name_template, params, false)
    }
}

/// Запись формы входа (`EntryForm`, `--entry-form`).
const fn entry(
    id: &'static str,
    prefix: &'static str,
    name_template: &'static str,
    params: &'static [ParamSpec],
) -> MechSpec {
    MechSpec {
        kind: "entry",
        ..row(id, prefix, name_template, params, false)
    }
}

const PCT: ParamSpec = real("pct", 0.0, Some(100.0), true, false);
const SECS: ParamSpec = int("secs", 1, Some(3600), &[]);
const BTC: ParamSpec = real("btc_bps", 0.0, None, false, false);
const BUF: ParamSpec = real("buffer_bps", 0.0, Some(10_000.0), false, true);
const U12_W: ParamSpec = int("u12", 1, Some(12), &[4, 8, 12]);
const U3: ParamSpec = int("u3", 1, Some(3), &[]);
const Q8_G: ParamSpec = int("q8", 1, Some(7), &[2, 4, 6]);
const WALL2_N: ParamSpec = int("n", 2, Some(3), &[]);
const TAPE_W: ParamSpec = int("w", 1, None, &[]);

/// `--entry-ttl-secs`: сетка `ENTRY_TTL_SECS` (forms.rs).
fn entry_ttl_secs() -> Vec<f64> {
    ENTRY_TTL_SECS.iter().map(|&s| s as f64).collect()
}

/// `--early-exit-secs`: 0,5 с + `EARLY_EXITS_S` (plan.rs).
fn early_exit_secs() -> Vec<f64> {
    std::iter::once(EARLY_EXIT_HALF_MS as f64 / 1_000.0)
        .chain(EARLY_EXITS_S.iter().map(|&s| s as f64))
        .collect()
}

const TTL_SECS: ParamSpec = ParamSpec {
    name: "secs",
    dom: Dom::Set(entry_ttl_secs),
};
const EARLY_SECS: ParamSpec = ParamSpec {
    name: "x",
    dom: Dom::Set(early_exit_secs),
};
const TAPE_Q: ParamSpec = real("q", 0.0, None, true, false);
const PYRE_N: ParamSpec = int("n", 1, Some(10), &[]);
const PYNW_K: ParamSpec = int("k", 1, Some(9), &[]);

/// Все формы выхода. Порядок на разбор не влияет (`ExitForm::parse_any` прежний).
pub static MECHANICS: &[MechSpec] = &[
    stop("StopBefore", "before", "before", &[]),
    stop("StopAt", "at", "at", &[]),
    stop("StopBehind", "behind", "behind", &[]),
    stop("StopMidFrontrun", "midfr", "midfr", &[]),
    stop("StopBehindStack", "stack2", "stack2", &[]),
    stop(
        "StopPct",
        "pct",
        "pct{x}",
        &[real("x", 0.0, Some(100.0), true, true)],
    ),
    stop(
        "StopSigma",
        "s",
        "s{a}",
        &[real("a", 0.0, None, false, false)],
    ),
    take("Take1to1", "1to1", "1to1", &[]),
    take("TakeHalf1to1", "half1to1", "half1to1", &[]),
    take(
        "TakePct",
        "tk",
        "tk{x}",
        &[real("x", 0.0, Some(100.0), true, true)],
    ),
    take(
        "TakeSigma",
        "t",
        "t{b}",
        &[real("b", 0.0, None, false, false)],
    ),
    take(
        "TakeTrail",
        "tr",
        "tr{a}x{t}",
        &[
            real("a", 0.0, None, true, false),
            real("t", 0.0, None, true, false),
        ],
    ),
    entry("EntrySingle", "single@fr", "single@fr", &[]),
    entry("EntryMarket", "market", "market", &[]),
    entry(
        "EntryOffsetPlus",
        "single@fr+",
        "single@fr+{n}",
        &[int("n", 1, None, &[])],
    ),
    entry(
        "EntryOffsetMinus",
        "single@fr_",
        "single@fr_{n}",
        &[int("n", 1, None, &[])],
    ),
    // σ-лестница Г-95 (EntryForm::LadderSigma, 3 ноги 0..0.0409σ), вес к стене w ≥ 2 (w = 1 — без суффикса, другое имя).
    // Прочие `ladder…`: связь from < to — в каталог позже.
    entry(
        "EntryLadderSigma3",
        "ladder",
        "ladder3x0..0.0409sw{w}",
        &[int("w", 2, None, &[])],
    ),
    // `eat<h>x<a>` (TakeForm::Eaten): связь 0 < h < a ≤ 100 между параметрами — в каталог позже.
    row("None", "none", "none", &[], false),
    row("Eat", "eat", "eat{pct}", &[PCT], false),
    row("Gone", "gone", "gone{pct}", &[PCT], false),
    row(
        "GoneTrail",
        "gone",
        "gone{pct}tr{trail_pct}",
        &[PCT, real("trail_pct", 0.0, Some(100.0), true, true)],
        false,
    ),
    row("GoneBe", "gone", "gone{pct}be", &[PCT], false),
    row("GoneBe", "gone", "gone{pct}bex", &[PCT], false),
    row(
        "GoneWall",
        "gone",
        "gone{pct}wall{buffer_bps}",
        &[PCT, BUF],
        false,
    ),
    row(
        "GoneWall",
        "gone",
        "gone{pct}wallx{buffer_bps}",
        &[PCT, BUF],
        false,
    ),
    row(
        "GoneWall",
        "gone",
        "gone{pct}wallk{buffer_bps}",
        &[PCT, BUF],
        false,
    ),
    row(
        "WallEat",
        "weat",
        "weat{pct}s{secs}m{btc_bps}",
        &[PCT, SECS, BTC],
        false,
    ),
    row(
        "WallEat",
        "weat",
        "weat{pct}s{secs}l{btc_bps}",
        &[PCT, SECS, BTC],
        false,
    ),
    row(
        "WallEat",
        "weat",
        "weat{pct}s{secs}a{btc_bps}",
        &[PCT, SECS, BTC],
        false,
    ),
    row(
        "PyrEat",
        "pyeat",
        "pyeat{parts}",
        &[int("parts", 2, Some(10), &[])],
        true,
    ),
    row(
        "PyrFresh",
        "pyfresh",
        "pyfresh{parts}",
        &[int("parts", 2, Some(12), &[])],
        true,
    ),
    row("PyrReinstall", "pyre", "pyre{n}u{u3}", &[PYRE_N, U3], true),
    row(
        "PyrReinstall",
        "pyre",
        "pyre{n}w{u12}",
        &[PYRE_N, U12_W],
        true,
    ),
    row("PyrNewWall", "pynw", "pynw{k}u{u3}", &[PYNW_K, U3], true),
    row(
        "PyrNewWall",
        "pynw",
        "pynw{k}w{u12}",
        &[PYNW_K, U12_W],
        true,
    ),
    row("HalfStop", "halfstop", "halfstop", &[], true),
    row("HalfStop", "halfstop", "halfstopf1", &[], true),
    row("HalfStop", "halfstop", "halfstopf3", &[], true),
    row("HalfStop", "halfstop", "halfstopg{q8}", &[Q8_G], true),
    row("HalfLevel", "halflevel", "halflevel", &[], true),
    row("HalfLevel", "halflevel", "halflevelf1", &[], true),
    row("HalfLevel", "halflevel", "halflevelf3", &[], true),
    row("HalfLevel", "halflevel", "halflevelg{q8}", &[Q8_G], true),
    row(
        "TakeSched",
        "tsl",
        "tsl{g10}t{t4}",
        &[int("g10", 1, Some(99), &[]), int("t4", 1, Some(4), &[])],
        true,
    ),
    row(
        "TakeSchedX",
        "tslx",
        "tslx{g8}t{t16}",
        &[int("g8", 1, Some(64), &[]), int("t16", 1, Some(16), &[])],
        true,
    ),
    row(
        "Converge",
        "conv",
        "conv{tol}a{a_bps}",
        &[int("tol", 0, Some(16), &[]), int("a_bps", 1, None, &[])],
        true,
    ),
    row(
        "NoStop",
        "nostop",
        "nostop{x2}",
        &[int("x2", 1, Some(4), &[3])],
        true,
    ),
    row(
        "NoStop",
        "nostop",
        "nostopx{x8}",
        &[int("x8", 1, Some(64), &[4, 8, 16])],
        true,
    ),
    row("Wall2", "wall2", "wall2", &[], true),
    row("Wall2", "wall2", "wall2x", &[], true),
    row("Wall2", "wall2", "wall20", &[], true),
    row("Wall2", "wall2", "wall2m{n}", &[WALL2_N], true),
    row("Wall2", "wall2", "wall2p{n}", &[WALL2_N], true),
    row(
        "Chase",
        "chase",
        "chase{ms}",
        &[int("ms", 1, None, &[])],
        true,
    ),
    row("Tape", "tape", "tape{w}q{q}", &[TAPE_W, TAPE_Q], true),
    row("Tape", "tape", "tape{w}qp", &[TAPE_W], true),
    row("Cxl", "cxl", "cxl{w}q{q}", &[TAPE_W, TAPE_Q], true),
    row("Cxl", "cxl", "cxl{w}qp", &[TAPE_W], true),
    // Флаги прогона (хвост имени формы клетки): потолок жизни входа и досрочный выход; домены — константы Rust.
    flag("EntryTtl", "ttl", "ttl{secs}", &[TTL_SECS]),
    flag("EarlyExit", "early", "early{x}", &[EARLY_SECS]),
];

impl Dom {
    /// Точки домена для проверки круга имён: границы (закрытые) и внутренние, без исключений.
    pub fn sample(&self) -> Vec<String> {
        match *self {
            Dom::Int { lo, hi, excl } => {
                let top = hi.unwrap_or(lo + 5);
                let mut v: Vec<i64> = vec![lo, lo + 1, (lo + top) / 2, top - 1, top];
                v.retain(|x| (lo..=top).contains(x) && !excl.contains(x));
                v.sort_unstable();
                v.dedup();
                v.iter().map(i64::to_string).collect()
            }
            Dom::Set(f) => f().iter().map(f64::to_string).collect(),
            Dom::Real {
                lo,
                hi,
                lo_open,
                hi_open,
            } => {
                let top = hi.unwrap_or(lo + 10.0);
                let mut v = vec![(lo + top) / 2.0, lo + (top - lo) / 4.0];
                if !lo_open {
                    v.push(lo);
                }
                if !hi_open && hi.is_some() {
                    v.push(top);
                }
                v.iter().map(f64::to_string).collect()
            }
        }
    }

    fn to_json(self) -> Value {
        match self {
            Dom::Int { lo, hi, excl } => {
                json!({"type": "int", "lo": lo, "hi": hi, "excl": excl})
            }
            Dom::Set(f) => json!({"type": "set", "values": f()}),
            Dom::Real {
                lo,
                hi,
                lo_open,
                hi_open,
            } => {
                json!({"type": "real", "lo": lo, "hi": hi, "lo_open": lo_open, "hi_open": hi_open})
            }
        }
    }
}

impl MechSpec {
    /// Имя по значениям параметров (строки как в имени): `{param}` → значение.
    pub fn expand(&self, vals: &[&str]) -> String {
        let mut out = self.name_template.to_string();
        for (p, v) in self.params.iter().zip(vals) {
            out = out.replace(&format!("{{{}}}", p.name), v);
        }
        out
    }

    /// Имена по точкам доменов (декартово произведение).
    pub fn sample_names(&self) -> Vec<String> {
        let mut acc: Vec<Vec<String>> = vec![vec![]];
        for p in self.params {
            let pts = p.dom.sample();
            acc = acc
                .into_iter()
                .flat_map(|a| {
                    pts.iter().map(move |x| {
                        let mut b = a.clone();
                        b.push(x.clone());
                        b
                    })
                })
                .collect();
        }
        acc.iter()
            .map(|vals| self.expand(&vals.iter().map(String::as_str).collect::<Vec<_>>()))
            .collect()
    }
}

/// Форма выхода только для R2-бинарника (по каталогу).
pub fn is_r2_variant(id: &str) -> bool {
    MECHANICS
        .iter()
        .any(|m| m.kind == "exit" && m.id == id && m.is_r2)
}

/// `lob mechanics --json`: каталог целиком для `cellhash.py`/`master.py`.
pub fn catalog_json() -> Value {
    let rows: Vec<Value> = MECHANICS
        .iter()
        .map(|m| {
            json!({
                "id": m.id,
                "kind": m.kind,
                "prefix": m.prefix,
                "name_template": m.name_template,
                "is_r2": m.is_r2,
                "samples": m.sample_names(),
                "params": m.params.iter().map(|p| json!({"name": p.name, "dom": p.dom.to_json()})).collect::<Vec<_>>(),
            })
        })
        .collect();
    json!({ "schema": 1, "mechanics": rows })
}

/// `lob mechanics [--json]` — каталог механик (В-227).
#[derive(clap::Args, Debug)]
pub struct MechanicsArgs {
    /// Каталог целиком в JSON (для Python); без флага — таблица «id, шаблон, r2».
    #[arg(long)]
    pub json: bool,
}

pub fn run_mechanics(args: &MechanicsArgs) -> anyhow::Result<()> {
    if args.json {
        println!("{}", serde_json::to_string_pretty(&catalog_json())?);
    } else {
        for m in MECHANICS {
            println!(
                "{}\t{}\t{}",
                m.id,
                m.name_template,
                if m.is_r2 { "r2" } else { "" }
            );
        }
    }
    Ok(())
}
