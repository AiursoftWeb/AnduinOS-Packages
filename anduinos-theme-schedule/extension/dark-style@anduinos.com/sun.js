const J2000 = 2451545.0;
const UNIX_JULIAN = 2440587.5;
const OBLIQUITY = 23.4397;
const ZENITH = -0.833;

export function julianDay(year, month, day) {
    const a = Math.trunc((14 - month) / 12);
    const y = year + 4800 - a;
    const m = month + 12 * a - 3;
    return day + Math.trunc((153 * m + 2) / 5) + 365 * y + Math.trunc(y / 4) -
        Math.trunc(y / 100) + Math.trunc(y / 400) - 32045;
}

function wrap360(value) {
    return ((value % 360) + 360) % 360;
}

function julianToUnix(julian) {
    return Math.round((julian - UNIX_JULIAN) * 86400);
}

export function eventsUtc(year, month, day, latitude, longitude) {
    const n = julianDay(year, month, day) - J2000 + 0.0008;
    const jStar = n - longitude / 360.0;
    const meanAnomalyDeg = wrap360(357.5291 + 0.98560028 * jStar);
    const meanAnomaly = meanAnomalyDeg * Math.PI / 180;
    const center = 1.9148 * Math.sin(meanAnomaly) +
        0.0200 * Math.sin(2 * meanAnomaly) +
        0.0003 * Math.sin(3 * meanAnomaly);
    const ecliptic = wrap360(meanAnomalyDeg + center + 180 + 102.9372) * Math.PI / 180;
    const jTransit = J2000 + jStar + 0.0053 * Math.sin(meanAnomaly) -
        0.0069 * Math.sin(2 * ecliptic);
    const declination = Math.asin(Math.sin(ecliptic) * Math.sin(OBLIQUITY * Math.PI / 180));
    const lat = latitude * Math.PI / 180;
    const denominator = Math.cos(lat) * Math.cos(declination);
    if (Math.abs(denominator) < Number.EPSILON)
        return null;
    const cosOmega = (Math.sin(ZENITH * Math.PI / 180) - Math.sin(lat) * Math.sin(declination)) /
        denominator;
    if (cosOmega < -1 || cosOmega > 1)
        return null;
    const omega = Math.acos(cosOmega) * 180 / Math.PI;
    return {
        sunrise: julianToUnix(jTransit - omega / 360),
        sunset: julianToUnix(jTransit + omega / 360),
    };
}

export function isDark(now, events) {
    return now < events.sunrise || now >= events.sunset;
}

export function nextTransition(now, events, tomorrow) {
    if (now < events.sunrise)
        return events.sunrise;
    if (now < events.sunset)
        return events.sunset;
    if (tomorrow)
        return tomorrow.sunrise;
    return now + 12 * 3600;
}

export function plan(now, year, month, day, nextYear, nextMonth, nextDay, latitude, longitude) {
    const today = eventsUtc(year, month, day, latitude, longitude);
    if (!today)
        return null;
    const tomorrow = eventsUtc(nextYear, nextMonth, nextDay, latitude, longitude);
    return {
        dark: isDark(now, today),
        next: nextTransition(now, today, tomorrow),
    };
}
