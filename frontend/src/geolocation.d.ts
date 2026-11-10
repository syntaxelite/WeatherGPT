export interface BrowserCoordinates {
  latitude: number;
  longitude: number;
}

export declare function requestBrowserPosition(
  geolocation?: Pick<Geolocation, "getCurrentPosition">,
): Promise<BrowserCoordinates>;

export declare function locationFallbackMessage(error: unknown): string;
