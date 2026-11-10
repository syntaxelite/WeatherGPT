"""Flask API for natural-language, real-time weather answers."""

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask.wrappers import Response
from groq import Groq
from werkzeug.exceptions import HTTPException

from weather import (
    InvalidCoordinatesError,
    LocationNotFoundError,
    WeatherServiceError,
    get_forecast,
    resolve_coordinates,
    resolve_location,
)


# Load environment variables from backend/.env
load_dotenv(Path(__file__).resolve().with_name(".env"))


GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

MAX_QUESTION_LENGTH = 500
MAX_CITY_LENGTH = 200
MAX_CONVERSATION_TURNS = 6
MAX_CONVERSATION_ANSWER_LENGTH = 2000

GROQ_BUSY_MESSAGE = (
    "The AI service is temporarily busy. Please try again in a moment."
)

QUESTION_ACTIVITIES = {
    "general",
    "picnic",
    "travel",
    "outdoor",
    "running",
    "umbrella",
}

ACTIVITY_THRESHOLDS = {
    "picnic": {"rain": 18, "wind": 22, "min_temp": 16, "max_temp": 31},
    "running": {"rain": 30, "wind": 30, "min_temp": 6, "max_temp": 29},
    "outdoor": {"rain": 45, "wind": 38, "min_temp": 2, "max_temp": 34},
}


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIST / "assets"),
    static_url_path="/assets",
)
app.config["JSON_SORT_KEYS"] = False

logging.basicConfig(level=logging.INFO)


class AIServiceError(Exception):
    """AI service could not understand or answer a weather question."""


def _groq_client() -> Groq:
    """Create and return a Groq client."""

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key or not api_key.strip():
        raise AIServiceError(
            "Groq is not configured. Add GROQ_API_KEY to backend/.env."
        )

    return Groq(api_key=api_key.strip())


def _raise_groq_error(exc: Exception, fallback_message: str) -> None:
    """Convert Groq API errors into user-friendly application errors."""

    status_code = getattr(exc, "status_code", None)

    if status_code in {429, 500, 502, 503, 504}:
        app.logger.warning(
            "Groq temporarily unavailable: HTTP %s",
            status_code,
        )
        raise AIServiceError(GROQ_BUSY_MESSAGE) from exc

    app.logger.exception("%s", fallback_message)
    raise AIServiceError(fallback_message) from exc


def understand_question(
    question: str,
    conversation: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Use Qwen through Groq to understand the user's weather question.

    Returns:
        {
            "location": "...",
            "day_offset": 0,
            "activity": "picnic",
            "local_time": "15:00",
            "requires_live_weather": True
        }
    """

    client = _groq_client()

    prompt = (
        "Understand the user's weather request and return JSON only. "
        "Return an object with exactly these fields: "
        '{"is_weather_question": boolean, "requires_live_weather": boolean, '
        '"location": string, "day_offset": integer, "activity": string, '
        '"local_time": string}. '

        "is_weather_question is true for questions about weather, climate, "
        "or weather science, including conceptual questions that need no location. "
        "Set requires_live_weather to true only when answering requires actual "
        "current conditions or a forecast, including activity, travel, or umbrella "
        "advice based on actual weather; set it false for conceptual explanations. "
        "For live-weather questions, extract a place explicitly named in the current "
        "question or clearly established in the recent conversation. Do not invent "
        "a place or infer one from unrelated context. "

        "If the user uses a well-known alternate or historic spelling "
        "for the same place, use its canonical name supported by geocoding. "

        "Set location to an empty string for conceptual questions or when no "
        "relevant location is established. "

        "day_offset is 0 for today, 1 for tomorrow, and otherwise the "
        "requested day number from today's date "
        f"({date.today().isoformat()}). "

        "Use an offset from 0 through 6 because this service provides "
        "a seven-day forecast. "

        "Use 0 when no day is specified. "

        "Classify the practical intent as exactly one of: general "
        "(ordinary weather question), picnic, travel, outdoor "
        "(such as walking, hiking, or sightseeing), running, or umbrella "
        "(whether to bring one). "
        "Set local_time to the requested local clock time in 24-hour HH:MM "
        "format, or an empty string if no time is specified. Do not infer a time. "

        "Use the recent conversation only to understand follow-up references, "
        "not as a source of current weather data. "
        f"Recent conversation: {json.dumps(conversation or [], ensure_ascii=False)}. "
        f"The user's current question is: {json.dumps(question, ensure_ascii=False)}"
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a weather-question understanding assistant. "
                        "Return valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

    except Exception as exc:
        _raise_groq_error(
            exc,
            "I couldn't understand that weather question right now.",
        )

    try:
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)

    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise AIServiceError(
            "I couldn't understand that weather question right now."
        ) from exc

    if not isinstance(parsed, dict):
        raise AIServiceError(
            "I couldn't understand that weather question right now."
        )

    if not isinstance(parsed.get("is_weather_question"), bool):
        raise AIServiceError(
            "I couldn't understand that weather question right now."
        )

    if parsed["is_weather_question"] is not True:
        raise ValueError(
            "Ask me about the weather in a place."
        )

    requires_live_weather = parsed.get("requires_live_weather")
    if not isinstance(requires_live_weather, bool):
        raise AIServiceError(
            "I couldn't determine whether that question needs a live forecast."
        )

    location = parsed.get("location")
    day_offset = parsed.get("day_offset")

    if not isinstance(location, str):
        raise AIServiceError("I couldn't identify the requested location.")
    if type(day_offset) is not int or not 0 <= day_offset <= 6:
        raise ValueError(
            "I can check conditions for today and the next six days."
        )

    activity = parsed.get("activity")
    local_time = parsed.get("local_time")
    if not isinstance(activity, str) or activity not in QUESTION_ACTIVITIES:
        raise AIServiceError(
            "I couldn't identify the kind of weather advice you need."
        )
    if (
        not isinstance(local_time, str)
        or (local_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local_time))
    ):
        raise AIServiceError(
            "I couldn't identify the requested local time."
        )

    return {
        "location": location.strip(),
        "day_offset": day_offset,
        "activity": activity,
        "local_time": local_time or None,
        "requires_live_weather": requires_live_weather,
    }


def answer_conceptual_question(
    question: str,
    conversation: list[dict[str, str]] | None = None,
) -> str:
    """Answer a non-live weather or weather-science question through Groq."""

    client = _groq_client()
    messages = [
        {
            "role": "system",
            "content": (
                "You are WeatherGPT, a friendly assistant for weather and weather "
                "science questions. Answer the user's conceptual question directly "
                "in clear, conversational language, usually in two or three sentences. "
                "Use the recent conversation to resolve natural follow-ups. "
                "Do not claim or invent current conditions, forecasts, or other live "
                "weather data. If a question needs a live forecast, ask for a location "
                "instead of guessing. If the question is unrelated to weather, kindly "
                "ask for a weather or weather-science question."
            ),
        }
    ]
    for turn in conversation or []:
        messages.extend(
            [
                {"role": "user", "content": turn["question"]},
                {"role": "assistant", "content": turn["answer"]},
            ]
        )
    messages.append({"role": "user", "content": question})

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.4,
        )
    except Exception as exc:
        _raise_groq_error(
            exc,
            "I couldn't answer that weather question right now.",
        )

    try:
        answer = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise AIServiceError(
            "I couldn't answer that weather question right now."
        ) from exc

    if not isinstance(answer, str) or not answer.strip():
        raise AIServiceError(
            "I couldn't answer that weather question right now."
        )
    return answer.strip()


def _read_conversation(value: Any) -> list[dict[str, str]]:
    """Validate and bound client-provided follow-up context."""

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_CONVERSATION_TURNS:
        raise ValueError("Conversation context must contain at most six exchanges.")

    conversation = []
    for turn in value:
        if not isinstance(turn, dict):
            raise ValueError("Conversation context contains an invalid exchange.")
        prior_question = turn.get("question")
        prior_answer = turn.get("answer")
        if (
            not isinstance(prior_question, str)
            or not prior_question.strip()
            or len(prior_question) > MAX_QUESTION_LENGTH
            or not isinstance(prior_answer, str)
            or not prior_answer.strip()
            or len(prior_answer) > MAX_CONVERSATION_ANSWER_LENGTH
        ):
            raise ValueError("Conversation context contains an invalid exchange.")
        conversation.append(
            {
                "question": prior_question.strip(),
                "answer": prior_answer.strip(),
            }
        )
    return conversation


def _resolve_default_location(value: Any) -> dict[str, Any] | None:
    """Resolve the currently selected frontend location for a live question."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("The selected weather location is invalid.")

    name = value.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_CITY_LENGTH:
        raise ValueError("The selected weather location is invalid.")

    has_latitude = "latitude" in value
    has_longitude = "longitude" in value
    if has_latitude != has_longitude:
        raise ValueError("The selected weather location has incomplete coordinates.")
    if has_latitude:
        location = resolve_coordinates(value["latitude"], value["longitude"])
        location["name"] = name.strip()
        region = value.get("region", "")
        country = value.get("country", "")
        location["region"] = region if isinstance(region, str) else ""
        location["country"] = country if isinstance(country, str) else ""
        return location

    return resolve_location(name.strip())


def _display_date(value: str) -> str:
    """Format an Open-Meteo local forecast date for a conversational answer."""

    try:
        forecast_date = date.fromisoformat(value)
    except ValueError as exc:
        raise AIServiceError("The requested forecast date is unavailable.") from exc
    return f"{forecast_date.strftime('%A, %B')} {forecast_date.day}"


def _display_time(value: str) -> str:
    """Format a validated 24-hour local time in a compact 12-hour style."""

    hour, minute = (int(part) for part in value.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}" if minute else f"{display_hour} {suffix}"


def _hourly_forecast(weather: dict[str, Any]) -> list[dict[str, Any]]:
    hourly = weather.get("hourly_forecast")
    if not isinstance(hourly, list):
        hourly = weather.get("hourly")
    return hourly if isinstance(hourly, list) else []


def _is_severe_condition(condition: str) -> bool:
    severe_terms = (
        "thunder",
        "heavy rain",
        "freezing",
        "heavy snow",
        "violent",
        "hail",
    )
    normalized = condition.casefold()
    return any(term in normalized for term in severe_terms)


def _hourly_recommendation(activity: str, hour: dict[str, Any]) -> str:
    """Apply the assistant's weather-only thresholds to one forecast hour."""

    rain = hour["rain_probability_pct"]
    precipitation = hour["precipitation_mm"]
    wind = hour["wind_kmh"]
    temperature = hour["temperature_c"]
    condition = hour["condition"]
    severe = _is_severe_condition(condition)

    if "unavailable" in condition.casefold():
        return "It depends"

    if activity == "umbrella":
        if severe or rain >= 30 or precipitation > 0.2 or any(
            term in condition.casefold() for term in ("rain", "drizzle", "snow")
        ):
            return "Yes"
        return "No"

    if activity == "travel":
        if severe or rain >= 70 or wind >= 50:
            return "No"
        if rain <= 25 and wind < 30:
            return "Yes"
        return "It depends"

    thresholds = ACTIVITY_THRESHOLDS[activity]
    suitable = (
        not severe
        and rain <= thresholds["rain"]
        and precipitation <= 0.2
        and wind <= thresholds["wind"]
        and thresholds["min_temp"] <= temperature <= thresholds["max_temp"]
    )
    if suitable:
        return "Yes"
    if severe or rain >= 70 or wind >= 45 or temperature < 0 or temperature > 35:
        return "No"
    return "It depends"


def _hourly_answer(
    activity: str,
    location_name: str,
    forecast_date: str,
    local_time: str,
    hour: dict[str, Any],
) -> str:
    """Compose a brief, grounded answer for one forecast hour."""

    decision = _hourly_recommendation(activity, hour)
    time_label = _display_time(local_time)
    date_label = _display_date(forecast_date)
    condition = hour["condition"]
    rain = hour["rain_probability_pct"]
    wind = round(hour["wind_kmh"])
    temperature = round(hour["temperature_c"])
    precipitation = hour["precipitation_mm"]

    if activity == "general":
        return (
            f"Around {time_label} in {location_name} on {date_label}, expect "
            f"{condition.lower()}, about {temperature}°C, a {rain}% chance of rain, "
            f"and {wind} km/h winds."
        )
    if activity == "umbrella":
        if decision == "Yes":
            return (
                f"Yes—bring an umbrella around {time_label} in {location_name}: "
                f"{rain}% chance of rain"
                f"{f' and {precipitation:g} mm of precipitation' if precipitation > 0 else ''}."
            )
        if decision == "No":
            return (
                f"No, you likely won't need an umbrella around {time_label} in "
                f"{location_name}: the forecast shows {rain}% chance of rain and "
                f"no precipitation."
            )
        return (
            f"It depends: rain chance around {time_label} in {location_name} is "
            f"{rain}%, so check again closer to the time."
        )

    if activity == "travel":
        evidence = f"{condition.lower()}, {rain}% chance of rain, and {wind} km/h winds"
        action = "travel"
    else:
        evidence = (
            f"{condition.lower()}, {temperature}°C, {rain}% chance of rain, "
            f"and {wind} km/h winds"
        )
        if precipitation > 0.2:
            evidence += f", with {precipitation:g} mm of precipitation"
        action = {"picnic": "a picnic", "running": "running", "outdoor": "outdoor plans"}[activity]

    if activity == "travel":
        caveat = " This only assesses weather, not road or transit conditions."
    else:
        caveat = ""
    return (
        f"{decision}—{action.capitalize()} looks "
        f"{'suitable' if decision == 'Yes' else 'unfavorable' if decision == 'No' else 'uncertain'} "
        f"around {time_label} in {location_name}: {evidence}.{caveat}"
    )


def _daily_recommendation(
    activity: str,
    location_name: str,
    day: dict[str, Any],
    forecast_date: str,
    hourly: list[dict[str, Any]],
) -> str:
    """Answer activity questions without an hour using that day's forecast."""

    date_label = _display_date(forecast_date)
    condition = day["condition"]
    rain = day["rain_probability_pct"]

    if activity == "umbrella":
        if _is_severe_condition(condition) or rain >= 30 or any(
            term in condition.casefold() for term in ("rain", "drizzle", "snow")
        ):
            return f"Yes—take an umbrella in {location_name} on {date_label}: up to {rain}% chance of rain."
        condition_note = (
            ""
            if "unavailable" in condition.casefold()
            else f" Conditions are {condition.lower()}."
        )
        return (
            f"No, you likely won't need an umbrella in {location_name} on "
            f"{date_label}: up to {rain}% chance of rain.{condition_note}"
        )

    daytime_hours = [
        hour
        for hour in hourly
        if isinstance(hour.get("time"), str)
        and hour["time"][:10] == forecast_date
        and len(hour["time"]) >= 13
        and 7 <= int(hour["time"][11:13]) <= 20
    ]
    suitable_hours = [
        hour
        for hour in daytime_hours
        if _hourly_recommendation(activity, hour) == "Yes"
    ]
    if suitable_hours:
        best_hour = min(
            suitable_hours,
            key=lambda hour: (hour["rain_probability_pct"], hour["wind_kmh"]),
        )
        return _hourly_answer(
            activity,
            location_name,
            forecast_date,
            best_hour["time"][11:16],
            best_hour,
        )
    if not daytime_hours:
        return (
            f"It depends: I don't have daytime hourly data for {location_name} on "
            f"{date_label}, so I can't make a reliable {activity} recommendation."
        )

    high = round(day["high_c"])
    if (
        _is_severe_condition(condition)
        or rain >= 70
        or high < 0
        or high > 35
    ):
        decision = "No"
    else:
        decision = "It depends"
    return (
        f"{decision}: {location_name}'s forecast for {date_label} is {condition.lower()}, "
        f"with a high near {high}°C and up to {rain}% chance of rain; "
        f"I don't see a suitable daytime hour for {activity}."
    )


def generate_answer(
    question: str,
    location: dict[str, Any],
    weather: dict[str, Any],
    day_offset: int,
    activity: str = "general",
    local_time: str | None = None,
) -> str:
    """
    Compose a concise answer or recommendation from verified forecast values.
    """

    forecast = weather.get("forecast")
    if (
        not isinstance(forecast, list)
        or type(day_offset) is not int
        or not 0 <= day_offset < len(forecast)
    ):
        raise AIServiceError(
            "The requested forecast day is unavailable. Please try another day."
        )
    day = forecast[day_offset]
    location_name = location.get("name")
    if not isinstance(location_name, str) or not location_name:
        raise AIServiceError("The location details are incomplete.")
    if not isinstance(day, dict):
        raise AIServiceError("The requested forecast day is incomplete.")

    if not isinstance(activity, str) or activity not in QUESTION_ACTIVITIES:
        raise AIServiceError("The requested activity is unavailable.")
    if local_time is not None and (
        not isinstance(local_time, str)
        or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local_time)
    ):
        raise AIServiceError("The requested local time is unavailable.")

    forecast_date = day.get("date")
    if not isinstance(forecast_date, str):
        raise AIServiceError("The requested forecast date is unavailable.")
    date_label = _display_date(forecast_date)
    hourly = _hourly_forecast(weather)

    if local_time is not None:
        target_hour = f"{forecast_date}T{local_time[:2]}"
        selected_hour = next(
            (
                hour
                for hour in hourly
                if isinstance(hour, dict)
                and isinstance(hour.get("time"), str)
                and hour["time"][:13] == target_hour
            ),
            None,
        )
        if selected_hour is None:
            return (
                f"It depends: I don't have an hourly forecast for {location_name} "
                f"on {date_label} at {_display_time(local_time)}, so I can't give "
                "a reliable recommendation."
            )
        return _hourly_answer(
            activity,
            location_name,
            forecast_date,
            local_time,
            selected_hour,
        )

    if activity != "general":
        return _daily_recommendation(
            activity,
            location_name,
            day,
            forecast_date,
            hourly,
        )

    if "unavailable" in day["condition"].casefold():
        return (
            f"I can't confirm the conditions in {location_name} on {date_label}. "
            f"The forecast still shows a high near {round(day['high_c'])}°C, "
            f"a low around {round(day['low_c'])}°C, and up to "
            f"{day['rain_probability_pct']}% chance of rain."
        )

    return (
        f"For {location_name} on {date_label}, expect {day['condition'].lower()}, "
        f"a high near {round(day['high_c'])}°C, a low around {round(day['low_c'])}°C, "
        f"and up to {day['rain_probability_pct']}% chance of rain."
    )


def _error(
    message: str,
    status: int,
) -> tuple[Response, int]:
    """Return a consistent JSON error response."""

    return jsonify({"error": message}), status


@app.get("/api/health")
def health() -> tuple[Response, int]:
    """Health-check endpoint."""

    return jsonify({"status": "ok"}), 200


def _weather_result(location: dict[str, Any], weather: dict[str, Any], answer: str = "") -> dict[str, Any]:
    location["timezone"] = weather["timezone"]
    public_weather = {
        key: value
        for key, value in weather.items()
        if key != "hourly_forecast"
    }
    return {
        "answer": answer,
        "location": location,
        "weather": public_weather,
    }


@app.post("/api/weather")
def weather_endpoint() -> tuple[Response, int]:
    """Return weather for coordinates, a city, or a natural-language question."""

    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return _error(
            "Send a question in JSON format.",
            400,
        )

    has_latitude = "latitude" in body
    has_longitude = "longitude" in body
    if has_latitude or has_longitude:
        if not has_latitude or not has_longitude:
            return _error("Send both latitude and longitude.", 400)
        try:
            location = resolve_coordinates(body["latitude"], body["longitude"])
        except InvalidCoordinatesError as exc:
            return _error(str(exc), 400)

        try:
            weather = get_forecast(location)
        except WeatherServiceError:
            app.logger.exception("Open-Meteo request failed")
            return _error(
                "Weather data is temporarily unavailable. Please try again.",
                502,
            )

        return jsonify(
            _weather_result(
                {
                    key: location[key]
                    for key in ("name", "region", "country", "latitude", "longitude")
                },
                weather,
            )
        ), 200

    if "city" in body:
        city = body["city"]
        if not isinstance(city, str) or not city.strip():
            return _error("Enter a city or town to search.", 400)
        if len(city) > MAX_CITY_LENGTH:
            return _error("Keep the city name under 200 characters.", 400)

        try:
            location = resolve_location(city.strip())
            weather = get_forecast(location)
        except LocationNotFoundError as exc:
            return _error(str(exc), 404)
        except WeatherServiceError:
            app.logger.exception("Open-Meteo request failed")
            return _error(
                "Weather data is temporarily unavailable. Please try again.",
                502,
            )

        location.pop("latitude", None)
        location.pop("longitude", None)
        return jsonify(_weather_result(location, weather)), 200

    question = body.get("question")

    if not isinstance(question, str) or not question.strip():
        return _error(
            "Type a weather question to get started.",
            400,
        )

    if len(question) > MAX_QUESTION_LENGTH:
        return _error(
            "Keep your question under 500 characters.",
            400,
        )

    try:
        conversation = _read_conversation(body.get("conversation"))
    except ValueError as exc:
        return _error(str(exc), 400)

    # ---------------------------------------------------------
    # STEP 1: Understand the user's question using Qwen
    # ---------------------------------------------------------

    try:
        intent = understand_question(question.strip(), conversation)

    except ValueError as exc:
        return _error(str(exc), 400)

    except AIServiceError as exc:
        return _error(str(exc), 503)

    if not intent.get("requires_live_weather", True):
        try:
            answer = answer_conceptual_question(question.strip(), conversation)
        except AIServiceError as exc:
            return _error(str(exc), 503)
        return jsonify({"type": "conversation", "answer": answer}), 200

    # ---------------------------------------------------------
    # STEP 2: Find the requested location
    # ---------------------------------------------------------

    requested_location = intent["location"]
    try:
        if requested_location:
            location = resolve_location(requested_location)
        else:
            location = _resolve_default_location(body.get("default_location"))
            if location is None:
                return _error(
                    "Tell me a city or town so I can check the live weather.",
                    400,
                )

    except InvalidCoordinatesError as exc:
        return _error(str(exc), 400)
    except ValueError as exc:
        return _error(str(exc), 400)
    except LocationNotFoundError as exc:
        return _error(str(exc), 404)
    except WeatherServiceError:
        app.logger.exception("Open-Meteo location lookup failed")
        return _error(
            "Weather data is temporarily unavailable. Please try again.",
            502,
        )

    # ---------------------------------------------------------
    # STEP 3: Get real weather data from Open-Meteo
    # ---------------------------------------------------------

    try:
        weather = get_forecast(location)

    except WeatherServiceError:
        app.logger.exception(
            "Open-Meteo request failed"
        )

        return _error(
            "Weather data is temporarily unavailable. Please try again.",
            502,
        )

    # ---------------------------------------------------------
    # STEP 4: Compose an answer from verified weather facts
    # ---------------------------------------------------------

    try:
        answer = generate_answer(
            question.strip(),
            location,
            weather,
            intent["day_offset"],
            intent.get("activity", "general"),
            intent.get("local_time"),
        )

    except AIServiceError as exc:
        return _error(str(exc), 503)

    # Remove coordinates before sending location data to frontend
    location.pop("latitude", None)
    location.pop("longitude", None)

    # ---------------------------------------------------------
    # STEP 5: Send final response to React
    # ---------------------------------------------------------

    return jsonify(_weather_result(location, weather, answer)), 200


@app.post("/api/compare")
def compare_weather() -> tuple[Response, int]:
    """Compare current and forecast weather for two city names."""

    body = request.get_json(silent=True)
    cities = body.get("cities") if isinstance(body, dict) else None
    if (
        not isinstance(cities, list)
        or len(cities) != 2
        or any(not isinstance(city, str) or not city.strip() for city in cities)
    ):
        return _error("Enter exactly two city or town names to compare.", 400)
    if any(len(city) > MAX_CITY_LENGTH for city in cities):
        return _error("Keep each city name under 200 characters.", 400)
    if cities[0].strip().casefold() == cities[1].strip().casefold():
        return _error("Choose two different cities to compare.", 400)

    comparisons = []
    for city in cities:
        try:
            location = resolve_location(city.strip())
            weather = get_forecast(location)
        except LocationNotFoundError as exc:
            return _error(str(exc), 404)
        except WeatherServiceError:
            app.logger.exception("Open-Meteo request failed during city comparison")
            return _error(
                "Weather data is temporarily unavailable. Please try again.",
                502,
            )

        location.pop("latitude", None)
        location.pop("longitude", None)
        comparisons.append(_weather_result(location, weather))

    return jsonify({"comparisons": comparisons}), 200


@app.get("/")
def frontend_index() -> Response | tuple[Response, int]:
    """Serve the production frontend when a Vite build is available."""

    if not (FRONTEND_DIST / "index.html").is_file():
        return _error(
            "The frontend build is missing. Run npm run build in frontend.",
            503,
        )
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.errorhandler(HTTPException)
def handle_http_error(
    error: HTTPException,
) -> tuple[Response, int]:
    """Handle standard Flask HTTP errors."""

    message = "The request could not be completed."

    if error.code and error.code < 500:
        message = "Check the request and try again."

    return _error(
        message,
        error.code or 500,
    )


@app.errorhandler(Exception)
def handle_unexpected_error(
    error: Exception,
) -> tuple[Response, int]:
    """Handle unexpected application errors."""

    app.logger.exception(
        "Unexpected application error"
    )

    return _error(
        "Something went wrong. Please try again.",
        500,
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=8000,
        debug=False,
    )