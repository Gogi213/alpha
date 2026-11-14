//! Конфиг варианта П-42 (§5.2): база = правила витрины; вариант = база + переопределения `ключ=значение`.
//!
//! Проценты хранятся в тысячных долях процента (`*_pm`: 0,3 % = 300), кратные NATR — в тысячных (`*_m`),
//! обороты — в единицах кэша (1e-5 котируемой валюты). Строка варианта: `<id> ключ=значение ключ=значение…`.

use super::data::NATR_PERIODS;
use anyhow::{bail, Context, Result};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct LevelCfg {
    pub tf_min: usize,
    pub k: usize,
    pub d_pm: i64,
    pub n: usize,
    pub tau_pm: i64,
    pub l_days: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct SigCfg {
    pub lv: LevelCfg,
    pub natr_p: usize,
    pub natr_min_pm: i64,
    pub t1h_u: i64,
    /// 0 — фильтра нет.
    pub t24_u: i64,
    pub track_pm: i64,
    pub e_pm: i64,
    pub color: bool,
    pub f_pm: i64,
    pub r_min: usize,
    pub ban: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExitCfg {
    pub natr_p: usize,
    pub n_tp: usize,
    pub t_low_m: i64,
    pub t_high_m: i64,
    pub q_pct: i64,
    pub be: bool,
    pub a_m: i64,
    pub r_m: i64,
    pub s_pm: i64,
    pub slip_bps: i64,
    /// Лучший порядок внутри минутки (справочно, не для вердикта).
    pub best_order: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PortCfg {
    pub cool_min: usize,
    pub m_stops: usize,
    pub mm_min: usize,
    pub s_glob: usize,
    pub w_min: usize,
    pub ww_min: usize,
    pub p: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Params {
    pub sig: SigCfg,
    pub exit: ExitCfg,
    pub port: PortCfg,
}

fn pm(x: f64) -> i64 {
    (x * 1000.0).round() as i64
}

impl Params {
    /// База витрины (§5.2).
    pub fn base() -> Params {
        Params {
            sig: SigCfg {
                lv: LevelCfg {
                    tf_min: 60,
                    k: 2,
                    d_pm: pm(0.5),
                    n: 2,
                    tau_pm: pm(0.2),
                    l_days: 7,
                },
                natr_p: 30,
                natr_min_pm: pm(0.4),
                t1h_u: 500_000 * 100_000,
                t24_u: 0,
                track_pm: pm(2.0),
                e_pm: pm(0.3),
                color: true,
                f_pm: pm(0.1),
                r_min: 15,
                ban: true,
            },
            exit: ExitCfg {
                natr_p: 30,
                n_tp: 8,
                t_low_m: 1000,
                t_high_m: 5000,
                q_pct: 50,
                be: true,
                a_m: 2000,
                r_m: 2000,
                s_pm: pm(5.0),
                slip_bps: 1,
                best_order: false,
            },
            port: PortCfg {
                cool_min: 5,
                m_stops: 3,
                mm_min: 60,
                s_glob: 4,
                w_min: 10,
                ww_min: 60,
                p: 3,
            },
        }
    }

    /// Применяет одно переопределение; ключи — в `docs/research/P-42-level-breakout.md` §5.2.
    pub fn set(&mut self, key: &str, val: &str) -> Result<()> {
        let f: f64 = val.parse().with_context(|| format!("{key}={val}"))?;
        let u = f.round() as usize;
        let b = f != 0.0;
        match key {
            "natr_p" => self.sig.natr_p = u,
            "natr_min" => self.sig.natr_min_pm = pm(f),
            "t1h" => self.sig.t1h_u = (f * 1e6 * 1e5).round() as i64,
            "t24" => self.sig.t24_u = (f * 1e6 * 1e5).round() as i64,
            "natr_exit_p" => self.exit.natr_p = u,
            "tf" => self.sig.lv.tf_min = u,
            "k" => self.sig.lv.k = u,
            "d" => self.sig.lv.d_pm = pm(f),
            "n_bounce" => self.sig.lv.n = u,
            "tau" => self.sig.lv.tau_pm = pm(f),
            "l_days" => self.sig.lv.l_days = u,
            "track" => self.sig.track_pm = pm(f),
            "e" => self.sig.e_pm = pm(f),
            "color" => self.sig.color = b,
            "f" => self.sig.f_pm = pm(f),
            "r" => self.sig.r_min = u,
            "ban" => self.sig.ban = b,
            "n_tp" => self.exit.n_tp = u,
            "t_low" => self.exit.t_low_m = pm(f),
            "t_high" => self.exit.t_high_m = pm(f),
            "q" => self.exit.q_pct = f.round() as i64,
            "be" => self.exit.be = b,
            "a" => self.exit.a_m = pm(f),
            "rr" => self.exit.r_m = pm(f),
            "s" => self.exit.s_pm = pm(f),
            "slip" => self.exit.slip_bps = f.round() as i64,
            "best" => self.exit.best_order = b,
            "c" => self.port.cool_min = u,
            "m" => self.port.m_stops = u,
            "mm" => self.port.mm_min = u,
            "s_glob" => self.port.s_glob = u,
            "w" => self.port.w_min = u,
            "ww" => self.port.ww_min = u,
            "p" => self.port.p = u,
            _ => bail!("неизвестный ключ {key}"),
        }
        Ok(())
    }

    pub fn validate(&self) -> Result<()> {
        if !NATR_PERIODS.contains(&self.sig.natr_p) || !NATR_PERIODS.contains(&self.exit.natr_p) {
            bail!("период NATR должен быть из {NATR_PERIODS:?}");
        }
        if !super::data::TF_MINUTES.contains(&self.sig.lv.tf_min) {
            bail!("tf должен быть из {:?}", super::data::TF_MINUTES);
        }
        if self.exit.t_low_m >= self.exit.t_high_m {
            bail!("t_low должен быть меньше t_high (лестница тейков идёт вверх)");
        }
        if self.exit.n_tp < 2 || self.port.p == 0 || self.sig.lv.k == 0 || self.sig.lv.n == 0 {
            bail!("n_tp ≥ 2, p ≥ 1, k ≥ 1, n_bounce ≥ 1");
        }
        Ok(())
    }
}

/// Строка `<id> k=v k=v…` → (id, параметры).
pub fn parse_variant(line: &str) -> Result<(String, Params)> {
    let mut it = line.split_whitespace();
    let Some(id) = it.next() else {
        bail!("пустая строка варианта");
    };
    let mut p = Params::base();
    for kv in it {
        let Some((k, v)) = kv.split_once('=') else {
            bail!("ожидалось ключ=значение: {kv}");
        };
        p.set(k, v)?;
    }
    p.validate()?;
    Ok((id.to_string(), p))
}
