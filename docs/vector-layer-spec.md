# Feature Spec: Historical City Markers

## Overview

This project renders city markers on top of the raster Leaflet map and supports historical playback.

Core requirements:

- city markers are loaded from static JSON
- each city can have multiple historical states
- the map shows the state active for the selected timeline date
- ruined or unclaimed cities are not deleted from data
- ruined cities can be hidden with a dedicated filter

All vector data uses Minecraft coordinates:

```text
X -> right
Y -> down
(0,0) -> top-left
```

For imported JourneyMap waypoints:

- source `x` maps to map `x`
- source `z` maps to map `y`
- source `y` is treated as elevation only and is not used for marker placement

## Storage

City runtime data is stored in a single static bundle:

```text
data/
  cities.json
```

This bundle contains:

- timeline metadata
- nations used by city states
- city definitions with state history

Other vector layers may continue to use separate files later.

## Runtime JSON model

```json
{
  "schema_version": 1,
  "generated_at": "2026-03-26T23:16:02+00:00",
  "timeline": {
    "dates": ["2026-03-25", "2026-04-01"],
    "default_date": "2026-04-01"
  },
  "nations": [
    {
      "id": "avernia",
      "name": "Avernia",
      "color": "#d46f35"
    }
  ],
  "cities": [
    {
      "id": "nova-roma",
      "name": "Nova Roma",
      "kind": "city",
      "x": 6064,
      "y": -103,
      "states": [
        {
          "from": "2026-03-25",
          "to": "2026-04-01",
          "status": "active",
          "nation_id": "avernia",
          "label": "Nova Roma",
          "tags": [],
          "source": {
            "type": "journeymap-waypoint",
            "file": "town-Nova_Roma-nation-Avernia_6064-78-103.json",
            "waypoint_id": "town-Nova_Roma-nation-Avernia_6064-78-103"
          }
        },
        {
          "from": "2026-04-01",
          "to": null,
          "status": "ruins",
          "nation_id": null,
          "label": "Nova Roma",
          "tags": ["bankrupt"]
        }
      ]
    }
  ]
}
```

## Data model rules

### Nations

- `id` must be unique
- `color` must be unique in practice for readability
- nation color is used by active city markers

### Cities

- `id` must be unique
- `x` and `y` must stay in map coordinates
- `kind` is reserved for future marker classes, currently `city`
- a city object persists even after destruction or loss of claim

### City states

Allowed `status` values:

- `active`
- `ruins`

Rules:

- state intervals use `from <= date < to`
- `to: null` means the state is active until replaced
- states for one city must not overlap
- `nation_id` may be `null`
- ruined or unclaimed cities must use `status: "ruins"` instead of being removed
- `label` may change over time
- `tags` are optional annotations such as `shop`, `bankrupt`, `unclaimed`

## Visual rules

### Active cities

- marker shape: circle
- marker color: nation color if `nation_id` exists
- free city fallback color: `#9e9e9e`

### Ruins

- marker shape: distinct ruined marker
- marker color: muted gray
- must remain available in popups and timeline playback
- must be hideable through a UI filter without removing data

### Labels

- labels are always rendered next to visible city markers
- label text comes from active `state.label`

## Timeline behavior

- the horizontal timeline selects a single active date
- all visible markers are recalculated for that date
- if a city has no active state for the selected date, it is not rendered
- `timeline.dates` provides slider stops
- if omitted, slider stops may be derived from all `from` and `to` boundaries

## Import from JourneyMap

Source directory:

```text
C:\Users\atfas\curseforge\minecraft\Instances\Armies of Terra\journeymap\data\mp\Armies~of~Terra\waypoints
```

Import rules:

- read all files matching `town*.json`
- parse coordinates from waypoint JSON
- parse city name, nation, and special markers from waypoint filename
- map prefixes like `ruin`, `bankrupt`, or unclaimed-city names to `status: "ruins"`
- keep source file metadata in each imported state

## Rendering notes

Coordinate conversion must always use:

```js
function gameToLatLng(x, y) {
  return L.latLng(-y, x);
}
```

Recommended client seams:

- `loadCities()`
- `validateCityDataset()`
- `resolveCityState()`
- `renderCities()`
- `setActiveDateByIndex()`

## Next implementation tasks

1. Allow manual editing of additional historical states.
2. Add a small legend for nation colors and ruin markers.
3. Extend the same time model to outposts and POI if needed.
