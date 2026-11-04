//! Параметры прогона из `params.json` — тот же файл, что пишет протокол (`docs/research/P-34-params.json`):
//! секции и ключи как в нём. Нужные ядру ключи обязательны, умолчаний нет (изобретённое число запрещено);
//! остальные ключи файла (`_src`, `verdict`, `costs`, …) ядро не читает и не проверяет. Зерно контролей — не
//! здесь, а аргументом запуска (в файле зерно только у бутстрэпа).

use anyhow::{bail, Context, Result};
use serde::Deserialize;
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Params {
    pub grid_min: GridMin,
    pub episodes: Episodes,
    pub features: Features,
    pub onset: Onset,
    pub controls: Controls,
    pub label_forward: LabelForward,
    pub split: Split,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct GridMin {
    pub value: u32,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Episodes {
    #[serde(rename = "D_min")]
    pub d_min: Vec<u32>,
    #[serde(rename = "quantile_X_D")]
    pub quantile_x_d: f64,
    #[serde(rename = "E4_wick")]
    pub e4_wick: Wick,
    /// Строго меньше этого числа свечей в хвосте — событие помечено выбросом (П-34 §8).
    #[serde(rename = "outlier_tail_candles_lt")]
    pub outlier_tail_candles_lt: u32,
    /// Страта «рыночный эпизод» (П-34 §2): эпизод монеты помечен, если эпизод BTCUSDT того же знака и D
    /// перекрывает его хотя бы на эту долю.
    pub market_stratum: MarketStratum,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct MarketStratum {
    pub min_overlap_share: f64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Wick {
    pub return_share: f64,
    pub within_min: u32,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Features {
    pub windows_min: Vec<u32>,
    pub norm: Norm,
    pub funding_z_history_days: u32,
    pub theta: Vec<f64>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Norm {
    pub history_days: u32,
    pub mad_factor: f64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Onset {
    #[serde(rename = "L_pre_min")]
    pub l_pre_min: u32,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Controls {
    #[serde(rename = "C2a")]
    pub c2a: C2a,
    #[serde(rename = "C2b")]
    pub c2b: C2b,
    pub sigma_1m_window_days: u32,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct C2a {
    pub moments_per_first_trigger: u32,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct C2b {
    pub moments_per_episode: u32,
    pub exclude_same_sign_episode_window_min: [i64; 2],
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct LabelForward {
    #[serde(rename = "D_min")]
    pub d_min: Vec<u32>,
    #[serde(rename = "H_min")]
    pub h_min: Vec<u32>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Split {
    pub fit: [String; 2],
    pub test: [String; 2],
}

impl Params {
    pub fn from_json(text: &str) -> Result<Params> {
        let p: Params = serde_json::from_str(text).context("params.json")?;
        p.validate()?;
        Ok(p)
    }

    pub fn load(path: &Path) -> Result<Params> {
        let text = std::fs::read_to_string(path).with_context(|| path.display().to_string())?;
        Self::from_json(&text)
    }

    fn validate(&self) -> Result<()> {
        let g = self.grid_min.value;
        if g == 0 {
            bail!("grid_min.value > 0");
        }
        let mins = |name: &str, v: &[u32], step: u32| -> Result<()> {
            if v.is_empty() || v.iter().any(|&x| x == 0 || x % step != 0) {
                bail!("{name}: непусто, каждое значение > 0 и кратно {step} мин");
            }
            Ok(())
        };
        mins("features.windows_min", &self.features.windows_min, g)?;
        mins("episodes.D_min", &self.episodes.d_min, 1)?;
        mins("label_forward.D_min", &self.label_forward.d_min, 1)?;
        mins("label_forward.H_min", &self.label_forward.h_min, 1)?;
        let th = &self.features.theta;
        if th.is_empty() || th.iter().any(|t| !t.is_finite() || *t <= 0.0) {
            bail!("features.theta: непусто, каждый порог конечен и > 0");
        }
        let (q, m) = (self.episodes.quantile_x_d, self.features.norm.mad_factor);
        if !(q > 0.0 && q < 1.0) || !(m.is_finite() && m > 0.0) {
            bail!("episodes.quantile_X_D в (0,1), features.norm.mad_factor > 0");
        }
        let share = self.episodes.e4_wick.return_share;
        if !(share > 0.0 && share <= 1.0) {
            bail!("episodes.E4_wick.return_share в (0,1]");
        }
        let ov = self.episodes.market_stratum.min_overlap_share;
        if !(ov > 0.0 && ov <= 1.0) {
            bail!("episodes.market_stratum.min_overlap_share в (0,1]");
        }
        let (n, f, s) = (
            self.features.norm.history_days,
            self.features.funding_z_history_days,
            self.controls.sigma_1m_window_days,
        );
        if n == 0 || f == 0 || s == 0 || self.onset.l_pre_min == 0 {
            bail!("history_days, funding_z_history_days, sigma_1m_window_days, L_pre_min > 0");
        }
        if self.controls.c2a.moments_per_first_trigger == 0
            || self.controls.c2b.moments_per_episode == 0
        {
            bail!("число контрольных моментов > 0");
        }
        let w = self.controls.c2b.exclude_same_sign_episode_window_min;
        if w[0] > w[1] {
            bail!("controls.C2b.exclude_same_sign_episode_window_min: начало <= конец");
        }
        Ok(())
    }
}
