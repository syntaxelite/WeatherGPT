import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import app
from weather import LocationNotFoundError, WeatherServiceError


def weather_fixture():
    return {
        "timezone": "Europe/Helsinki",
        "current": {
            "temperature_c": 18.0,
            "feels_like_c": 17.0,
            "condition": "Partly cloudy",
            "humidity_pct": 55,
            "wind_kmh": 12.0,
            "rain_probability_pct": 20,
            "observed_at": "2026-10-05T12:00",
            "is_day": True,
        },
        "forecast": [
            {
                "date": f"2026-10-{day:02d}",
                "high_c": 20.0,
                "low_c": 10.0,
                "condition": "Partly cloudy",
                "rain_probability_pct": 20,
            }
            for day in range(5, 12)
        ],
        "hourly": [],
    }


class WeatherEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        app.app.config["TESTING"] = True

    def test_rejects_missing_and_empty_questions(self):
        self.assertEqual(
            self.client.post("/api/weather", json={}).status_code,
            400,
        )

        self.assertEqual(
            self.client.post(
                "/api/weather",
                json={"question": "  "},
            ).status_code,
            400,
        )

        self.assertEqual(
            self.client.post(
                "/api/weather",
                json={"question": "x" * 501},
            ).status_code,
            400,
        )

    def test_requires_json(self):
        response = self.client.post(
            "/api/weather",
            data="question?",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    @patch.object(app, "get_forecast", return_value=weather_fixture())
    def test_coordinate_request_returns_weather_without_calling_groq(self, forecast):
        response = self.client.post(
            "/api/weather",
            json={"latitude": 12.5, "longitude": -45.25},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["location"]["name"], "Your location")
        self.assertEqual(payload["location"]["latitude"], 12.5)
        self.assertEqual(payload["location"]["longitude"], -45.25)
        self.assertEqual(payload["location"]["timezone"], "Europe/Helsinki")
        forecast.assert_called_once()

    def test_coordinate_request_rejects_missing_and_invalid_coordinates(self):
        for body in (
            {"latitude": 12.5},
            {"latitude": 91, "longitude": 0},
            {"latitude": 0, "longitude": -181},
            {"latitude": "12.5", "longitude": 0},
        ):
            with self.subTest(body=body):
                response = self.client.post("/api/weather", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())

    @patch.object(app, "get_forecast", return_value=weather_fixture())
    @patch.object(
        app,
        "resolve_location",
        return_value={
            "name": "Helsinki",
            "region": "Uusimaa",
            "country": "Finland",
            "latitude": 60.17,
            "longitude": 24.94,
        },
    )
    def test_city_search_does_not_require_groq(self, resolve, forecast):
        response = self.client.post("/api/weather", json={"city": "Helsinki"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["location"]["name"], "Helsinki")
        self.assertEqual(response.get_json()["location"]["timezone"], "Europe/Helsinki")
        resolve.assert_called_once_with("Helsinki")
        forecast.assert_called_once()

    def test_reports_unconfigured_groq_without_traceback(self):
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.post(
                "/api/weather",
                json={
                    "question": "What's the weather in a city?"
                },
            )

        self.assertEqual(response.status_code, 503)

        self.assertEqual(
            response.get_json(),
            {
                "error": (
                    "Groq is not configured. "
                    "Add GROQ_API_KEY to backend/.env."
                )
            },
        )

    @patch.object(app, "Groq")
    def test_configures_groq_client(self, client_factory):
        with patch.dict(
            "os.environ",
            {
                "GROQ_API_KEY": "test-key",
                "GROQ_MODEL": "qwen/qwen3.8-27b",
            },
        ):
            app._groq_client()

        client_factory.assert_called_once_with(
            api_key="test-key"
        )

    @patch.object(app, "_groq_client")
    def test_maps_unavailable_groq_question_parse_to_clear_503_error(
        self,
        groq_client,
    ):
        error = Exception("Groq temporarily unavailable")
        error.status_code = 503

        groq_client.return_value.chat.completions.create.side_effect = error

        with self.assertRaisesRegex(
            app.AIServiceError,
            app.GROQ_BUSY_MESSAGE,
        ):
            app.understand_question(
                "Weather in a city"
            )

    @patch.object(app, "_groq_client")
    def test_answer_uses_only_retrieved_facts(self, groq_client):
        answer = app.generate_answer(
            "Weather in Helsinki today",
            {"name": "Helsinki"},
            weather_fixture(),
            0,
        )

        self.assertIn("20.0°C", answer)
        self.assertIn("10.0°C", answer)
        self.assertIn("Partly cloudy", answer)
        self.assertIn("20% chance of rain", answer)
        self.assertIn("55% humidity", answer)
        groq_client.assert_not_called()

    def test_answer_rejects_day_offset_outside_available_forecast(self):
        for offset in (7, -1, True, 1.5, "1"):
            with self.subTest(offset=offset):
                with self.assertRaisesRegex(app.AIServiceError, "forecast day is unavailable"):
                    app.generate_answer(
                        "Weather in Helsinki next week",
                        {"name": "Helsinki"},
                        weather_fixture(),
                        offset,
                    )

    def test_answer_rejects_an_insufficient_forecast_without_indexing(self):
        weather = weather_fixture()
        weather["forecast"] = weather["forecast"][:1]

        with self.assertRaisesRegex(app.AIServiceError, "forecast day is unavailable"):
            app.generate_answer(
                "Weather in Helsinki next week",
                {"name": "Helsinki"},
                weather,
                1,
            )

    @patch.object(
        app,
        "understand_question",
        side_effect=app.AIServiceError(
            app.GROQ_BUSY_MESSAGE
        ),
    )
    def test_returns_ai_busy_503_in_existing_api_error_shape(
        self,
        understand,
    ):
        response = self.client.post(
            "/api/weather",
            json={
                "question": "What's the weather in a city?"
            },
        )

        self.assertEqual(response.status_code, 503)

        self.assertEqual(
            response.get_json(),
            {
                "error": app.GROQ_BUSY_MESSAGE
            },
        )

        understand.assert_called_once()

    @patch.object(
        app,
        "generate_answer",
        side_effect=app.AIServiceError(
            app.GROQ_BUSY_MESSAGE
        ),
    )
    @patch.object(
        app,
        "get_forecast",
        return_value={
            "current": {},
            "forecast": [],
        },
    )
    @patch.object(
        app,
        "resolve_location",
        return_value={"name": "A city"},
    )
    @patch.object(
        app,
        "understand_question",
        return_value={
            "location": "A city",
            "day_offset": 0,
        },
    )
    def test_returns_ai_busy_503_when_answer_generation_is_unavailable(
        self,
        understand,
        resolve,
        forecast,
        answer,
    ):
        response = self.client.post(
            "/api/weather",
            json={
                "question": "What's the weather in a city?"
            },
        )

        self.assertEqual(response.status_code, 503)

        self.assertEqual(
            response.get_json(),
            {
                "error": app.GROQ_BUSY_MESSAGE
            },
        )

        understand.assert_called_once()
        resolve.assert_called_once()
        forecast.assert_called_once()
        answer.assert_called_once()

    @patch.object(
        app,
        "generate_answer",
        return_value="It is sunny today.",
    )
    @patch.object(app, "get_forecast")
    @patch.object(app, "resolve_location")
    @patch.object(
        app,
        "understand_question",
        return_value={
            "location": "Arbitrary town",
            "day_offset": 1,
        },
    )
    def test_returns_real_weather_payload_shape(
        self,
        understand,
        resolve,
        forecast,
        answer,
    ):
        resolve.return_value = {
            "name": "Arbitrary town",
            "region": "Example region",
            "country": "Example country",
            "latitude": 10.0,
            "longitude": 20.0,
        }

        forecast.return_value = {
            "timezone": "Europe/Helsinki",
            "current": {
                "temperature_c": 28.0
            },
            "forecast": [
                {"date": "2026-10-05"},
                {"date": "2026-10-06"},
            ],
        }

        response = self.client.post(
            "/api/weather",
            json={
                "question":
                    "Weather in Arbitrary town tomorrow"
            },
        )

        self.assertEqual(response.status_code, 200)

        payload = response.get_json()

        self.assertEqual(
            payload["location"]["name"],
            "Arbitrary town",
        )

        self.assertEqual(
            payload["weather"]["current"]["temperature_c"],
            28.0,
        )

        self.assertEqual(
            len(payload["weather"]["forecast"]),
            2,
        )

        self.assertNotIn(
            "latitude",
            payload["location"],
        )

        self.assertNotIn(
            "longitude",
            payload["location"],
        )

        answer.assert_called_once()
        understand.assert_called_once()
        self.assertEqual(payload["location"]["timezone"], "Europe/Helsinki")

    @patch.object(
        app,
        "resolve_location",
        side_effect=LocationNotFoundError(
            "Not found."
        ),
    )
    @patch.object(
        app,
        "understand_question",
        return_value={
            "location": "Nowhere",
            "day_offset": 0,
        },
    )
    def test_reports_unknown_location_without_weather_fabrication(
        self,
        understand,
        resolve,
    ):
        response = self.client.post(
            "/api/weather",
            json={
                "question": "Weather in Nowhere"
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertEqual(
            response.get_json(),
            {
                "error": "Not found."
            },
        )

    def test_health_check(self):
        self.assertEqual(
            self.client.get(
                "/api/health"
            ).get_json(),
            {"status": "ok"},
        )

    @patch.object(app, "get_forecast")
    @patch.object(app, "resolve_location")
    def test_city_comparison_returns_two_real_forecast_payloads(self, resolve, forecast):
        resolve.side_effect = [
            {"name": "Helsinki", "region": "Uusimaa", "country": "Finland", "latitude": 60.17, "longitude": 24.94},
            {"name": "Lisbon", "region": "Lisbon", "country": "Portugal", "latitude": 38.72, "longitude": -9.14},
        ]
        forecast.side_effect = [weather_fixture(), weather_fixture()]

        response = self.client.post(
            "/api/compare",
            json={"cities": ["Helsinki", "Lisbon"]},
        )

        self.assertEqual(response.status_code, 200)
        comparisons = response.get_json()["comparisons"]
        self.assertEqual([item["location"]["name"] for item in comparisons], ["Helsinki", "Lisbon"])
        self.assertEqual(comparisons[0]["weather"]["forecast"][0]["high_c"], 20.0)
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(forecast.call_count, 2)

    def test_city_comparison_validates_city_count_and_duplicate_names(self):
        for cities in ([], ["Helsinki"], ["Helsinki", "Lisbon", "Oslo"], ["Helsinki", " helsinki "]):
            with self.subTest(cities=cities):
                response = self.client.post("/api/compare", json={"cities": cities})
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())

    @patch.object(app, "get_forecast")
    @patch.object(app, "resolve_location", side_effect=LocationNotFoundError("Not found."))
    def test_city_comparison_reports_unresolved_city(self, resolve, forecast):
        response = self.client.post(
            "/api/compare",
            json={"cities": ["Nowhere", "Lisbon"]},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Not found."})
        forecast.assert_not_called()

    def test_production_frontend_route_reports_missing_build(self):
        with TemporaryDirectory() as directory:
            with patch.object(app, "FRONTEND_DIST", Path(directory)):
                response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertIn("frontend build is missing", response.get_json()["error"])

    def test_production_frontend_route_serves_the_built_index(self):
        with TemporaryDirectory() as directory:
            Path(directory, "index.html").write_text("<main>WeatherGPT</main>", encoding="utf-8")
            with patch.object(app, "FRONTEND_DIST", Path(directory)):
                response = self.client.get("/")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"WeatherGPT", response.data)
                response.close()

if __name__ == "__main__":
    unittest.main()