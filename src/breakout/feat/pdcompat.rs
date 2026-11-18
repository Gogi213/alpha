//! Разбор полей CSV так, как это делает `pandas.read_csv` 2.2.3 с параметрами по умолчанию (`float_precision=None`
//! = конвертер `precise_xstrtod`): значение — не «правильно округлённое», а результат накопления до 17 цифр в `f64`
//! и одного деления/умножения на степень десяти. Для коротких десятичных записей (≤ 15 значащих цифр) оба совпадают;
//! для длинных (`13052463.0000000000000000` в `metrics`) — нет, поэтому копируется алгоритм pandas, а не `str::parse`.

use anyhow::{bail, Result};
use std::sync::OnceLock;

fn pow10_table() -> &'static [f64] {
    static T: OnceLock<Vec<f64>> = OnceLock::new();
    T.get_or_init(|| {
        (0..=308)
            .map(|k| format!("1e{k}").parse::<f64>().unwrap_or(f64::INFINITY))
            .collect()
    })
}

fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | 0x0b | 0x0c | b'\r')
}

/// `precise_xstrtod` pandas (`tokenizer.c`) на всём поле; `None` — не число (pandas перевёл бы столбец в `object`).
pub fn xstrtod(b: &[u8]) -> Option<f64> {
    let n = b.len();
    let mut p = 0;
    while p < n && is_space(b[p]) {
        p += 1;
    }
    let mut negative = false;
    if p < n && (b[p] == b'-' || b[p] == b'+') {
        negative = b[p] == b'-';
        p += 1;
    }
    const MAX_DIGITS: i32 = 17;
    let mut number = 0.0f64;
    let mut exponent = 0i32;
    let mut num_digits = 0i32;
    let mut num_decimals = 0i32;
    while p < n && b[p].is_ascii_digit() {
        if num_digits < MAX_DIGITS {
            number = number * 10.0 + f64::from(b[p] - b'0');
            num_digits += 1;
        } else {
            exponent += 1;
        }
        p += 1;
    }
    if p < n && b[p] == b'.' {
        p += 1;
        while num_digits < MAX_DIGITS && p < n && b[p].is_ascii_digit() {
            number = number * 10.0 + f64::from(b[p] - b'0');
            p += 1;
            num_digits += 1;
            num_decimals += 1;
        }
        if num_digits >= MAX_DIGITS {
            while p < n && b[p].is_ascii_digit() {
                p += 1;
            }
        }
        exponent -= num_decimals;
    }
    if num_digits == 0 {
        return None;
    }
    if negative {
        number = -number;
    }
    if p < n && (b[p] == b'e' || b[p] == b'E') {
        p += 1;
        let mut eneg = false;
        if p < n && (b[p] == b'-' || b[p] == b'+') {
            eneg = b[p] == b'-';
            p += 1;
        }
        let mut digits = 0;
        let mut e = 0i32;
        while p < n && b[p].is_ascii_digit() {
            e = e.saturating_mul(10).saturating_add(i32::from(b[p] - b'0'));
            digits += 1;
            p += 1;
        }
        if digits == 0 {
            p -= 1;
        }
        exponent = if eneg { exponent - e } else { exponent + e };
    }
    let e = pow10_table();
    if exponent > 308 {
        return None;
    } else if exponent > 0 {
        number *= e[exponent as usize];
    } else if exponent < -308 {
        if exponent < -616 {
            number = 0.0;
        } else {
            number /= e[(-308 - exponent) as usize];
            number /= e[308];
        }
    } else {
        number /= e[(-exponent) as usize];
    }
    while p < n && is_space(b[p]) {
        p += 1;
    }
    if p != n {
        return None;
    }
    Some(number)
}

/// Значения, которые `read_csv` по умолчанию считает пропуском (`na_values`).
fn is_na(b: &[u8]) -> bool {
    matches!(
        b,
        b"" | b"#N/A"
            | b"#N/A N/A"
            | b"#NA"
            | b"-1.#IND"
            | b"-1.#QNAN"
            | b"-NaN"
            | b"-nan"
            | b"1.#IND"
            | b"1.#QNAN"
            | b"<NA>"
            | b"N/A"
            | b"NA"
            | b"NULL"
            | b"NaN"
            | b"None"
            | b"n/a"
            | b"nan"
            | b"null"
    )
}

/// Поле float-столбца: пропуск → NaN, иначе `precise_xstrtod`; не число — ошибка (Python в этом месте упал бы).
pub fn parse_f64(b: &[u8]) -> Result<f64> {
    if is_na(b) {
        return Ok(f64::NAN);
    }
    match xstrtod(b) {
        Some(v) => Ok(v),
        None => bail!("не число: {:?}", String::from_utf8_lossy(b)),
    }
}

/// Поле int64-столбца (`transact_time`, `open_time`, `calc_time`, `funding_time`).
pub fn parse_i64(b: &[u8]) -> Result<i64> {
    let (neg, d) = match b.first() {
        Some(b'-') => (true, &b[1..]),
        Some(b'+') => (false, &b[1..]),
        _ => (false, b),
    };
    if d.is_empty() || d.len() > 18 || !d.iter().all(u8::is_ascii_digit) {
        bail!("не целое: {:?}", String::from_utf8_lossy(b));
    }
    let v = d.iter().fold(0i64, |a, &c| a * 10 + i64::from(c - b'0'));
    Ok(if neg { -v } else { v })
}

/// Дней от 1970-01-01 до гражданской даты (алгоритм Хиннанта).
pub fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// `tk594_feat.date_str`: сутки от 1970-01-01 → `YYYY-MM-DD`.
pub fn date_str(abs_day: i64) -> String {
    let z = abs_day + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}")
}

/// `YYYY-MM-DD` → сутки от 1970-01-01.
pub fn parse_date(s: &str) -> Result<i64> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        bail!("дата должна быть YYYY-MM-DD: {s}");
    }
    let n = |r: std::ops::Range<usize>| -> Result<i64> { parse_i64(&b[r]) };
    Ok(days_from_civil(n(0..4)?, n(5..7)?, n(8..10)?))
}

/// `create_time` `metrics` (`YYYY-MM-DD HH:MM:SS`, UTC) → мс от 1970; как `pd.to_datetime(...).astype("int64") // 10**6`.
pub fn parse_datetime_ms(b: &[u8]) -> Result<i64> {
    if b.len() != 19
        || b[4] != b'-'
        || b[7] != b'-'
        || (b[10] != b' ' && b[10] != b'T')
        || b[13] != b':'
        || b[16] != b':'
    {
        bail!(
            "время должно быть YYYY-MM-DD HH:MM:SS: {:?}",
            String::from_utf8_lossy(b)
        );
    }
    let n = |r: std::ops::Range<usize>| -> Result<i64> { parse_i64(&b[r]) };
    let days = days_from_civil(n(0..4)?, n(5..7)?, n(8..10)?);
    Ok(((days * 24 + n(11..13)?) * 60 + n(14..16)?) * 60_000 + n(17..19)? * 1000)
}
