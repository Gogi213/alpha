//! TK-516: быстрый разбор десятичного числа в f64 — результат бит в бит равен `str::parse::<f64>`.
//!
//! Путь Клингера: `[-]цифры[.цифры]`, мантисса m ≤ 2^53 и дробных цифр k ≤ 22 — тогда m и 10^k точно представимы в f64, а деление
//! IEEE корректно округляет, т. е. m / 10^k = ближайшее к десятичному значению (то же, что даёт `str::parse`). Всё прочее
//! (порядок `e`, знак `+`, `inf`/`nan`, длинная мантисса, пусто, мусор) — `str::parse`, включая его ошибку (`unwrap` -> паника, как раньше).

const P10: [f64; 23] = [
    1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22,
];
const MAX_EXACT: u64 = 1 << 53;

#[inline]
pub fn parse_f64(s: &str) -> f64 {
    match fast(s.as_bytes()) {
        Some(v) => v,
        None => s.parse().unwrap(),
    }
}

/// То же из байтов (токен строки без проверки UTF-8 всей строки; не-UTF-8 токен вне быстрого пути — паника, как у `str::parse`).
#[inline]
pub fn parse_f64_b(b: &[u8]) -> f64 {
    match fast(b) {
        Some(v) => v,
        None => std::str::from_utf8(b).unwrap().parse().unwrap(),
    }
}

#[inline]
fn fast(b: &[u8]) -> Option<f64> {
    let (neg, b) = match b.first() {
        Some(b'-') => (true, &b[1..]),
        _ => (false, b),
    };
    let (mut m, mut digits, mut frac, mut seen_dot) = (0u64, 0usize, 0usize, false);
    for &c in b {
        let d = c.wrapping_sub(b'0');
        if d < 10 {
            // 19 цифр помещаются в u64 (10^19 − 1 < 2^64); дальше — общий путь
            if digits == 19 {
                return None;
            }
            m = m * 10 + d as u64;
            digits += 1;
            frac += seen_dot as usize;
        } else if c == b'.' && !seen_dot {
            seen_dot = true;
        } else {
            return None;
        }
    }
    if digits == 0 || m > MAX_EXACT || frac > 22 {
        return None;
    }
    let v = m as f64 / P10[frac];
    Some(if neg { -v } else { v })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn same(s: &str) {
        let a = parse_f64(s);
        let b: f64 = s.parse().unwrap();
        assert_eq!(a.to_bits(), b.to_bits(), "{s}");
    }

    #[test]
    fn boundary_inputs() {
        for s in [
            "0", "0.0", "-0", "-0.0", "1.", ".5", "-.5", "00012.50", "0.00000001", "123456789012345", "9007199254740992", "9007199254740993",
            "9007199254740993.5", "1234567890123456789", "12345678901234567890", "0.1", "0.2", "0.3", "0.30000000000000004", "1e5", "1E-5",
            "+1.5", "inf", "NaN", "0.0000000000000000000001", "0.00000000000000000000001", "1.0000000000000000000000001", "18446744073709551616",
            "100000000000000000000000", "5e-324", "1.7976931348623157e308", "-123.456", "4503599627370497.5",
        ] {
            same(s);
        }
    }

    #[test]
    #[should_panic]
    fn empty_panics_like_parse() {
        parse_f64("");
    }

    #[test]
    #[should_panic]
    fn dot_only_panics_like_parse() {
        parse_f64(".");
    }

    #[test]
    #[should_panic]
    fn minus_only_panics_like_parse() {
        parse_f64("-");
    }

    #[test]
    #[should_panic]
    fn two_dots_panics_like_parse() {
        parse_f64("1.2.3");
    }

    #[test]
    fn random_decimals_equal_str_parse() {
        let mut s = 0x9E37_79B9_7F4A_7C15u64;
        let mut rnd = |n: u64| {
            s ^= s << 13;
            s ^= s >> 7;
            s ^= s << 17;
            s % n
        };
        let mut buf = String::new();
        for _ in 0..3_000_000 {
            buf.clear();
            if rnd(8) == 0 {
                buf.push('-');
            }
            let int_d = rnd(12);
            let frac_d = rnd(26);
            for i in 0..int_d {
                buf.push((b'0' + if i == 0 && int_d > 1 { 1 + rnd(9) } else { rnd(10) } as u8) as char);
            }
            if int_d == 0 && frac_d == 0 {
                buf.push('0');
            }
            if frac_d > 0 || rnd(10) == 0 {
                buf.push('.');
            }
            for _ in 0..frac_d {
                buf.push((b'0' + rnd(10) as u8) as char);
            }
            same(&buf);
        }
    }

    #[test]
    fn random_near_2_pow_53() {
        for m in (MAX_EXACT - 5000)..(MAX_EXACT + 5000) {
            for k in [0usize, 1, 3, 8, 15] {
                let mut t = m.to_string();
                if k > 0 {
                    while t.len() <= k {
                        t.insert(0, '0');
                    }
                    t.insert(t.len() - k, '.');
                }
                same(&t);
            }
        }
    }
}
