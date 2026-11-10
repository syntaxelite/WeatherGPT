const activityRules = [
  { activity: "Walking", rain: 45, wind: 38, minTemp: 2, maxTemp: 34 },
  { activity: "Jogging", rain: 30, wind: 30, minTemp: 6, maxTemp: 29 },
  { activity: "Cycling", rain: 25, wind: 27, minTemp: 5, maxTemp: 31 },
  { activity: "Picnic", rain: 18, wind: 22, minTemp: 16, maxTemp: 31 },
];

const unsafeCondition = /thunder|heavy rain|freezing|heavy snow|violent/i;

export function getActivityPlans(hours) {
  return activityRules.map((rule) => {
    const candidates = hours
      .filter((hour) => {
        const hourOfDay = Number(hour.time.slice(11, 13));
        return hourOfDay >= 7 && hourOfDay <= 20;
      })
      .map((hour) => {
        const temperaturePenalty =
          Math.max(0, rule.minTemp - hour.temperature_c, hour.temperature_c - rule.maxTemp) * 3;
        const score =
          100 -
          hour.rain_probability_pct * 0.65 -
          hour.wind_kmh * 0.45 -
          temperaturePenalty -
          (hour.precipitation_mm > 0.2 ? 12 : 0);
        const suitable =
          hour.rain_probability_pct <= rule.rain &&
          hour.wind_kmh <= rule.wind &&
          hour.temperature_c >= rule.minTemp &&
          hour.temperature_c <= rule.maxTemp &&
          hour.precipitation_mm <= 0.2 &&
          !unsafeCondition.test(hour.condition);
        return { hour, score, suitable };
      })
      .filter((candidate) => candidate.suitable)
      .sort((left, right) => right.score - left.score)
      .slice(0, 3);

    return {
      activity: rule.activity,
      criteria: {
        maxRainProbabilityPct: rule.rain,
        maxWindKmh: rule.wind,
        minTemperatureC: rule.minTemp,
        maxTemperatureC: rule.maxTemp,
      },
      windows: candidates.map(({ hour }) => ({
        time: hour.time,
        temperature_c: hour.temperature_c,
        rain_probability_pct: hour.rain_probability_pct,
        precipitation_mm: hour.precipitation_mm,
        wind_kmh: hour.wind_kmh,
        condition: hour.condition,
      })),
    };
  });
}

export function getDailyScale(days) {
  if (!days.length) return { min: 0, max: 1 };
  const values = days.flatMap((day) => [day.low_c, day.high_c]);
  return { min: Math.min(...values), max: Math.max(...values) || 1 };
}

export function formatLocationDateTime(value, timezone, options) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return "Local time unavailable";
  const [, year, month, day, hour, minute] = match;
  const utcWallTime = Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute));
  const requestedOptions = options ?? { hour: "numeric", minute: "2-digit" };

  if (!timezone) {
    return new Intl.DateTimeFormat(undefined, { ...requestedOptions, timeZone: "UTC" }).format(utcWallTime);
  }

  let instant = utcWallTime;
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: timezone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(instant);
      const part = (type) => Number(parts.find((item) => item.type === type)?.value);
      const displayedAsUtc = Date.UTC(
        part("year"),
        part("month") - 1,
        part("day"),
        part("hour"),
        part("minute"),
      );
      instant += utcWallTime - displayedAsUtc;
    }
    return new Intl.DateTimeFormat(undefined, { ...requestedOptions, timeZone: timezone }).format(instant);
  } catch {
    return "Local time unavailable";
  }
}

export function formatLocationDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const utcDate = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12);
  return {
    weekday: new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" }).format(utcDate),
    monthDay: new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" }).format(utcDate),
  };
}
