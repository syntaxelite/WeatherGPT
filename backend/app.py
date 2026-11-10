"""Flask API for natural-language, real-time weather answers."""

import json
import logging
import os
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

GROQ_BUSY_MESSAGE = (
    "The AI service is temporarily busy. Please try again in a moment."
)


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


def understand_question(question: str) -> dict[str, Any]:
    """
    Use Qwen through Groq to understand the user's weather question.

    Returns:
        {
            "location": "...",
            "day_offset": 0
        }
    """

    client = _groq_client()

    prompt = (
        "Understand the user's weather request and return JSON only. "
        "The JSON must contain exactly these fields: "
        '{"is_weather_question": boolean, "location": string, "day_offset": integer}. '

        "Extract only a place explicitly named by the user. "
        "Do not guess or add a country or region. "

        "If the user uses a well-known alternate or historic spelling "
        "for the same place, use its canonical name supported by geocoding. "

        "Set location to an empty string if no place is named. "

        "day_offset is 0 for today, 1 for tomorrow, and otherwise the "
        "requested day number from today's date "
        f"({date.today().isoformat()}). "

        "Use an offset from 0 through 6 because this service provides "
        "a seven-day forecast. "

        "Use 0 when no day is specified. "

        f"The user's question is: {json.dumps(question, ensure_ascii=False)}"
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

    location = parsed.get("location")
    day_offset = parsed.get("day_offset")

    if not isinstance(location, str) or len(location.strip()) < 2:
        raise ValueError(
            "Include a city or town so I know where to check."
        )

    if type(day_offset) is not int or not 0 <= day_offset <= 6:
        raise ValueError(
            "I can check conditions for today and the next six days."
        )

    return {
        "location": location.strip(),
        "day_offset": day_offset,
    }


def generate_answer(
    question: str,
    location: dict[str, Any],
    weather: dict[str, Any],
    day_offset: int,
) -> str:
    """
    Compose a concise answer from verified forecast values.
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
    current = weather["current"]
    location_name = location.get("name")
    if not isinstance(location_name, str) or not location_name:
        raise AIServiceError("The location details are incomplete.")
    if not isinstance(day, dict):
        raise AIServiceError("The requested forecast day is incomplete.")

    return (
        f"In {location_name}, the forecast for {day['date']} is {day['condition']}, "
        f"with a high of {day['high_c']}°C, a low of {day['low_c']}°C, "
        f"and up to {day['rain_probability_pct']}% chance of rain. "
        f"Current conditions are {current['temperature_c']}°C and "
        f"{current['condition']}, with {current['humidity_pct']}% humidity "
        f"and winds at {current['wind_kmh']} km/h."
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
    return {
        "answer": answer,
        "location": location,
        "weather": weather,
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

    # ---------------------------------------------------------
    # STEP 1: Understand the user's question using Qwen
    # ---------------------------------------------------------

    try:
        intent = understand_question(question.strip())

    except ValueError as exc:
        return _error(str(exc), 400)

    except AIServiceError as exc:
        return _error(str(exc), 503)

    # ---------------------------------------------------------
    # STEP 2: Find the requested location
    # ---------------------------------------------------------

    try:
        location = resolve_location(intent["location"])

    except LocationNotFoundError as exc:
        return _error(str(exc), 404)

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