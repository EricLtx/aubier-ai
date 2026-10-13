//! `vk liveness renew --until`: a moment given as `+30d` (from now) or as an
//! RFC 3339 timestamp, turned into the time-to-live the daemon's
//! `liveness.renew` takes. No date crate: the two forms are small, and the
//! shell has no other use for one.
use anyhow::{anyhow, bail, Result};

/// The longest a lease may be renewed for in one call; the daemon's own limit
/// (`MAX_LIVENESS_TTL_MS` in `vk-ipc`), stated here so the refusal can name it
/// before anything is signed.
pub const MAX_TTL_MS: u64 = 30 * 24 * 60 * 60 * 1000;

/// What `--until` defaults to.
pub const DEFAULT_UNTIL: &str = "+7d";

/// Milliseconds from `now_ms` until `spec`, which must be a moment in the
/// future no more than thirty days away.
pub fn ttl_ms(spec: &str, now_ms: u64) -> Result<u64> {
    let spec = spec.trim();
    let ttl = if let Some(rel) = spec.strip_prefix('+') {
        relative_ms(rel)?
    } else {
        let at = rfc3339_ms(spec)?;
        at.checked_sub(now_ms)
            .filter(|t| *t > 0)
            .ok_or_else(|| anyhow!("--until {spec} is not in the future"))?
    };
    if ttl == 0 || ttl > MAX_TTL_MS {
        bail!("--until {spec}: a lease runs between a millisecond and thirty days (+30d)");
    }
    Ok(ttl)
}

/// `30d`, `12h`, `90m`, `45s`.
fn relative_ms(rel: &str) -> Result<u64> {
    let bad = || anyhow!("--until +{rel}: expected a number and a unit (+30d, +12h, +90m, +45s)");
    let unit = rel.chars().last().ok_or_else(bad)?;
    // Digits only: `u64::parse` would take a second `+`; and the number is
    // cut where the unit character starts, so a multi-byte unit cannot panic.
    let number = &rel[..rel.len() - unit.len_utf8()];
    if number.is_empty() || !number.bytes().all(|c| c.is_ascii_digit()) {
        return Err(bad());
    }
    let n: u64 = number.parse().map_err(|_| bad())?;
    let per = match unit {
        'd' => 86_400_000,
        'h' => 3_600_000,
        'm' => 60_000,
        's' => 1_000,
        _ => return Err(bad()),
    };
    n.checked_mul(per).ok_or_else(bad)
}

/// `YYYY-MM-DDTHH:MM:SS[.fff](Z|+HH:MM|-HH:MM)` as milliseconds since the Unix epoch.
fn rfc3339_ms(s: &str) -> Result<u64> {
    let bad = || anyhow!("--until {s}: expected +30d or an RFC 3339 time (2026-11-01T09:00:00Z)");
    let b = s.as_bytes();
    let digits = |from: usize, len: usize| -> Result<i64> {
        let part = s.get(from..from + len).ok_or_else(bad)?;
        if !part.bytes().all(|c| c.is_ascii_digit()) {
            return Err(bad());
        }
        part.parse().map_err(|_| bad())
    };
    if b.len() < 20 || b[4] != b'-' || b[7] != b'-' || !matches!(b[10], b'T' | b't' | b' ') {
        return Err(bad());
    }
    if b[13] != b':' || b[16] != b':' {
        return Err(bad());
    }
    let (y, mo, d) = (digits(0, 4)?, digits(5, 2)?, digits(8, 2)?);
    let (h, mi, sec) = (digits(11, 2)?, digits(14, 2)?, digits(17, 2)?);
    let month_len = match mo {
        2 if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    if !(1..=12).contains(&mo) || !(1..=month_len).contains(&d) || h > 23 || mi > 59 || sec > 59 {
        return Err(bad());
    }
    let mut i = 19;
    let mut frac_ms = 0i64;
    if b[i] == b'.' {
        let start = i + 1;
        i = start;
        while i < b.len() && b[i].is_ascii_digit() {
            i += 1;
        }
        if i == start {
            return Err(bad());
        }
        let milli: String = s[start..i].chars().chain("00".chars()).take(3).collect();
        frac_ms = milli.parse().map_err(|_| bad())?;
    }
    let offset_s = match &s[i..] {
        "Z" | "z" => 0,
        tz if tz.len() == 6 && tz.as_bytes()[3] == b':' => {
            let sign = match tz.as_bytes()[0] {
                b'+' => 1,
                b'-' => -1,
                _ => return Err(bad()),
            };
            let (oh, om) = (digits(i + 1, 2)?, digits(i + 4, 2)?);
            if oh > 23 || om > 59 {
                return Err(bad());
            }
            sign * (oh * 3600 + om * 60)
        }
        _ => return Err(bad()),
    };
    let secs = days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + sec - offset_s;
    u64::try_from(secs * 1000 + frac_ms).map_err(|_| bad())
}

/// Days since 1970-01-01 of a proleptic Gregorian date (Hinnant's algorithm).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_700_000_000_000; // 2023-11-14T22:13:20Z

    #[test]
    fn relative_forms() {
        assert_eq!(ttl_ms("+30d", NOW).unwrap(), MAX_TTL_MS);
        assert_eq!(ttl_ms("+12h", NOW).unwrap(), 12 * 3_600_000);
        assert_eq!(ttl_ms("+90m", NOW).unwrap(), 90 * 60_000);
        assert!(ttl_ms("+31d", NOW).is_err());
        assert!(ttl_ms("+0d", NOW).is_err());
        assert!(ttl_ms("+d", NOW).is_err());
        assert!(ttl_ms("+5w", NOW).is_err());
        // A multi-byte unit is refused, not a panic; so is a doubled sign.
        assert!(ttl_ms("+5é", NOW).is_err());
        assert!(ttl_ms("+é", NOW).is_err());
        assert!(ttl_ms("++5d", NOW).is_err());
    }

    #[test]
    fn rfc3339_is_read_with_its_offset() {
        assert_eq!(rfc3339_ms("1970-01-01T00:00:00Z").unwrap(), 0);
        assert_eq!(rfc3339_ms("2023-11-14T22:13:20Z").unwrap(), NOW);
        assert_eq!(rfc3339_ms("2023-11-14T23:13:20+01:00").unwrap(), NOW);
        assert_eq!(rfc3339_ms("2023-11-14T22:13:20.500Z").unwrap(), NOW + 500);
        assert_eq!(
            rfc3339_ms("2024-02-29T00:00:00Z").unwrap(),
            1_709_164_800_000
        );
        assert_eq!(ttl_ms("2023-11-15T22:13:20Z", NOW).unwrap(), 86_400_000);
    }

    #[test]
    fn the_past_and_the_far_future_are_refused() {
        assert!(ttl_ms("2023-11-14T22:13:20Z", NOW).is_err());
        assert!(ttl_ms("2023-01-01T00:00:00Z", NOW).is_err());
        assert!(ttl_ms("2024-11-14T22:13:20Z", NOW).is_err());
        assert!(ttl_ms("tomorrow", NOW).is_err());
        assert!(ttl_ms("2023-13-14T22:13:20Z", NOW).is_err());
        assert!(ttl_ms("2023-02-31T00:00:00Z", NOW).is_err());
        assert!(ttl_ms("2023-11-15T22:13:20+99:99", NOW).is_err());
    }
}
