# Vector Layer Integration Notes

## Current project state

- raster map is rendered from `tiles.json` and preview overlays
- `index.html` already defines `gameToLatLng(x, y)` and `latLngToGame(latlng)`
- the project works as a static site, so vector data should load with `fetch()`

## Suggested runtime structure

Load vector data after the base map metadata and tile overlays are ready:

```js
const [nations, cities, outposts, poi, roads] = await Promise.all([
  fetchJson("data/nations.json"),
  fetchJson("data/cities.json"),
  fetchJson("data/outposts.json"),
  fetchJson("data/poi.json"),
  fetchJson("data/roads.json")
]);
```

## Suggested helper tables

```js
const nationById = new Map(nations.map((nation) => [nation.id, nation]));
const cityById = new Map(cities.map((city) => [city.id, city]));
```

## Suggested color helpers

```js
const FREE_CITY_COLOR = "#9e9e9e";

function getCityColor(city, nationById) {
  if (!city.nation_id) return FREE_CITY_COLOR;
  return nationById.get(city.nation_id)?.color ?? FREE_CITY_COLOR;
}
```

## Validation rules to enforce in code

- skip or warn on outposts with missing `city_id`
- skip or warn on cities with unknown `nation_id`
- skip or warn on roads with fewer than two points
- keep rendering resilient: invalid objects should not break the full map

## Editing hooks to preserve

When adding interactive editing later, keep these seams isolated:

- `loadVectorData()`
- `validateVectorData()`
- `renderCities()`
- `renderOutposts()`
- `renderPoi()`
- `renderRoads()`
- `refreshVectorLayers()`

## Open design choices

- exact capital marker shape in Leaflet: SVG `divIcon` or canvas-based custom marker
- outpost shape: small circle is the simplest first implementation
- POI icon registry can start from a plain circle marker until assets exist
