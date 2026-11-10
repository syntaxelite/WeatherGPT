import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Cloud,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Drop,
  Star,
  Sun,
  ThermometerSimple,
  Wind,
  type Icon,
} from "@phosphor-icons/react";
import { locationFallbackMessage, requestBrowserPosition } from "./geolocation.js";
import {
  formatLocationDate,
  formatLocationDateTime,
  getActivityPlans,
  getDailyScale,
} from "./weatherInsights.mjs";
import {
  parseSavedCities,
  removeSavedCity as removeSavedCityRecord,
  toggleSavedCity,
  type SavedCity,
} from "./savedLocations.mjs";

interface WeatherDay {
  date: string;
  high_c: number;
  low_c: number;
  condition: string;
  rain_probability_pct: number;
}

interface WeatherSnapshot {
  temperature_c: number;
  feels_like_c: number;
  condition: string;
  humidity_pct: number;
  wind_kmh: number;
  rain_probability_pct: number;
  observed_at: string;
  is_day: boolean;
}

interface HourlyWeather {
  time: string;
  temperature_c: number;
  rain_probability_pct: number;
  precipitation_mm: number;
  wind_kmh: number;
  condition: string;
}

interface WeatherResponse {
  answer: string;
  location: {
    name: string;
    region: string;
    country: string;
    latitude?: number;
    longitude?: number;
    timezone?: string;
  };
  weather: {
    timezone: string;
    current: WeatherSnapshot;
    forecast: WeatherDay[];
    hourly: HourlyWeather[];
  };
}

interface ConversationTurn {
  question: string;
  answer: string;
}

interface SelectedWeatherLocation {
  name: string;
  region: string;
  country: string;
  latitude?: number;
  longitude?: number;
}

type ViewState = "initial" | "loading" | "success" | "error";
type SearchMode = "city" | "question" | "compare";
type RequestBody =
  | { city: string }
  | {
      question: string;
      conversation?: ConversationTurn[];
      default_location?: SelectedWeatherLocation;
    }
  | { latitude: number; longitude: number };
interface ComparisonResponse {
  comparisons: WeatherResponse[];
}

function getConditionIcon(condition: string): Icon {
  const value = condition.toLowerCase();
  if (value.includes("thunder")) return CloudLightning;
  if (value.includes("snow") || value.includes("sleet")) return CloudSnow;
  if (value.includes("rain") || value.includes("drizzle")) return CloudRain;
  if (value.includes("fog")) return CloudFog;
  if (value.includes("cloud") || value.includes("overcast")) return Cloud;
  if (value.includes("clear") || value.includes("sun")) return Sun;
  return CloudSun;
}

function formatDay(date: string): { weekday: string; monthDay: string } {
  return formatLocationDate(date) ?? { weekday: "Forecast", monthDay: date };
}

function formatObservation(date: string, timezone: string): string {
  const formatted = formatLocationDateTime(date, timezone);
  return formatted === "Local time unavailable" ? formatted : `${formatted} local time`;
}

function formatTemperature(value: number): string {
  return `${Math.round(value)}°`;
}

function formatCoordinates(latitude: number, longitude: number): string {
  const latHemisphere = latitude < 0 ? "S" : "N";
  const lonHemisphere = longitude < 0 ? "W" : "E";
  return `${Math.abs(latitude).toFixed(3)}° ${latHemisphere}, ${Math.abs(longitude).toFixed(3)}° ${lonHemisphere}`;
}

function isWeatherResponse(value: unknown): value is WeatherResponse {
  if (typeof value !== "object" || value === null) return false;
  const response = value as Partial<WeatherResponse>;
  return (
    typeof response.answer === "string" &&
    typeof response.location?.name === "string" &&
    typeof response.location?.country === "string" &&
    typeof response.weather?.current?.temperature_c === "number" &&
    typeof response.weather?.timezone === "string" &&
    Array.isArray(response.weather?.forecast) &&
    Array.isArray(response.weather?.hourly)
  );
}

function isConversationalResponse(value: unknown): value is { type: "conversation"; answer: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    value.type === "conversation" &&
    "answer" in value &&
    typeof value.answer === "string" &&
    value.answer.trim().length > 0
  );
}

function isComparisonResponse(value: unknown): value is ComparisonResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "comparisons" in value &&
    Array.isArray(value.comparisons) &&
    value.comparisons.length === 2 &&
    value.comparisons.every(isWeatherResponse)
  );
}

function getWalkAdvice(weather: WeatherResponse["weather"]): {
  title: string;
  detail: string;
  evidence: string;
} {
  const today = weather.forecast[0];
  const current = weather.current;
  const condition = today?.condition.toLowerCase() ?? "";
  const severeConditions = ["thunder", "heavy rain", "freezing", "heavy snow"];
  const severe = severeConditions.some((term) => condition.includes(term));
  const rainChance = today?.rain_probability_pct ?? current.rain_probability_pct;
  const highWind = current.wind_kmh >= 35;

  if (severe || rainChance >= 60 || highWind) {
    return {
      title: "A walk can wait",
      detail: "Today's forecast points to weather that may make a walk less comfortable.",
      evidence: `${today?.condition ?? current.condition}, ${rainChance}% forecast rain chance, ${Math.round(current.wind_kmh)} km/h current wind.`,
    };
  }
  if (rainChance <= 25 && current.wind_kmh < 25 && !condition.includes("rain")) {
    return {
      title: "Looks good for a walk",
      detail: "Today's forecast looks relatively calm. Take a layer if the temperature calls for one.",
      evidence: `${today?.condition ?? current.condition}, ${rainChance}% forecast rain chance, ${Math.round(current.wind_kmh)} km/h current wind.`,
    };
  }
  return {
    title: "Check the timing",
    detail: "Conditions are mixed. A short look outside before you leave may help.",
    evidence: `${today?.condition ?? current.condition}, ${rainChance}% forecast rain chance, ${Math.round(current.wind_kmh)} km/h current wind.`,
  };
}

export default function App() {
  const locationRequestStarted = useRef(false);
  const retryRequest = useRef<{ body: RequestBody; query: string } | null>(null);
  const retryComparisonRequest = useRef<[string, string] | null>(null);
  const conversationHistory = useRef<ConversationTurn[]>([]);
  const selectedWeatherLocation = useRef<SelectedWeatherLocation | null>(null);
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("city");
  const [comparisonCities, setComparisonCities] = useState<[string, string]>(["", ""]);
  const [comparison, setComparison] = useState<ComparisonResponse | null>(null);
  const [savedCities, setSavedCities] = useState<SavedCity[]>([]);
  const [savedLocationsError, setSavedLocationsError] = useState("");
  const [currentCityQuery, setCurrentCityQuery] = useState("");
  const [viewState, setViewState] = useState<ViewState>("initial");
  const [result, setResult] = useState<WeatherResponse | null>(null);
  const [conversationAnswer, setConversationAnswer] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [locationMessage, setLocationMessage] = useState(
    "Allow location access when your browser asks. You can search for a city instead.",
  );
  const [locationFallback, setLocationFallback] = useState(false);
  const [submittedQuery, setSubmittedQuery] = useState("");
  const CurrentWeatherIcon = result ? getConditionIcon(result.weather.current.condition) : CloudSun;

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("weathergpt:saved-cities");
      if (!stored) return;
      setSavedCities(parseSavedCities(stored));
    } catch {
      setSavedLocationsError("Saved locations are unavailable because browser storage could not be read.");
    }
  }, []);

  function persistSavedCities(nextCities: SavedCity[]) {
    try {
      window.localStorage.setItem("weathergpt:saved-cities", JSON.stringify(nextCities));
      setSavedCities(nextCities);
      setSavedLocationsError("");
    } catch {
      setSavedLocationsError("This browser could not save the location. Check its storage settings and try again.");
    }
  }

  function saveCurrentCity() {
    if (!currentCityQuery || !result) return;
    const nextCities = toggleSavedCity(savedCities, {
      query: currentCityQuery,
      name: result.location.name,
    });
    persistSavedCities(nextCities);
  }

  function removeSavedCity(city: SavedCity) {
    persistSavedCities(removeSavedCityRecord(savedCities, city.query));
  }

  async function loadWeather(body: RequestBody, submitted: string) {
    setViewState("loading");
    setErrorMessage("");
    setConversationAnswer("");
    setComparison(null);
    retryComparisonRequest.current = null;
    retryRequest.current = { body, query: submitted };
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 45_000);

    try {
      const response = await fetch("/api/weather", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const message =
          typeof payload === "object" &&
          payload !== null &&
          "error" in payload &&
          typeof payload.error === "string"
            ? payload.error
            : "Weather data is temporarily unavailable. Please try again.";
        throw new Error(message);
      }
      if (isConversationalResponse(payload)) {
        setResult(null);
        setConversationAnswer(payload.answer);
        setSubmittedQuery(submitted);
        setCurrentCityQuery("");
        if ("question" in body) {
          conversationHistory.current = [
            ...conversationHistory.current,
            { question: body.question, answer: payload.answer },
          ].slice(-6);
        }
        setViewState("success");
        return true;
      }
      if (!isWeatherResponse(payload)) {
        throw new Error("The forecast came back incomplete. Please try again.");
      }

      setResult(payload);
      setConversationAnswer("");
      setSubmittedQuery(submitted);
      setCurrentCityQuery("city" in body ? body.city : "");
      selectedWeatherLocation.current = {
        name: payload.location.name,
        region: payload.location.region,
        country: payload.location.country,
        ...(payload.location.latitude !== undefined && payload.location.longitude !== undefined
          ? {
              latitude: payload.location.latitude,
              longitude: payload.location.longitude,
            }
          : {}),
      };
      if ("question" in body) {
        conversationHistory.current = [
          ...conversationHistory.current,
          { question: body.question, answer: payload.answer },
        ].slice(-6);
      }
      setViewState("success");
      return true;
    } catch (error) {
      setViewState("error");
      setErrorMessage(
        error instanceof DOMException && error.name === "AbortError"
          ? "This is taking longer than expected. Please try again."
          : error instanceof TypeError
            ? "I can't reach the weather service right now. Please try again."
            : error instanceof Error
              ? error.message
              : "Weather data is temporarily unavailable. Please try again.",
      );
      return false;
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function loadComparison(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cities: [string, string] = [
      comparisonCities[0].trim(),
      comparisonCities[1].trim(),
    ];
    if (cities.some((city) => !city) || viewState === "loading") return;
    await requestComparison(cities);
  }

  async function requestComparison(cities: [string, string]) {
    setViewState("loading");
    setErrorMessage("");
    setComparison(null);
    setResult(null);
    setConversationAnswer("");
    retryRequest.current = null;
    retryComparisonRequest.current = cities;

    try {
      const response = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cities }),
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const message =
          typeof payload === "object" &&
          payload !== null &&
          "error" in payload &&
          typeof payload.error === "string"
            ? payload.error
            : "Weather data is temporarily unavailable. Please try again.";
        throw new Error(message);
      }
      if (!isComparisonResponse(payload)) {
        throw new Error("The city comparison came back incomplete. Please try again.");
      }
      setComparison(payload);
      setViewState("success");
    } catch (error) {
      setViewState("error");
      setErrorMessage(
        error instanceof TypeError
          ? "I can't reach the weather service right now. Please try again."
          : error instanceof Error
            ? error.message
            : "Weather data is temporarily unavailable. Please try again.",
      );
    }
  }

  useEffect(() => {
    if (locationRequestStarted.current) return;
    locationRequestStarted.current = true;

    if (!navigator.geolocation) {
      setLocationFallback(true);
      setLocationMessage(locationFallbackMessage({ code: 0 }));
      return;
    }

    requestBrowserPosition()
      .then(async ({ latitude, longitude }) => {
        setLocationMessage("Using the location shared by your browser.");
        const loaded = await loadWeather({ latitude, longitude }, "Your location");
        if (!loaded) {
          setLocationFallback(true);
          setLocationMessage("We couldn't load your local forecast. Search for a city instead.");
          setErrorMessage("");
          setViewState("initial");
        }
      })
      .catch((error: unknown) => {
        setLocationFallback(true);
        setLocationMessage(locationFallbackMessage(error));
      });
  }, []);

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery || viewState === "loading") return;

    const body: RequestBody =
      searchMode === "city"
        ? { city: trimmedQuery }
        : {
            question: trimmedQuery,
            conversation: conversationHistory.current.slice(-6),
            ...(selectedWeatherLocation.current
              ? { default_location: selectedWeatherLocation.current }
              : {}),
          };
    setLocationFallback(false);
    setLocationMessage("");
    await loadWeather(body, trimmedQuery);
  }

  function retryLastRequest() {
    if (retryComparisonRequest.current) {
      void requestComparison(retryComparisonRequest.current);
      return;
    }
    if (retryRequest.current) {
      void loadWeather(retryRequest.current.body, retryRequest.current.query);
    }
  }

  const coordinates =
    result?.location.latitude !== undefined && result.location.longitude !== undefined
      ? formatCoordinates(result.location.latitude, result.location.longitude)
      : null;
  const currentCityIsSaved = savedCities.some(
    (saved) => saved.query.toLocaleLowerCase() === currentCityQuery.toLocaleLowerCase(),
  );

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="/" aria-label="WeatherGPT home">
          <span className="brand-mark" aria-hidden="true">
            <CloudSun size={22} weight="regular" />
          </span>
          <span>WeatherGPT</span>
        </a>
        <span className="header-note">Weather, close to home</span>
      </header>

      <main className="page-content">
        <section className="intro" aria-labelledby="page-title">
          <p className="eyebrow">A CLEARER LOOK AT YOUR DAY</p>
          <h1 id="page-title">
            Your day,
            <br />
            in <span>forecast.</span>
          </h1>
          <div className="intro-bottom">
            <p className="intro-copy">
              Current conditions and a practical seven-day outlook for wherever you are.
            </p>
            <p className="source-note">Live data from Open-Meteo</p>
          </div>
        </section>

        <section className="search-section" aria-label="Find weather">
          <div className="search-modes" role="group" aria-label="Search type">
            <button
              className={searchMode === "city" ? "mode-button selected" : "mode-button"}
              type="button"
              aria-pressed={searchMode === "city"}
              onClick={() => setSearchMode("city")}
            >
              Search a city
            </button>
            <button
              className={searchMode === "question" ? "mode-button selected" : "mode-button"}
              type="button"
              aria-pressed={searchMode === "question"}
              onClick={() => setSearchMode("question")}
            >
              Ask WeatherGPT
            </button>
            <button
              className={searchMode === "compare" ? "mode-button selected" : "mode-button"}
              type="button"
              aria-pressed={searchMode === "compare"}
              onClick={() => setSearchMode("compare")}
            >
              Compare cities
            </button>
          </div>
          {searchMode === "compare" ? (
            <form className="compare-form" onSubmit={loadComparison}>
              {comparisonCities.map((city, index) => (
                <div className="compare-input" key={index}>
                  <label htmlFor={`compare-city-${index}`}>
                    {index === 0 ? "First city" : "Second city"}
                  </label>
                  <input
                    id={`compare-city-${index}`}
                    type="text"
                    maxLength={200}
                    autoComplete="off"
                    placeholder={index === 0 ? "For example, Oslo" : "For example, Rome"}
                    value={city}
                    onChange={(event) =>
                      setComparisonCities((current) => {
                        const next: [string, string] = [...current];
                        next[index] = event.target.value;
                        return next;
                      })
                    }
                    disabled={viewState === "loading"}
                    required
                  />
                </div>
              ))}
              <button
                className="submit-button compare-submit"
                type="submit"
                disabled={viewState === "loading" || comparisonCities.some((city) => !city.trim())}
              >
                {viewState === "loading" ? (
                  <span className="submit-spinner" aria-hidden="true" />
                ) : (
                  <>
                    Compare <ArrowRight size={18} weight="regular" aria-hidden="true" />
                  </>
                )}
              </button>
            </form>
          ) : (
            <form className="question-form" onSubmit={submitSearch}>
              <label htmlFor="weather-search">
                {searchMode === "city" ? "City or town" : "Your weather question"}
              </label>
              <div className="search-input-row">
                <input
                  id="weather-search"
                  name="query"
                  type="text"
                  autoComplete="off"
                  maxLength={searchMode === "city" ? 200 : 500}
                  placeholder={searchMode === "city" ? "Try Reykjavík or Kyoto" : "Will it rain in Lisbon tomorrow?"}
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    if (viewState === "error") setViewState(result ? "success" : "initial");
                  }}
                  disabled={viewState === "loading"}
                  required
                />
                <button
                  className="submit-button"
                  type="submit"
                  aria-label={viewState === "loading" ? "Checking the forecast" : "Search weather"}
                  disabled={viewState === "loading" || !query.trim()}
                >
                  {viewState === "loading" ? (
                    <span className="submit-spinner" aria-hidden="true" />
                  ) : (
                    <ArrowRight size={21} weight="regular" aria-hidden="true" />
                  )}
                </button>
              </div>
            </form>
          )}
          <p className={locationFallback ? "location-message fallback" : "location-message"} role="status">
            {locationMessage}
          </p>
          <section className="saved-locations" aria-labelledby="saved-locations-title">
            <div className="saved-heading">
              <h2 id="saved-locations-title">Saved locations</h2>
              {savedCities.length > 0 && <span>{savedCities.length}</span>}
            </div>
            {savedCities.length > 0 ? (
              <ul className="saved-city-list">
                {savedCities.map((city) => (
                  <li key={city.query}>
                    <button
                      type="button"
                      className="saved-city-button"
                      onClick={() => {
                        setSearchMode("city");
                        setQuery(city.query);
                        void loadWeather({ city: city.query }, city.query);
                      }}
                      disabled={viewState === "loading"}
                    >
                      <Star size={15} weight="fill" aria-hidden="true" />
                      {city.name}
                    </button>
                    <button
                      type="button"
                      className="remove-saved-button"
                      aria-label={`Remove ${city.name} from saved locations`}
                      onClick={() => removeSavedCity(city)}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="saved-empty">Save a searched city to keep it close at hand.</p>
            )}
            {savedLocationsError && <p className="storage-error" role="status">{savedLocationsError}</p>}
          </section>
        </section>

        <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {viewState === "loading" && "Checking the latest weather and forecast."}
          {viewState === "success" && result && `Weather for ${result.location.name} is ready.`}
          {viewState === "success" && conversationAnswer && "WeatherGPT's answer is ready."}
          {viewState === "error" && errorMessage}
        </div>

        {viewState === "loading" && <LoadingState />}
        {viewState === "error" && (
          <ErrorState message={errorMessage} onRetry={retryLastRequest} />
        )}
        {viewState === "success" && comparison && (
          <CityComparison comparison={comparison} />
        )}

        {viewState === "success" && conversationAnswer && (
          <section className="weather-result" aria-label="WeatherGPT answer">
            <div className="answer-panel">
              <div>
                <p className="result-label">WEATHERGPT ANSWERS</p>
                <p className="asked-question">{submittedQuery}</p>
              </div>
              <p className="answer-text">{conversationAnswer}</p>
            </div>
          </section>
        )}

        {viewState === "success" && result && !comparison && (
          <section className="weather-result" aria-label={`Weather for ${result.location.name}`}>
            {result.answer && (
              <div className="answer-panel">
                <div>
                  <p className="result-label">YOUR FORECAST</p>
                  <p className="asked-question">{submittedQuery}</p>
                </div>
                <p className="answer-text">{result.answer}</p>
              </div>
            )}

            <div className="location-heading">
              <div>
                <p className="result-label">RIGHT NOW IN</p>
                <h2>{result.location.name}</h2>
                <p className="location-detail">
                  {coordinates ??
                    [result.location.region, result.location.country].filter(Boolean).join(", ")}
                </p>
                {currentCityQuery && (
                  <button className="save-location-button" type="button" onClick={saveCurrentCity}>
                    <Star size={15} weight={currentCityIsSaved ? "fill" : "regular"} aria-hidden="true" />
                    {currentCityIsSaved ? "Remove saved location" : "Save location"}
                  </button>
                )}
              </div>
              <p className="observed-at">
                Observed {formatObservation(result.weather.current.observed_at, result.weather.timezone)}
                <span>{result.weather.timezone.replaceAll("_", " ")}</span>
              </p>
            </div>

            <div className="current-weather">
              <div className="temperature-reading">
                <div className="temperature-line">
                  <span className="temperature-value">
                    {formatTemperature(result.weather.current.temperature_c)}
                  </span>
                  <span className="temperature-unit">°C</span>
                </div>
                <div className="condition-line">
                  <CurrentWeatherIcon size={23} weight="regular" aria-hidden="true" />
                  <span>{result.weather.current.condition}</span>
                </div>
              </div>
              <div className="weather-facts" aria-label="Current weather details">
                <WeatherFact
                  icon={ThermometerSimple}
                  label="Feels like"
                  value={`${Math.round(result.weather.current.feels_like_c)}°C`}
                />
                <WeatherFact
                  icon={Drop}
                  label="Humidity"
                  value={`${result.weather.current.humidity_pct}%`}
                />
                <WeatherFact
                  icon={Wind}
                  label="Wind"
                  value={`${Math.round(result.weather.current.wind_kmh)} km/h`}
                />
                <WeatherFact
                  icon={CloudRain}
                  label="Rain chance"
                  value={`${result.weather.current.rain_probability_pct}%`}
                />
              </div>
            </div>

            <WalkAdvice weather={result.weather} />
            <PlanMyDay hours={result.weather.hourly} timezone={result.weather.timezone} />

            <div className="forecast-section">
              <div className="forecast-heading">
                <h3>The days ahead</h3>
                <span>7-day outlook</span>
              </div>
              <div className="forecast-grid">
                {result.weather.forecast.slice(0, 7).map((day) => (
                  <ForecastDay key={day.date} day={day} />
                ))}
              </div>
            </div>
            <WeatherInsights weather={result.weather} />
          </section>
        )}

        {viewState === "initial" && <InitialNote locationFallback={locationFallback} />}
      </main>

      <footer className="site-footer">
        <span>WeatherGPT</span>
        <span>Weather data by Open-Meteo</span>
      </footer>
    </div>
  );
}

function WeatherFact({ icon: FactIcon, label, value }: { icon: Icon; label: string; value: string }) {
  return (
    <div className="weather-fact">
      <span className="fact-icon" aria-hidden="true">
        <FactIcon size={18} weight="regular" />
      </span>
      <span className="fact-label">{label}</span>
      <span className="fact-value">{value}</span>
    </div>
  );
}

function ForecastDay({ day }: { day: WeatherDay }) {
  const { weekday, monthDay } = formatDay(day.date);
  const DayIcon = getConditionIcon(day.condition);
  return (
    <article className="forecast-day" aria-label={`${weekday}, ${day.condition}`}>
      <div className="forecast-date">
        <span>{weekday}</span>
        <span>{monthDay}</span>
      </div>
      <DayIcon className="forecast-icon" size={25} weight="regular" aria-hidden="true" />
      <span className="forecast-condition">{day.condition}</span>
      <div className="forecast-temperatures">
        <span>{formatTemperature(day.high_c)}</span>
        <span>{formatTemperature(day.low_c)}</span>
      </div>
      <span className="forecast-rain">
        <Drop size={13} weight="regular" aria-hidden="true" />
        {day.rain_probability_pct}%
      </span>
    </article>
  );
}

function WalkAdvice({ weather }: { weather: WeatherResponse["weather"] }) {
  const advice = getWalkAdvice(weather);
  return (
    <section className="walk-advice" aria-labelledby="walk-title">
      <div className="walk-icon" aria-hidden="true">
        <Sun size={23} weight="regular" />
      </div>
      <div className="walk-copy">
        <p className="result-label">SHOULD I GO FOR A WALK?</p>
        <h3 id="walk-title">{advice.title}</h3>
        <p>{advice.detail}</p>
        <p className="walk-evidence">{advice.evidence}</p>
        <p className="walk-disclaimer">
          Based on today's forecast, not an official weather warning.
        </p>
        <details className="walk-rules">
          <summary>How this suggestion is made</summary>
          <p>
            We suggest waiting when rain chance is 60% or higher, wind is 35 km/h or higher, or
            the forecast reports thunder, heavy rain, freezing precipitation, or heavy snow. A
            calmer suggestion needs 25% rain chance or less, wind below 25 km/h, and no rain
            forecast.
          </p>
        </details>
      </div>
    </section>
  );
}

function PlanMyDay({ hours, timezone }: { hours: HourlyWeather[]; timezone: string }) {
  const plans = getActivityPlans(hours);
  return (
    <section className="day-plan" aria-labelledby="day-plan-title">
      <div className="section-heading">
        <div>
          <p className="result-label">MAKE THE MOST OF THE FORECAST</p>
          <h3 id="day-plan-title">Plan my day</h3>
        </div>
        <p>Hourly options for the next 24 hours</p>
      </div>
      <p className="plan-method">
        Times are ranked using forecast rain probability, temperature, wind, precipitation, and
        severe-weather conditions.
      </p>
      <div className="activity-plans">
        {plans.map((plan) => (
          <article className="activity-plan" key={plan.activity}>
            <h4>{plan.activity}</h4>
            {plan.windows.length > 0 ? (
              <ul>
                {plan.windows.map((window) => (
                  <li key={window.time}>
                    <span className="activity-time">
                      {formatLocationDateTime(window.time, timezone, {
                        weekday: "short",
                        hour: "numeric",
                      })}
                    </span>
                    <span className="activity-reason">
                      {Math.round(window.temperature_c)}°C is in the {plan.criteria.minTemperatureC}-
                      {plan.criteria.maxTemperatureC}°C range. Rain is {window.rain_probability_pct}%
                      {" "}(limit {plan.criteria.maxRainProbabilityPct}%), precipitation is{" "}
                      {window.precipitation_mm} mm, and wind is {Math.round(window.wind_kmh)} km/h
                      {" "}(limit {plan.criteria.maxWindKmh} km/h). {window.condition}.
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="activity-none">No forecast hour meets the activity thresholds.</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function WeatherInsights({ weather }: { weather: WeatherResponse["weather"] }) {
  const { hourly, forecast, timezone } = weather;
  const temperatures = hourly.map((hour) => hour.temperature_c);
  const minTemperature = Math.min(...temperatures);
  const temperatureSpan = Math.max(1, Math.max(...temperatures) - minTemperature);
  const points = hourly
    .map((hour, index) => {
      const x = 20 + (index / Math.max(1, hourly.length - 1)) * 680;
      const y = 16 + (1 - (hour.temperature_c - minTemperature) / temperatureSpan) * 112;
      return `${x},${y}`;
    })
    .join(" ");
  const scale = getDailyScale(forecast);
  const dailySpan = Math.max(1, scale.max - scale.min);

  return (
    <section className="weather-insights" aria-labelledby="insights-title">
      <div className="section-heading">
        <div>
          <p className="result-label">THE FORECAST AT A GLANCE</p>
          <h3 id="insights-title">Weather insights</h3>
        </div>
        <p>Local time in {timezone.replaceAll("_", " ")}</p>
      </div>

      <div className="insight-chart">
        <h4>Hourly temperature</h4>
        <div className="temperature-chart-scroll">
          <svg
            className="temperature-chart"
            viewBox="0 0 720 145"
            role="img"
            aria-label={`Hourly temperature from ${Math.round(minTemperature)} to ${Math.round(
              Math.max(...temperatures),
            )} degrees Celsius over the next 24 hours`}
            preserveAspectRatio="none"
          >
            <line x1="20" y1="128" x2="700" y2="128" className="chart-axis" />
            <polyline points={points} className="temperature-line-chart" />
            {hourly.map((hour, index) => {
              const x = 20 + (index / Math.max(1, hourly.length - 1)) * 680;
              const y =
                16 + (1 - (hour.temperature_c - minTemperature) / temperatureSpan) * 112;
              return (
                <circle
                  key={hour.time}
                  cx={x}
                  cy={y}
                  r="3"
                  className="temperature-point"
                >
                  <title>
                    {formatLocationDateTime(hour.time, timezone, { hour: "numeric" })}:{" "}
                    {Math.round(hour.temperature_c)}°C
                  </title>
                </circle>
              );
            })}
          </svg>
        </div>
        <div className="chart-ticks">
          {hourly.map((hour, index) =>
            index % 6 === 0 ? (
              <span key={hour.time}>
                {formatLocationDateTime(hour.time, timezone, { hour: "numeric" })}
              </span>
            ) : null,
          )}
        </div>
      </div>

      <div className="insight-chart">
        <h4>Hourly rain probability</h4>
        <div className="rain-chart" role="list" aria-label="Hourly rain probability over the next 24 hours">
          {hourly.map((hour) => (
            <div className="rain-column" role="listitem" key={hour.time}>
              <span
                className="rain-column-fill"
                style={{ height: `${hour.rain_probability_pct}%` }}
                title={`${formatLocationDateTime(hour.time, timezone, { hour: "numeric" })}: ${hour.rain_probability_pct}% rain probability`}
              />
              <span className="sr-only">
                {formatLocationDateTime(hour.time, timezone, { hour: "numeric" })}:{" "}
                {hour.rain_probability_pct}% chance of rain
              </span>
            </div>
          ))}
        </div>
        <div className="chart-ticks">
          {hourly.map((hour, index) =>
            index % 6 === 0 ? (
              <span key={hour.time}>
                {formatLocationDateTime(hour.time, timezone, { hour: "numeric" })}
              </span>
            ) : null,
          )}
        </div>
        <div className="chart-scale"><span>0%</span><span>100%</span></div>
      </div>

      <div className="insight-chart daily-trend">
        <h4>Seven-day temperature trend</h4>
        <div className="daily-trend-list">
          {forecast.slice(0, 7).map((day) => {
            const { weekday, monthDay } = formatDay(day.date);
            const left = ((day.low_c - scale.min) / dailySpan) * 100;
            const width = Math.max(2, ((day.high_c - day.low_c) / dailySpan) * 100);
            return (
              <div className="daily-trend-row" key={day.date}>
                <span className="daily-trend-date">{weekday}<small>{monthDay}</small></span>
                <span
                  className="daily-trend-track"
                  role="img"
                  aria-label={`${weekday}: low ${Math.round(day.low_c)} degrees, high ${Math.round(day.high_c)} degrees Celsius`}
                >
                  <span className="daily-trend-range" style={{ left: `${left}%`, width: `${width}%` }} />
                </span>
                <span className="daily-trend-values">
                  {Math.round(day.high_c)}° <span>{Math.round(day.low_c)}°</span>
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function CityComparison({ comparison }: { comparison: ComparisonResponse }) {
  const [first, second] = comparison.comparisons;
  if (!first || !second) return null;
  return (
    <section className="comparison-result" aria-label="City weather comparison">
      <div className="comparison-heading">
        <div>
          <p className="result-label">SIDE BY SIDE</p>
          <h2>City comparison</h2>
        </div>
        <p>Live conditions and seven-day forecasts from Open-Meteo.</p>
      </div>
      <div className="comparison-grid">
        {[first, second].map((city) => (
          <article className="comparison-city" key={city.location.name}>
            <div className="comparison-city-heading">
              <div>
                <p className="result-label">RIGHT NOW IN</p>
                <h3>{city.location.name}</h3>
                <p>{[city.location.region, city.location.country].filter(Boolean).join(", ")}</p>
              </div>
              <span className="comparison-temperature">
                {Math.round(city.weather.current.temperature_c)}°
              </span>
            </div>
            <p className="comparison-condition">{city.weather.current.condition}</p>
            <dl className="comparison-facts">
              <div><dt>Feels like</dt><dd>{Math.round(city.weather.current.feels_like_c)}°C</dd></div>
              <div><dt>Rain chance now</dt><dd>{city.weather.current.rain_probability_pct}%</dd></div>
              <div><dt>Wind</dt><dd>{Math.round(city.weather.current.wind_kmh)} km/h</dd></div>
              <div><dt>Humidity</dt><dd>{city.weather.current.humidity_pct}%</dd></div>
            </dl>
            <h4>Next seven days</h4>
            <div className="comparison-forecast">
              {city.weather.forecast.slice(0, 7).map((day) => {
                const { weekday } = formatDay(day.date);
                return (
                  <div key={day.date}>
                    <span>{weekday}</span>
                    <span>{Math.round(day.high_c)}° / {Math.round(day.low_c)}°</span>
                    <small>{day.rain_probability_pct}% rain</small>
                  </div>
                );
              })}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function LoadingState() {
  return (
    <section className="loading-state" aria-label="Loading forecast" aria-busy="true">
      <div className="loading-heading">
        <span className="loading-orbit" aria-hidden="true">
          <CloudSun size={29} weight="regular" />
        </span>
        <div>
          <p className="result-label">READING THE SKY</p>
          <p className="loading-copy">Finding your place and checking the latest forecast.</p>
        </div>
      </div>
      <div className="loading-rule" aria-hidden="true" />
      <div className="loading-lines" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </section>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="error-state" aria-labelledby="error-title" role="alert">
      <div className="error-copy">
        <p className="result-label">A QUICK PAUSE</p>
        <h2 id="error-title">{message}</h2>
        <p>Check the message and try again, or search for a city.</p>
      </div>
      <button className="retry-button" type="button" onClick={onRetry}>
        Try again <ArrowRight size={16} weight="regular" aria-hidden="true" />
      </button>
    </section>
  );
}

function InitialNote({ locationFallback }: { locationFallback: boolean }) {
  return (
    <div className="initial-note">
      <span className="initial-note-mark" aria-hidden="true">
        <Cloud size={20} weight="regular" />
      </span>
      <p>
        {locationFallback
          ? "Search any city to see current conditions and the forecast."
          : "Your local forecast will appear here when your location is ready."}
      </p>
    </div>
  );
}
