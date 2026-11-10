export function requestBrowserPosition(geolocation = globalThis.navigator?.geolocation) {
  if (!geolocation) {
    return Promise.reject(Object.assign(new Error("Geolocation is unavailable."), { code: 0 }));
  }

  return new Promise((resolve, reject) => {
    geolocation.getCurrentPosition(
      (position) =>
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        }),
      reject,
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 300_000 },
    );
  });
}

export function locationFallbackMessage(error) {
  if (error?.code === 1) {
    return "Location permission was denied. Search for a city instead.";
  }
  if (error?.code === 2 || error?.code === 3) {
    return "Your location couldn't be determined. Search for a city instead.";
  }
  return "Location access isn't available in this browser. Search for a city instead.";
}
