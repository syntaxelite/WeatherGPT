export interface HourlyWeather {
  time: string;
  temperature_c: number;
  rain_probability_pct: number;
  precipitation_mm: number;
  wind_kmh: number;
  condition: string;
}

export interface DailyWeather {
  date: string;
  high_c: number;
  low_c: number;
  condition: string;
  rain_probability_pct: number;
}

export interface ActivityWindow {
  time: string;
  temperature_c: number;
  rain_probability_pct: number;
  precipitation_mm: number;
  wind_kmh: number;
  condition: string;
}

export function getActivityPlans(hours: HourlyWeather[]): Array<{
  activity: string;
  criteria: {
    maxRainProbabilityPct: number;
    maxWindKmh: number;
    minTemperatureC: number;
    maxTemperatureC: number;
  };
  windows: ActivityWindow[];
}>;
export function getDailyScale(days: DailyWeather[]): { min: number; max: number };
export function formatLocationDateTime(
  value: string,
  timezone?: string,
  options?: Intl.DateTimeFormatOptions,
): string;
export function formatLocationDate(value: string): { weekday: string; monthDay: string } | null;
