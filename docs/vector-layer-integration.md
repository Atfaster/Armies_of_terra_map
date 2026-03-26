# Vector Layer Integration Notes

## Current project state

- raster map is rendered from `tiles.json` and preview overlays
- `index.html` defines `gameToLatLng(x, y)` and `latLngToGame(latlng)`
- the project works as a static site, so city data is loaded with `fetch()`

## Runtime loading

City markers now load from a single historical bundle:

```js
const cityDataset = await fetchJson("data/cities.json");
```

The bundle contains:

- `timeline`
- `nations`
- `cities`

## Suggested runtime structure

```js
const runtime = {
  cityDataset: null,
  nationById: new Map(),
  timelineDates: [],
  activeDate: null,
  showRuins: true
};
```

## Required helper functions

```js
function validateCityDataset(dataset) {}
function collectTimelineDates(dataset, cities) {}
function resolveCityState(city, activeDate) {}
function renderCities() {}
function setActiveDateByIndex(index) {}
```

## Timeline resolution

The client must select the state where:

```js
state.from <= activeDate && (!state.to || activeDate < state.to)
```

Notes:

- all state dates are ISO `YYYY-MM-DD`
- string comparison is safe for these dates
- if no state matches, the city is hidden for that date

## Color rules

```js
const FREE_CITY_COLOR = "#9e9e9e";
const RUINS_COLOR = "#6b7280";

function getCityColor(state, nationById) {
  if (state.status === "ruins") return RUINS_COLOR;
  if (!state.nation_id) return FREE_CITY_COLOR;
  return nationById.get(state.nation_id)?.color ?? FREE_CITY_COLOR;
}
```

## Validation rules to enforce in code

- skip or warn on cities without numeric `x` and `y`
- skip or warn on cities without `states`
- skip or warn on states with missing `from`
- skip or warn on states with unknown `nation_id`
- keep rendering resilient: invalid objects should not break the full map

## UI expectations

- a dedicated Leaflet layer for city markers
- a checkbox to show or hide ruins
- a horizontal timeline slider
- city labels rendered next to markers
- popup content taken from the active state

## Import workflow

The project includes:

```text
scripts/import_city_waypoints.py
```

Example usage:

```powershell
python scripts/import_city_waypoints.py `
  "C:\Users\atfas\curseforge\minecraft\Instances\Armies of Terra\journeymap\data\mp\Armies~of~Terra\waypoints" `
  "data\cities.json"
```

The importer currently:

- reads `town*.json`
- extracts `x` and `z`
- converts name fragments into `label`, `nation_id`, `status`, and `tags`
- emits one initial state per city using the waypoint file modification date

## Open design choices

- whether to keep manual historical edits directly in `data/cities.json`
- whether to introduce a second editable source format and compile it into the runtime bundle
- whether to extend the timeline control to outposts and POI in the same pass
