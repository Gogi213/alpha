mod aligned;

pub use aligned::{AlignedArray, CACHE_LINE_SIZE};

/// `f64::round` (half away from zero) without the libm call: bit-identical for every finite input.
pub trait RoundHa {
    fn round_ha(self) -> f64;
}

impl RoundHa for f64 {
    #[inline(always)]
    fn round_ha(self) -> f64 {
        const MAGIC: f64 = 4503599627370496.0;
        let a = self.abs();
        if a >= MAGIC {
            return self;
        }
        let mut r = (a + MAGIC) - MAGIC;
        if a - r >= 0.5 {
            r += 1.0;
        }
        r.copysign(self)
    }
}

/// Gets price precision.
///
/// * `tick_size` - This should not be a computed value.
pub fn get_precision(tick_size: f64) -> usize {
    let s = tick_size.to_string();
    let mut prec = 0;
    for (i, c) in s.chars().enumerate() {
        if c == '.' {
            prec = s.len() - i - 1;
            break;
        }
    }
    prec
}
