import test from "node:test";
import assert from "node:assert/strict";

import {
  parseSavedCities,
  removeSavedCity,
  toggleSavedCity,
} from "../src/savedLocations.mjs";

test("loads persisted saved locations and rejects malformed entries", () => {
  assert.deepEqual(parseSavedCities('[{"query":"Oslo","name":"Oslo"}]'), [
    { query: "Oslo", name: "Oslo" },
  ]);
  assert.throws(() => parseSavedCities("{"), SyntaxError);
  assert.throws(() => parseSavedCities('[{"query":"","name":"Oslo"}]'), /invalid format/);
});

test("toggles a city without adding duplicates that differ only by case", () => {
  const saved = [{ query: "Oslo", name: "Oslo" }];
  assert.deepEqual(toggleSavedCity(saved, { query: "OSLO", name: "Oslo" }), []);
  assert.deepEqual(toggleSavedCity(saved, { query: "Rome", name: "Rome" }), [
    { query: "Oslo", name: "Oslo" },
    { query: "Rome", name: "Rome" },
  ]);
});

test("removes the requested saved city while retaining other locations", () => {
  assert.deepEqual(
    removeSavedCity(
      [
        { query: "Oslo", name: "Oslo" },
        { query: "Rome", name: "Rome" },
      ],
      "rome",
    ),
    [{ query: "Oslo", name: "Oslo" }],
  );
});
