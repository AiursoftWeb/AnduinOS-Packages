//! NOAA / Wikipedia sunrise equation. Times are Unix seconds in UTC.

const J2000: f64 = 2_451_545.0;
const UNIX_JULIAN: f64 = 2_440_587.5;
const OBLIQUITY: f64 = 23.4397_f64;
const ZENITH: f64 = -0.833_f64;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SolarEvents {
    pub sunrise: i64,
    pub sunset: i64,
}

pub fn julian_day(year: i32, month: u32, day: u32) -> f64 {
    let month = month as i32;
    let day = day as i32;
    let a = (14 - month) / 12;
    let y = i64::from(year) + 4800 - i64::from(a);
    let m = i64::from(month) + 12 * i64::from(a) - 3;
    (i64::from(day) + (153 * m + 2) / 5 + 365 * y + y / 4 - y / 100 + y / 400 - 32045) as f64
}

fn julian_to_unix(julian: f64) -> i64 {
    ((julian - UNIX_JULIAN) * 86_400.0).round() as i64
}

fn wrap_360(value: f64) -> f64 {
    value.rem_euclid(360.0)
}

/// Civil sunrise and sunset for the UTC calendar date at `latitude`/`longitude`.
pub fn events_utc(year: i32, month: u32, day: u32, latitude: f64, longitude: f64) -> Option<SolarEvents> {
    let n = julian_day(year, month, day) - J2000 + 0.0008;
    let j_star = n - longitude / 360.0;
    let mean_anomaly = wrap_360(357.5291 + 0.985_600_28 * j_star).to_radians();
    let center = 1.9148 * mean_anomaly.sin()
        + 0.0200 * (2.0 * mean_anomaly).sin()
        + 0.0003 * (3.0 * mean_anomaly).sin();
    let ecliptic = wrap_360(mean_anomaly.to_degrees() + center + 180.0 + 102.9372).to_radians();
    let j_transit = J2000 + j_star + 0.0053 * mean_anomaly.sin() - 0.0069 * (2.0 * ecliptic).sin();
    let declination = (ecliptic.sin() * OBLIQUITY.to_radians().sin()).asin();
    let lat = latitude.to_radians();
    let numerator = ZENITH.to_radians().sin() - lat.sin() * declination.sin();
    let denominator = lat.cos() * declination.cos();
    if denominator.abs() < f64::EPSILON {
        return None;
    }
    let cos_omega = numerator / denominator;
    if !( -1.0..=1.0).contains(&cos_omega) {
        return None;
    }
    let omega = cos_omega.acos().to_degrees();
    Some(SolarEvents {
        sunrise: julian_to_unix(j_transit - omega / 360.0),
        sunset: julian_to_unix(j_transit + omega / 360.0),
    })
}

pub fn is_dark(now: i64, events: SolarEvents) -> bool {
    now < events.sunrise || now >= events.sunset
}

pub fn next_transition(now: i64, events: SolarEvents, tomorrow: Option<SolarEvents>) -> i64 {
    if now < events.sunrise {
        events.sunrise
    } else if now < events.sunset {
        events.sunset
    } else if let Some(tomorrow) = tomorrow {
        tomorrow.sunrise
    } else {
        now + 12 * 3600
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sydney_winter_has_morning_sunrise_and_afternoon_sunset() {
        // 2026-08-23 at Sydney Opera House. Values are UTC.
        let events = events_utc(2026, 8, 23, -33.8568, 151.2153).unwrap();
        let sunrise_hour = (events.sunrise.rem_euclid(86_400)) / 3600;
        let sunset_hour = (events.sunset.rem_euclid(86_400)) / 3600;
        assert!(
            (20..=21).contains(&sunrise_hour),
            "UTC sunrise hour {sunrise_hour}, unix {}",
            events.sunrise
        );
        assert!(
            (7..=8).contains(&sunset_hour),
            "UTC sunset hour {sunset_hour}, unix {}",
            events.sunset
        );
        assert!(events.sunset > events.sunrise);
    }

    #[test]
    fn dark_before_sunrise_and_after_sunset() {
        let events = SolarEvents {
            sunrise: 1_000,
            sunset: 2_000,
        };
        assert!(is_dark(999, events));
        assert!(!is_dark(1_000, events));
        assert!(!is_dark(1_500, events));
        assert!(is_dark(2_000, events));
    }
}
