import test from "node:test";
import assert from "node:assert/strict";

import {
  formatLocationDate,
  formatLocationDateTime,
  getActivityPlans,
  getDailyScale,
} from "../src/weatherInsights.mjs";

test("formats Open-Meteo wall-clock timestamps in the searched city's timezone", () => {
  const options = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };

  assert.equal(formatLocationDateTime("2026-10-05T12:00", "Asia/Tokyo", options), "12:00");
  assert.equal(formatLocationDateTime("2026-10-05T12:00", "America/Toronto", options), "12:00");
  assert.equal(formatLocationDateTime("not-a-date", "Asia/Tokyo", options), "Local time unavailable");
  assert.equal(formatLocationDateTime("2026-10-05T12:00", "Not/A_Timezone", options), "Local time unavailable");
});

test("formats date-only forecasts without the browser timezone shifting the day", () => {
  assert.deepEqual(formatLocationDate("2026-10-05"), {
    weekday: new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" })
      .format(Date.UTC(2026, 9, 5, 12)),
    monthDay: new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" })
      .format(Date.UTC(2026, 9, 5, 12)),
  });
  assert.equal(formatLocationDate("invalid"), null);
});

test("returns only forecast hours that meet each activity's weather thresholds", () => {
  const hours = [
    { time: "2026-10-05T08:00", temperature_c: 20, rain_probability_pct: 5, precipitation_mm: 0, wind_kmh: 8, condition: "Clear sky" },
    { time: "2026-10-05T12:00", temperature_c: 23, rain_probability_pct: 10, precipitation_mm: 0, wind_kmh: 12, condition: "Partly cloudy" },
    { time: "2026-10-05T16:00", temperature_c: 18, rain_probability_pct: 80, precipitation_mm: 2, wind_kmh: 45, condition: "Heavy rain" },
  ];

  const plans = getActivityPlans(hours);

  assert.equal(plans.length, 4);
  assert.deepEqual(plans.map((plan) => plan.activity), ["Walking", "Jogging", "Cycling", "Picnic"]);
  assert.ok(plans[0].windows.every((window) => window.rain_probability_pct <= 45));
  assert.ok(plans.every((plan) => plan.windows.every((window) => window.condition !== "Heavy rain")));
  assert.ok(plans[3].windows.length > 0);
});

test("does not invent activity windows when all hours are unsuitable", () => {
  const plans = getActivityPlans([
    { time: "2026-10-05T12:00", temperature_c: 8, rain_probability_pct: 90, precipitation_mm: 3, wind_kmh: 50, condition: "Thunderstorm" },
  ]);
  assert.ok(plans.every((plan) => plan.windows.length === 0));
});

test("calculates a real-data scale for the seven-day temperature trend", () => {
  assert.deepEqual(
    getDailyScale([{ date: "2026-10-05", high_c: 20, low_c: 10, condition: "Clear", rain_probability_pct: 0 }]),
    { min: 10, max: 20 },
  );
  assert.deepEqual(getDailyScale([]), { min: 0, max: 1 });
});
