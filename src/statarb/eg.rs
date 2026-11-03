//! Тест Энгла–Грейнджера по суммам (TK-486): OLS `y = α + β·x` на ln-ценах и ADF (лаг 0, без константы) остатка
//! выражаются через 15 сумм по минутам, где у обеих ног есть значения и в `t`, и в `t−1`. Суммы аддитивны по
//! блокам отсчётов: окно формирования и его половины (P-32 §6.1) — суммы блоков, а не пересчёт «пара × окно».
//!
//! Ряды центрируются на символ (одна константа на символ на всё окно) до сумм: сдвиг ноги не меняет β, `t`-статистику
//! и остаток, а `f64`-суммы не теряют точность на уровне ln-цены. Лаг ADF и отсутствие константы — выбор метода
//! (остаток OLS с константой имеет нулевое среднее), не назначенное число; другой лаг — отдельный вариант.

/// Суммы пары по общим наблюдениям `W` (`x`, `y` в `t` и `t−1` заданы у обеих ног). Порядок полей — как в `add`.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct EgSums {
    pub n: f64,
    pub x: f64,
    pub x1: f64,
    pub y: f64,
    pub y1: f64,
    pub xx: f64,
    pub x1x1: f64,
    pub xx1: f64,
    pub yy: f64,
    pub y1y1: f64,
    pub yy1: f64,
    pub xy: f64,
    pub x1y1: f64,
    pub xy1: f64,
    pub x1y: f64,
}

/// Результат теста: `t` — статистика ADF остатка (меньше — сильнее связь), `gamma` — коэффициент при `u_{t−1}`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct EgResult {
    pub alpha: f64,
    pub beta: f64,
    pub gamma: f64,
    pub t_adf: f64,
    pub n: u32,
}

impl EgSums {
    #[inline]
    pub fn add(&mut self, x: f64, x1: f64, y: f64, y1: f64) {
        self.n += 1.0;
        self.x += x;
        self.x1 += x1;
        self.y += y;
        self.y1 += y1;
        self.xx += x * x;
        self.x1x1 += x1 * x1;
        self.xx1 += x * x1;
        self.yy += y * y;
        self.y1y1 += y1 * y1;
        self.yy1 += y * y1;
        self.xy += x * y;
        self.x1y1 += x1 * y1;
        self.xy1 += x * y1;
        self.x1y += x1 * y;
    }

    /// Сумма блоков: окно формирования или его половина.
    pub fn merge(&mut self, o: &EgSums) {
        self.n += o.n;
        self.x += o.x;
        self.x1 += o.x1;
        self.y += o.y;
        self.y1 += o.y1;
        self.xx += o.xx;
        self.x1x1 += o.x1x1;
        self.xx1 += o.xx1;
        self.yy += o.yy;
        self.y1y1 += o.y1y1;
        self.yy1 += o.yy1;
        self.xy += o.xy;
        self.x1y1 += o.x1y1;
        self.xy1 += o.xy1;
        self.x1y += o.x1y;
    }

    /// Суммы прямым проходом по рядам ln-цен (`NaN` — нет свечи): эталон и «было» для замера.
    pub fn from_series(x: &[f64], y: &[f64]) -> EgSums {
        let mut s = EgSums::default();
        for k in 1..x.len().min(y.len()) {
            let (a, a1, b, b1) = (x[k], x[k - 1], y[k], y[k - 1]);
            if a.is_finite() && a1.is_finite() && b.is_finite() && b1.is_finite() {
                s.add(a, a1, b, b1);
            }
        }
        s
    }

    /// EG по суммам; `None` — наблюдений меньше `min_obs` (но не меньше 4), нулевая дисперсия `x` или остатка.
    pub fn test(&self, min_obs: u32) -> Option<EgResult> {
        let n = self.n;
        if n < f64::from(min_obs.max(4)) {
            return None;
        }
        let vxx = n * self.xx - self.x * self.x;
        if vxx <= 0.0 {
            return None;
        }
        let beta = (n * self.xy - self.x * self.y) / vxx;
        let alpha = (self.y - beta * self.x) / n;
        // квадратичные формы остатка u = y − α − βx и u1 = y1 − α − βx1 по суммам
        let uu = self.yy + n * alpha * alpha + beta * beta * self.xx
            - 2.0 * alpha * self.y
            - 2.0 * beta * self.xy
            + 2.0 * alpha * beta * self.x;
        let u1u1 = self.y1y1 + n * alpha * alpha + beta * beta * self.x1x1
            - 2.0 * alpha * self.y1
            - 2.0 * beta * self.x1y1
            + 2.0 * alpha * beta * self.x1;
        let uu1 = self.yy1 - alpha * self.y - beta * self.x1y - alpha * self.y1
            + n * alpha * alpha
            + alpha * beta * self.x1
            - beta * self.xy1
            + alpha * beta * self.x
            + beta * beta * self.xx1;
        if u1u1 <= 0.0 {
            return None;
        }
        let num = uu1 - u1u1; // Σ Δu·u1
        let dd = uu - 2.0 * uu1 + u1u1; // Σ Δu²
        let gamma = num / u1u1;
        let sse = dd - num * num / u1u1;
        if sse <= 0.0 {
            return None;
        }
        let t_adf = gamma / (sse / (n - 1.0) / u1u1).sqrt();
        Some(EgResult {
            alpha,
            beta,
            gamma,
            t_adf,
            n: n as u32,
        })
    }
}

/// Прямой EG по рядам (двухпроходный, центрированный) на тех же общих наблюдениях — эталон для проверки сумм.
pub fn eg_naive(x: &[f64], y: &[f64], min_obs: u32) -> Option<EgResult> {
    let idx: Vec<usize> = (1..x.len().min(y.len()))
        .filter(|&k| {
            x[k].is_finite() && x[k - 1].is_finite() && y[k].is_finite() && y[k - 1].is_finite()
        })
        .collect();
    let n = idx.len() as f64;
    if n < f64::from(min_obs.max(4)) {
        return None;
    }
    let mx = idx.iter().map(|&k| x[k]).sum::<f64>() / n;
    let my = idx.iter().map(|&k| y[k]).sum::<f64>() / n;
    let (mut sxx, mut sxy) = (0.0, 0.0);
    for &k in &idx {
        sxx += (x[k] - mx) * (x[k] - mx);
        sxy += (x[k] - mx) * (y[k] - my);
    }
    if sxx <= 0.0 {
        return None;
    }
    let beta = sxy / sxx;
    let alpha = my - beta * mx;
    let (mut a, mut b, mut c) = (0.0, 0.0, 0.0); // Σu1², Σ Δu·u1, Σ Δu²
    for &k in &idx {
        let u = y[k] - alpha - beta * x[k];
        let u1 = y[k - 1] - alpha - beta * x[k - 1];
        let d = u - u1;
        a += u1 * u1;
        b += d * u1;
        c += d * d;
    }
    if a <= 0.0 {
        return None;
    }
    let gamma = b / a;
    let sse = c - gamma * gamma * a;
    if sse <= 0.0 {
        return None;
    }
    Some(EgResult {
        alpha,
        beta,
        gamma,
        t_adf: gamma / (sse / (n - 1.0) / a).sqrt(),
        n: n as u32,
    })
}
