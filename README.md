# WeatherGPT

WeatherGPT is a React, Vite, and TypeScript frontend backed by Flask. It uses Open-Meteo for geocoding and live weather forecasts, and Groq with Qwen for weather-question interpretation. The backend builds answers from retrieved forecast facts; the model does not invent or generate weather conditions. API keys stay on the backend.

Features include current conditions, seven-day forecasts, transparent walk advice, hourly activity planning, two-city comparison, saved cities in browser storage, hourly and daily weather visualizations, and weather questions.

## Requirements

- Python 3.10 or later
- Node.js 20 or later
- A Groq API key for natural-language weather questions (optional for city forecasts and comparisons)

The browser geolocation prompt is optional. Search for a city if you decline or cannot use location access. Geolocation requires localhost or HTTPS.

## Local setup

Open two PowerShell terminals from the project root.

### 1. Configure and run Flask

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

Set `GROQ_API_KEY` in `backend\.env` to enable weather questions. `GROQ_MODEL` defaults to `qwen/qwen3.8-27b`. City forecasts, activity planning, and comparisons work without a Groq key.

Start the API from the `backend` directory:

```powershell
Set-Location backend
python app.py
```

The Flask API listens on `http://127.0.0.1:8000`.

### 2. Run the Vite frontend

In the second terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` requests to Flask. For a production-style local run, build the frontend and start Flask from the project root:

```powershell
Set-Location frontend
npm run build
Set-Location ..\backend
python app.py
```

Then open `http://127.0.0.1:8000`. Flask serves the built app and its assets while keeping `/api` requests on the same origin.

## Production deployment

The included [render.yaml](./render.yaml) builds the Vite app and deploys Flask as a single web service. Connect the repository to Render, provide `GROQ_API_KEY` in the service environment to enable AI-assisted question interpretation, then deploy. The Flask WSGI entry point is `backend/app.py` (`app:app`); the frontend build is served from `frontend/dist`.

For another WSGI host, install `backend/requirements.txt`, run `npm ci` and `npm run build` in `frontend`, set `GROQ_API_KEY` if weather questions are needed, and start the application with a WSGI server such as Gunicorn using `backend/app.py` as the application module. Ensure the built `frontend/dist` directory is deployed alongside the backend.

## API routes

- `GET /api/health` - service health check
- `POST /api/weather` - forecast for a city, coordinates, or a natural-language question
- `POST /api/compare` - forecasts for two different cities
- `GET /` - serves the production frontend build

## Tests and build

Run the backend tests from `backend`:

```powershell
python -m unittest -v
```

Run frontend tests and the production TypeScript/Vite build from `frontend`:

```powershell
npm test
npm run build
```

The frontend test suite covers location-local date/time formatting, activity-window selection, trend scaling, and saved-location persistence logic. Backend tests cover Open-Meteo validation, timezone propagation, API errors, weather-answer grounding, comparisons, and production frontend routing.
