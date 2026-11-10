import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch

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
        "hourly_forecast": [],
    }


def hourly_fixture(
    *,
    condition="Partly cloudy",
    temperature=20.0,
    rain=10,
    precipitation=0.0,
    wind=12.0,
):
    return {
        "time": "2026-10-05T15:00",
        "temperature_c": temperature,
        "rain_probability_pct": rain,
        "precipitation_mm": precipitation,
        "wind_kmh": wind,
        "condition": condition,
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
        self.assertNotIn("hourly_forecast", response.get_json()["weather"])
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
    def test_understands_activity_and_local_time(self, groq_client):
        response = groq_client.return_value.chat.completions.create.return_value
        response.choices = [Mock(message=Mock(content=(
            '{"is_weather_question": true, "location": "Munnar", '
            '"day_offset": 0, "activity": "picnic", "local_time": "15:00"}'
        )))]

        self.assertEqual(
            app.understand_question("Can I go for a picnic in Munnar at 3 PM?"),
            {
                "location": "Munnar",
                "day_offset": 0,
                "activity": "picnic",
                "local_time": "15:00",
            },
        )

    @patch.object(app, "_groq_client")
    def test_answer_uses_only_retrieved_facts(self, groq_client):
        answer = app.generate_answer(
            "Weather in Helsinki today",
            {"name": "Helsinki"},
            weather_fixture(),
            0,
        )

        self.assertIn("20°C", answer)
        self.assertIn("10°C", answer)
        self.assertIn("partly cloudy", answer)
        self.assertIn("20% chance of rain", answer)
        self.assertNotIn("humidity", answer)
        self.assertNotIn("Current conditions", answer)
        groq_client.assert_not_called()

    def test_picnic_answer_uses_the_requested_hour_and_retrieved_weather(self):
        weather = weather_fixture()
        weather["hourly_forecast"] = [hourly_fixture()]

        answer = app.generate_answer(
            "Can I go for a picnic in Munnar at 3 PM?",
            {"name": "Munnar"},
            weather,
            0,
            "picnic",
            "15:00",
        )

        self.assertTrue(answer.startswith("Yes—"))
        self.assertIn("picnic", answer)
        self.assertIn("around 3 PM", answer)
        self.assertIn("10% chance of rain", answer)
        self.assertIn("12 km/h winds", answer)

    def test_activity_without_a_requested_hour_uses_that_days_best_window(self):
        weather = weather_fixture()
        weather["hourly_forecast"] = [
            {
                **hourly_fixture(),
                "time": "2026-10-05T08:00",
                "rain_probability_pct": 5,
            },
            hourly_fixture(),
        ]

        answer = app.generate_answer(
            "Can I go for a picnic in Munnar today?",
            {"name": "Munnar"},
            weather,
            0,
            "picnic",
        )

        self.assertTrue(answer.startswith("Yes—"))
        self.assertIn("around 8 AM", answer)
        self.assertIn("5% chance of rain", answer)

    def test_activity_answer_uses_a_severe_hour_to_recommend_against_it(self):
        weather = weather_fixture()
        weather["hourly_forecast"] = [
            hourly_fixture(
                condition="Thunderstorm",
                rain=85,
                precipitation=4.0,
                wind=48,
            )
        ]

        answer = app.generate_answer(
            "Can I go for a picnic in Munnar at 3 PM?",
            {"name": "Munnar"},
            weather,
            0,
            "picnic",
            "15:00",
        )

        self.assertTrue(answer.startswith("No—"))
        self.assertIn("thunderstorm", answer.casefold())
        self.assertIn("85% chance of rain", answer)

    def test_unavailable_hourly_condition_does_not_make_a_confident_recommendation(self):
        weather = weather_fixture()
        weather["hourly_forecast"] = [
            hourly_fixture(condition="Conditions unavailable")
        ]

        answer = app.generate_answer(
            "Can I go for a picnic in Munnar at 3 PM?",
            {"name": "Munnar"},
            weather,
            0,
            "picnic",
            "15:00",
        )

        self.assertTrue(answer.startswith("It depends"))

    def test_specific_time_without_hourly_data_reports_uncertainty(self):
        answer = app.generate_answer(
            "Can I go for a picnic in Munnar at 3 PM?",
            {"name": "Munnar"},
            weather_fixture(),
            0,
            "picnic",
            "15:00",
        )

        self.assertTrue(answer.startswith("It depends:"))
        self.assertIn("don't have an hourly forecast", answer)
        self.assertIn("3 PM", answer)

    def test_umbrella_answer_is_direct_and_uses_hourly_rain_data(self):
        weather = weather_fixture()
        weather["hourly_forecast"] = [hourly_fixture(rain=65, precipitation=0.8)]

        answer = app.generate_answer(
            "Should I bring an umbrella in Munnar at 3 PM?",
            {"name": "Munnar"},
            weather,
            0,
            "umbrella",
            "15:00",
        )

        self.assertTrue(answer.startswith("Yes—"))
        self.assertIn("65% chance of rain", answer)
        self.assertIn("0.8 mm of precipitation", answer)

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
            "activity": "picnic",
            "local_time": "15:00",
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
        self.assertEqual(answer.call_args.args[4:], ("picnic", "15:00"))
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