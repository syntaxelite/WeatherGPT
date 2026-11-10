function isSavedCityList(value) {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        typeof item?.query === "string" &&
        item.query.trim().length > 0 &&
        typeof item?.name === "string" &&
        item.name.trim().length > 0,
    )
  );
}

export function parseSavedCities(serialized) {
  const parsed = JSON.parse(serialized);
  if (!isSavedCityList(parsed)) {
    throw new TypeError("Saved locations have an invalid format.");
  }
  return parsed;
}

export function toggleSavedCity(cities, city) {
  const match = city.query.toLocaleLowerCase();
  const alreadySaved = cities.some((saved) => saved.query.toLocaleLowerCase() === match);
  return alreadySaved
    ? cities.filter((saved) => saved.query.toLocaleLowerCase() !== match)
    : [...cities, city];
}

export function removeSavedCity(cities, query) {
  const match = query.toLocaleLowerCase();
  return cities.filter((saved) => saved.query.toLocaleLowerCase() !== match);
}
