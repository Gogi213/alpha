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
    /// §13.11: уровень умирает от прокола с первого пивота (минутный high/low за ценой > τ; предыстория и
    /// перепроверка при смене версии цены). Только `breakout tick`.
    pub pk: bool,
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
    /// §13.13: подход к уровню с правильной стороны — 0 выкл.; 1 — отвергать сигнал, пришедший в зону сверху (назад по сделкам:
    /// первая, дошедшая до уровня, решает «сверху», первая ниже зоны — «снизу»); 2 — только пометить (`ws`). Только `breakout tick`.
    pub ap: u8,
    /// §13.13: сделка на уровне или за ним (high ≥ цены версии) убивает уровень со следующей минуты. Только `breakout tick`.
    pub pb: bool,
    /// §13.14 «свежий уровень», (а): H в минутах — последнее касание уровня не старше H; 0 — (а) выключено. Только `breakout tick`.
    pub fr_h_min: usize,
    /// §13.14, (б) «проторговка»: окно K в минутах.
    pub fr_k: usize,
    /// §13.14, (б): доля K в % — минимум минут закрытия в полосе подхода; 0 — (б) выключено.
    pub fr_share: usize,
    /// §13.14, (б): ширина полосы подхода b в тысячных процента.
    pub fr_b_pm: i64,
}

#[derive(Clone, PartialEq)]
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
    /// Задержка входа, мс (только шаг B по сделкам; 1м-счёт её не использует).
    pub lat_ms: i64,
    /// Задержка исполнения выхода по стопу/трейлу/безубытку, мс (§13.6, справочно): выход — по первой сделке не раньше
    /// срабатывания + задержка; тейки остаются лимитками. 0 — выход на сделке срабатывания (как в §13.2).
    pub lat_exit_ms: i64,
    /// §13.12: перекос цены лестницы тейков — ступень i стоит на `t_low + (t_high − t_low)·(i/(n−1))^γ` (γ ×1000; 1000 — равные шаги).
    /// Только `breakout tick`.
    pub tp_gamma_m: i64,
    /// §13.12: перекос объёма — вес ступени ∝ `1 + κ·i/(n−1)` (κ ×1000; 0 — равные доли); сумма долей остаётся q. Только `breakout tick`.
    pub tp_kappa_m: i64,
    /// §13.12: источник NATR выхода: 0 — закрытая минутка t−1 (30 баров до сигнальной, §13.2); 1 — включая сигнальную минуту t
    /// (заглядывание вперёд, справочно). Только `breakout tick`.
    pub natr_src: u8,
    /// §13.16: вход по ленте «как на момент сигнала + `lat`»: W, мс — максимальный возраст последней сделки стороны; 0 — прежний вход
    /// (следующая сделка стороны после `сигнал + lat`). Только `breakout tick`.
    pub ent_w_ms: i64,
    /// §13.16: допустимый перелёт цены-заменителя за уровень, бп (0 — строго за уровнем отвергается). Действует при `ent_w_ms > 0`.
    pub ent_x_bp: i64,
    /// §13.19 (`ls`): стоп за дальним краем зоны + k·NATR (k ×1000; 0 — выкл): `max(e − S%, min(e, P_far) − nat(k))`. Только `breakout tick`.
    pub ls_m: i64,
    /// §13.19 (`tt`): выход по времени — T мин от первого «пересекли»; 0 — выкл.
    pub tx_min: i64,
    /// §13.19 (`tb`): «пробой» глубиной X (тысячные процента от уровня) отменяет выход по времени.
    pub tx_x_pm: i64,
    /// §13.19 (`rf`): выход при возврате — глубина f (тысячные процента от уровня); 0 — выкл.
    pub rt_f_pm: i64,
    /// §13.19 (`rs`): длительность возврата, с.
    pub rt_s: i64,
    /// §13.19 (`bm`): безубыток — 0 как раньше; 1 выкл; 2 после 2-го тейка; 3 за уровнем.
    pub be_mode: u8,
    /// §13.19 (`e2`): второй вход 50/50 при подходе к уровню ближе d (тысячные процента от уровня); 0 — выкл. Требует `ew > 0`.
    pub e2_pm: i64,
    /// §13.21 (`sn`): стоп в NATR — k0 ×1000 (0 — выкл.): `d = clamp(k0·NATR·g, 0,5 %, S_жёсткий)`, стоп = цена входа лота ∓ `d`; NATR — закрытая
    /// минутка `t−1`, заморожен на входе. Только `breakout tick`.
    pub sn_m: i64,
    /// §13.21 (`sg`): семейство множителя `g` — 0 константа (`g = 1`); 1 линейно по времени удержания (`g = 1 + β·Δt_ч`, пересчёт на каждой сделке);
    /// 2 геометрически по числу исполненных тейков `j` (`g = ρ^j`); 3 шаг по `j` (`g = 1 + δ·j`).
    pub sg: u8,
    /// §13.21 (`sb`): β семейства `sg = 1`, 1/ч ×1000.
    pub sb_m: i64,
    /// §13.21 (`sr`): ρ семейства `sg = 2`, ×1000.
    pub sr_m: i64,
    /// §13.21 (`sd`): δ семейства `sg = 3`, ×1000.
    pub sd_m: i64,
    /// §13.21 (`tj`): шаг лестницы тейков δ ×1000 (0 — выкл.): после исполнения тейков оставшиеся ступени стоят на `mult_i·(1 + δ·j)` NATR, `j` — число
    /// исполненных тейков; пересчитанная лестница действует со следующей сделки. Только `breakout tick`.
    pub tj_m: i64,
    /// v22 (`tl`, TK-603): трейл лимиткой — 0 выкл. (трейл тейкером по цене сделки, как в v21); 1 — лимитный приказ на остаток по цене трейла, мейкер,
    /// без проскальзывания, исполняется сделкой «дошла или прошла» (по цене приказа и тогда, когда к приходу приказа цена уже за ним); 2 — то же, но приказ,
    /// пришедший при цене уже строго за ним, исполняется как рыночный выход v21. Стопы и безубыток остаются рыночными. Только `breakout tick`.
    pub tl: u8,
    /// §13.31 (`nx`): выход «сценарий отменён» до касания — цена ушла от уровня против позиции на X·NATR (X ×1000; 0 — выкл.): пока не было сделки
    /// `p ≥ L`, первая сделка `p ≤ L − X·NATR` (не выше `e − 1`) закрывает остаток; после касания не действует. Вид выхода `nt`, не «стоп».
    /// Только `breakout tick`.
    pub nx_m: i64,
    /// §13.31 (`sl`): резиновый стоп от уровня — `L − k·NATR` (k ×1000; 0 — выкл.), не выше `e − 1`; жёсткий стоп `s` остаётся потолком убытка.
    /// Только `breakout tick`.
    pub sl_m: i64,
    /// §13.31 (`cw`): стоп за проторговкой — окно W закрытых минут перед сигнальной (0 — выкл.).
    pub cs_w: i64,
    /// §13.31 (`cl`, `ch`): допустимая ширина проторговки в NATR ×1000 — `cl ≤ ширина ≤ ch`; вне диапазона — запасной стоп `sl`.
    pub cs_lo_m: i64,
    pub cs_hi_m: i64,
    /// §13.31 (`cm`): отступ стопа за дальний край проторговки, NATR ×1000.
    pub cs_m_m: i64,
}

/// Ручной `Debug`: при выключенных ключах §13.31 строка `params` в meta.json — та же, что у производного `Debug` до §13.31.
impl std::fmt::Debug for ExitCfg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("ExitCfg");
        d.field("natr_p", &self.natr_p)
            .field("n_tp", &self.n_tp)
            .field("t_low_m", &self.t_low_m)
            .field("t_high_m", &self.t_high_m)
            .field("q_pct", &self.q_pct)
            .field("be", &self.be)
            .field("a_m", &self.a_m)
            .field("r_m", &self.r_m)
            .field("s_pm", &self.s_pm)
            .field("slip_bps", &self.slip_bps)
            .field("best_order", &self.best_order)
            .field("lat_ms", &self.lat_ms)
            .field("lat_exit_ms", &self.lat_exit_ms)
            .field("tp_gamma_m", &self.tp_gamma_m)
            .field("tp_kappa_m", &self.tp_kappa_m)
            .field("natr_src", &self.natr_src)
            .field("ent_w_ms", &self.ent_w_ms)
            .field("ent_x_bp", &self.ent_x_bp)
            .field("ls_m", &self.ls_m)
            .field("tx_min", &self.tx_min)
            .field("tx_x_pm", &self.tx_x_pm)
            .field("rt_f_pm", &self.rt_f_pm)
            .field("rt_s", &self.rt_s)
            .field("be_mode", &self.be_mode)
            .field("e2_pm", &self.e2_pm)
            .field("sn_m", &self.sn_m)
            .field("sg", &self.sg)
            .field("sb_m", &self.sb_m)
            .field("sr_m", &self.sr_m)
            .field("sd_m", &self.sd_m)
            .field("tj_m", &self.tj_m)
            .field("tl", &self.tl);
        if self.stop_redesign_opts() {
            d.field("nx_m", &self.nx_m)
                .field("sl_m", &self.sl_m)
                .field("cs_w", &self.cs_w)
                .field("cs_lo_m", &self.cs_lo_m)
                .field("cs_hi_m", &self.cs_hi_m)
                .field("cs_m_m", &self.cs_m_m);
        }
        d.finish()
    }
}

/// §13.21: фильтр кандидатов по признаку (`fa=<имя> fo=lo|hi fv=<число>`): кандидат отбрасывается ДО портфеля, если значение признака
/// меньше `fv` (`fo=lo`) или больше `fv` (`fo=hi`); нет строки в таблице или значение пусто/NaN — кандидат проходит. Таблица — `--ftab`.
/// Только `breakout tick`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct FilterCfg {
    /// Имя столбца таблицы признаков; пусто — фильтра нет.
    pub name: String,
    /// `fo`: `Some(false)` — `lo` (отбросить значения ниже `fv`), `Some(true)` — `hi` (выше `fv`).
    pub hi: Option<bool>,
    pub v: Option<f64>,
}

impl FilterCfg {
    pub fn on(&self) -> bool {
        !self.name.is_empty()
    }
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
    /// §13.5: 0 — одна сделка на уровень (любой выход расходует уровень); 1 — повторный вход после тейков/безубытка/
    /// трейла, уровень расходует только жёсткий стоп; 2 — выходы уровень не расходуют вовсе (ложный и истинный пробой —
    /// как и раньше). Только `breakout tick`.
    pub reentry: u8,
    /// §13.21 (`busy`): `true` (по умолчанию) — монета с открытой позицией отвергает новые сигналы (отказ «занята»); `false` — второй сигнал
    /// другого уровня той же монеты входит при открытой позиции (режим сигналов: одна сделка на уровень, лимиты портфеля — `p`, `c`, `m`, `s_glob`,
    /// `ww` — выключаются своими значениями). Только `breakout tick`.
    pub busy: bool,
}

#[derive(Clone, PartialEq)]
pub struct Params {
    pub sig: SigCfg,
    pub exit: ExitCfg,
    pub port: PortCfg,
    /// §13.21: фильтр кандидатов по признаку (`fa/fo/fv`); выключен по умолчанию.
    pub filt: FilterCfg,
    /// §13.28 (`mk`): вход лимиткой — 0 выкл. (путь тейкера), 1 — mk1, 2 — mk2 (ретест у уровня); требует `ew > 0`. Только `breakout tick`.
    pub mk: u8,
}

/// Ручной `Debug`: отпечаток параметров в `meta.json` при `mk = 0` — тот же, что у версий до §13.28 (поле `mk` печатается только при `mk ≠ 0`).
impl std::fmt::Debug for Params {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("Params");
        d.field("sig", &self.sig)
            .field("exit", &self.exit)
            .field("port", &self.port)
            .field("filt", &self.filt);
        if self.mk != 0 {
            d.field("mk", &self.mk);
        }
        d.finish()
    }
}

fn pm(x: f64) -> i64 {
    (x * 1000.0).round() as i64
}

impl SigCfg {
    /// Правило §13.14 «свежий уровень» включено (хотя бы одна из ветвей (а), (б)).
    pub fn fresh_on(&self) -> bool {
        self.fr_h_min > 0 || self.fr_share > 0
    }
}

impl ExitCfg {
    /// Вход по ленте §13.16 включён (`ew > 0`).
    pub fn tape_entry(&self) -> bool {
        self.ent_w_ms > 0
    }

    /// Опции §13.19, зависящие от цены уровня (`ls`, `tt`, `rf`, `bm = 3`) включены — нужны `lp` и `P_far`.
    pub fn level_opts(&self) -> bool {
        self.ls_m > 0
            || self.tx_min > 0
            || self.rt_f_pm > 0
            || self.be_mode == 3
            || self.stop_redesign_opts()
    }

    /// Опции §13.31 (`nx`, `sl`, `cw`, `cl`, `ch`, `cm`) включены — `breakout tick` only.
    pub fn stop_redesign_opts(&self) -> bool {
        self.nx_m != 0
            || self.sl_m != 0
            || self.cs_w != 0
            || self.cs_lo_m != 0
            || self.cs_hi_m != 0
            || self.cs_m_m != 0
    }

    /// Любая опция §13.19 включена (`breakout tick` only).
    pub fn lvl_exit_opts(&self) -> bool {
        self.level_opts() || self.be_mode != 0 || self.e2_pm > 0
    }

    /// Опции §13.21 (NATR-стоп `sn/sg/sb/sr/sd`, шаг тейков `tj`) включены — `breakout tick` only.
    pub fn natr_stop_opts(&self) -> bool {
        self.sn_m != 0
            || self.sg != 0
            || self.sb_m != 0
            || self.sr_m != 0
            || self.sd_m != 0
            || self.tj_m != 0
    }

    /// Опции §13.12 не по умолчанию (лестница тейков с перекосом или NATR по другой минутке) — только `breakout tick`.
    pub fn take_grid_opts(&self) -> bool {
        self.tp_gamma_m != 1000 || self.tp_kappa_m != 0 || self.natr_src != 0
    }
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
                    pk: false,
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
                ap: 0,
                pb: false,
                fr_h_min: 0,
                fr_k: 120,
                fr_share: 0,
                fr_b_pm: pm(0.5),
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
                lat_ms: 0,
                lat_exit_ms: 0,
                tp_gamma_m: 1000,
                tp_kappa_m: 0,
                natr_src: 0,
                ent_w_ms: 0,
                ent_x_bp: 0,
                ls_m: 0,
                tx_min: 0,
                tx_x_pm: 0,
                rt_f_pm: 0,
                rt_s: 0,
                be_mode: 0,
                e2_pm: 0,
                sn_m: 0,
                sg: 0,
                sb_m: 0,
                sr_m: 0,
                sd_m: 0,
                tj_m: 0,
                tl: 0,
                nx_m: 0,
                sl_m: 0,
                cs_w: 0,
                cs_lo_m: 0,
                cs_hi_m: 0,
                cs_m_m: 0,
            },
            port: PortCfg {
                cool_min: 5,
                m_stops: 3,
                mm_min: 60,
                s_glob: 4,
                w_min: 10,
                ww_min: 60,
                p: 3,
                reentry: 0,
                busy: true,
            },
            filt: FilterCfg::default(),
            mk: 0,
        }
    }

    /// Применяет одно переопределение; ключи — в `docs/research/P-42-level-breakout.md` §5.2.
    pub fn set(&mut self, key: &str, val: &str) -> Result<()> {
        // §13.21: строковые значения фильтра признаков — до числового разбора
        match key {
            "fa" => {
                self.filt.name = val.to_string();
                return Ok(());
            }
            "fo" => {
                self.filt.hi = Some(match val {
                    "lo" => false,
                    "hi" => true,
                    _ => bail!("fo ∈ {{lo, hi}}: {val}"),
                });
                return Ok(());
            }
            _ => {}
        }
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
            "pk" => self.sig.lv.pk = b,
            "track" => self.sig.track_pm = pm(f),
            "e" => self.sig.e_pm = pm(f),
            "color" => self.sig.color = b,
            "f" => self.sig.f_pm = pm(f),
            "r" => self.sig.r_min = u,
            "ban" => self.sig.ban = b,
            "ap" => self.sig.ap = u.min(255) as u8,
            "pb" => self.sig.pb = b,
            "fh" => self.sig.fr_h_min = (f * 60.0).round() as usize,
            "fk" => self.sig.fr_k = u,
            "fm" => self.sig.fr_share = u,
            "fb" => self.sig.fr_b_pm = pm(f),
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
            "lat" => self.exit.lat_ms = f.round() as i64,
            "latx" => self.exit.lat_exit_ms = f.round() as i64,
            "tpg" => self.exit.tp_gamma_m = pm(f),
            "tpk" => self.exit.tp_kappa_m = pm(f),
            "ns" => self.exit.natr_src = u.min(255) as u8,
            "ew" => self.exit.ent_w_ms = f.round() as i64,
            "ex" => self.exit.ent_x_bp = f.round() as i64,
            "ls" => self.exit.ls_m = pm(f),
            "tt" => self.exit.tx_min = f.round() as i64,
            "tb" => self.exit.tx_x_pm = pm(f),
            "rf" => self.exit.rt_f_pm = pm(f),
            "rs" => self.exit.rt_s = f.round() as i64,
            "bm" => self.exit.be_mode = u.min(255) as u8,
            "e2" => self.exit.e2_pm = pm(f),
            "sn" => self.exit.sn_m = pm(f),
            "sg" => self.exit.sg = u.min(255) as u8,
            "sb" => self.exit.sb_m = pm(f),
            "sr" => self.exit.sr_m = pm(f),
            "sd" => self.exit.sd_m = pm(f),
            "tj" => self.exit.tj_m = pm(f),
            "tl" => self.exit.tl = u.min(255) as u8,
            "nx" => self.exit.nx_m = pm(f),
            "sl" => self.exit.sl_m = pm(f),
            "cw" => self.exit.cs_w = f.round() as i64,
            "cl" => self.exit.cs_lo_m = pm(f),
            "ch" => self.exit.cs_hi_m = pm(f),
            "cm" => self.exit.cs_m_m = pm(f),
            "fv" => self.filt.v = Some(f),
            "c" => self.port.cool_min = u,
            "m" => self.port.m_stops = u,
            "mm" => self.port.mm_min = u,
            "s_glob" => self.port.s_glob = u,
            "w" => self.port.w_min = u,
            "ww" => self.port.ww_min = u,
            "p" => self.port.p = u,
            "reentry" => self.port.reentry = u.min(255) as u8,
            "busy" => self.port.busy = b,
            "mk" => self.mk = u.min(255) as u8,
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
        if self.sig.ap > 2 {
            bail!("ap ∈ {{0, 1, 2}}");
        }
        if self.exit.tp_gamma_m <= 0 || self.exit.tp_kappa_m <= -1000 {
            bail!("tpg > 0, tpk > −1");
        }
        if self.sig.fr_share > 100 || self.sig.fr_k == 0 || self.sig.fr_k > 1440 {
            bail!("fm ≤ 100, 1 ≤ fk ≤ 1440");
        }
        if self.exit.natr_src > 1 {
            bail!("ns ∈ {{0, 1}}");
        }
        if self.exit.ent_w_ms < 0 || self.exit.ent_x_bp < 0 {
            bail!("ew ≥ 0, ex ≥ 0");
        }
        if self.exit.ls_m < 0
            || self.exit.tx_min < 0
            || self.exit.tx_x_pm < 0
            || self.exit.rt_f_pm < 0
            || self.exit.rt_s < 0
            || self.exit.e2_pm < 0
        {
            bail!("ls, tt, tb, rf, rs, e2 ≥ 0");
        }
        if self.exit.be_mode > 3 {
            bail!("bm ∈ {{0, 1, 2, 3}}");
        }
        if self.exit.e2_pm > 0 && !self.exit.tape_entry() {
            bail!("e2 требует ew > 0 (вход по ленте)");
        }
        if self.mk > 2 {
            bail!("mk ∈ {{0, 1, 2}}");
        }
        if self.mk > 0 && !self.exit.tape_entry() {
            bail!("mk требует ew > 0 (вход по ленте)");
        }
        if self.port.reentry > 2 {
            bail!("reentry ∈ {{0, 1, 2}}");
        }
        self.validate_v21()?;
        self.validate_stops()?;
        if self.exit.n_tp < 2 || self.port.p == 0 || self.sig.lv.k == 0 || self.sig.lv.n == 0 {
            bail!("n_tp ≥ 2, p ≥ 1, k ≥ 1, n_bounce ≥ 1");
        }
        Ok(())
    }

    /// §13.31: `nx`, `sl`, `cw/cl/ch/cm` ≥ 0; ключи проторговки заданы только вместе с `cw` (ключ без `cw` — опечатка, а не «выкл.»); `cl < ch`.
    fn validate_stops(&self) -> Result<()> {
        let e = &self.exit;
        if e.nx_m < 0 || e.sl_m < 0 || e.cs_w < 0 || e.cs_lo_m < 0 || e.cs_hi_m < 0 || e.cs_m_m < 0
        {
            bail!("nx, sl, cw, cl, ch, cm ≥ 0");
        }
        if e.cs_w == 0 && (e.cs_lo_m != 0 || e.cs_hi_m != 0 || e.cs_m_m != 0) {
            bail!("cl/ch/cm требуют cw > 0 (проторговка)");
        }
        if e.cs_w > 0 && e.cs_lo_m >= e.cs_hi_m {
            bail!("cw требует ch > cl (допустимая ширина проторговки)");
        }
        Ok(())
    }

    /// §13.21: NATR-стоп (`sn/sg/sb/sr/sd`), шаг тейков (`tj`), фильтр признаков (`fa/fo/fv`).
    fn validate_v21(&self) -> Result<()> {
        let e = &self.exit;
        if e.sn_m < 0 || e.sb_m < 0 || e.sr_m < 0 || e.sd_m < 0 || e.tj_m < 0 {
            bail!("sn, sb, sr, sd, tj ≥ 0");
        }
        if e.sg > 3 {
            bail!("sg ∈ {{0, 1, 2, 3}}");
        }
        if e.tl > 2 {
            bail!("tl ∈ {{0, 1, 2}}");
        }
        if e.sn_m == 0 && (e.sg != 0 || e.sb_m != 0 || e.sr_m != 0 || e.sd_m != 0) {
            bail!("sg/sb/sr/sd требуют sn > 0 (NATR-стоп)");
        }
        if e.sn_m > 0 && e.natr_src != 0 {
            bail!("sn требует ns = 0: NATR стопа — закрытая минутка t−1");
        }
        // параметр семейства задан ровно для своего семейства: ключ без семейства (или семейство без ключа) — опечатка, а не «выкл.»
        let fam = [(1u8, e.sb_m, "sb"), (2, e.sr_m, "sr"), (3, e.sd_m, "sd")];
        for (g, v, name) in fam {
            if e.sg == g && v == 0 {
                bail!("sg={g} требует {name} > 0");
            }
            if e.sg != g && v != 0 {
                bail!("{name} задан при sg={}", e.sg);
            }
        }
        let fl = &self.filt;
        if fl.on() {
            if !fl
                .name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_')
            {
                bail!("fa: имя признака — буквы, цифры, «_»: {}", fl.name);
            }
            let (Some(_), Some(v)) = (fl.hi, fl.v) else {
                bail!("fa требует fo (lo|hi) и fv (число)");
            };
            if !v.is_finite() {
                bail!("fv должен быть конечным числом");
            }
        } else if fl.hi.is_some() || fl.v.is_some() {
            bail!("fo/fv без fa");
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
