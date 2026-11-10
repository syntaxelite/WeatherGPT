export interface SavedCity {
  query: string;
  name: string;
}

export function parseSavedCities(serialized: string): SavedCity[];
export function toggleSavedCity(cities: SavedCity[], city: SavedCity): SavedCity[];
export function removeSavedCity(cities: SavedCity[], query: string): SavedCity[];
