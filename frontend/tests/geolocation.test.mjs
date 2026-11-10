import assert from "node:assert/strict";
import test from "node:test";
import { locationFallbackMessage, requestBrowserPosition } from "../src/geolocation.js";

test("uses browser-provided coordinates and conservative options", async () => {
  let requestedOptions;
  const geolocation = {
    getCurrentPosition(success, _failure, options) {
      requestedOptions = options;
      success({ coords: { latitude: 51.5, longitude: -0.12 } });
    },
  };

  assert.deepEqual(await requestBrowserPosition(geolocation), {
    latitude: 51.5,
    longitude: -0.12,
  });
  assert.deepEqual(requestedOptions, {
    enableHighAccuracy: false,
    timeout: 10_000,
    maximumAge: 300_000,
  });
});

test("reports denied location access with the manual-search fallback", async () => {
  const denied = Object.assign(new Error("Permission denied"), { code: 1 });
  const geolocation = {
    getCurrentPosition(_success, failure) {
      failure(denied);
    },
  };

  await assert.rejects(requestBrowserPosition(geolocation), denied);
  assert.match(locationFallbackMessage(denied), /permission was denied.*search for a city/i);
});

test("reports unavailable location access with the manual-search fallback", async () => {
  await assert.rejects(requestBrowserPosition(undefined), /unavailable/i);
  assert.match(locationFallbackMessage({ code: 0 }), /isn't available.*search for a city/i);
  assert.match(locationFallbackMessage({ code: 2 }), /couldn't be determined.*search for a city/i);
});
