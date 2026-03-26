# Feature Spec: Cities, Outposts, POI and Roads

## Overview

This project will add vector layers on top of the existing raster Leaflet map:

- cities with hierarchy and nation affiliation
- outposts bound to cities
- points of interest (POI)
- roads with multiple surface types

All vector data uses the Minecraft coordinate system:

```text
X -> right
Y -> down
(0,0) -> top-left
```

## Data model

### Nations

```json
{
  "id": "nation_red",
  "name": "Red Empire",
  "color": "#e53935"
}
```

Rules:

- each nation has a unique `color`
- nation color is reused by cities and outposts

### Cities

```json
{
  "id": "city_1",
  "name": "Aldburg",
  "type": "capital",
  "x": 1234,
  "y": 5678,
  "nation_id": "nation_red"
}
```

Allowed city types:

- `capital`
- `city`
- `free`

Rules:

- `nation_id` may be `null` for a free city
- free cities must render in gray

### Outposts

```json
{
  "id": "outpost_1",
  "name": "North Watch",
  "x": 1300,
  "y": 5800,
  "city_id": "city_1"
}
```

Rules:

- each outpost must belong to an existing city
- deleting a city must also delete its outposts
- outpost color is derived from the parent city

### POI

```json
{
  "id": "poi_1",
  "name": "Ancient Ruins",
  "x": 9000,
  "y": 12000,
  "type": "ruins"
}
```

Rules:

- POI is independent from cities and nations
- custom icon system can be added later

### Roads

```json
{
  "id": "road_1",
  "type": "cobble",
  "points": [
    [100, 200],
    [300, 400],
    [800, 900]
  ]
}
```

Allowed road types:

- `trail`
- `gravel`
- `cobble`
- `concrete`

## Visual rules

### City markers

- `capital`: star, large, nation color
- `city`: circle, medium, nation color
- `free`: circle, gray `#9e9e9e`

### Outposts

- small circle or square
- color inherited from parent city
- smaller than city markers

### POI

- custom icons in a future step
- no nation color dependency

### Roads

| Type | Color | Width |
| --- | --- | --- |
| `trail` | `#8d6e63` | `2` |
| `gravel` | `#9e9e9e` | `3` |
| `cobble` | `#616161` | `4` |
| `concrete` | `#212121` | `5` |

## Rendering notes

Coordinate conversion must always use:

```js
function gameToLatLng(x, y) {
  return L.latLng(-y, x);
}
```

Recommended Leaflet layer structure:

```js
const layers = {
  cities: L.layerGroup(),
  outposts: L.layerGroup(),
  poi: L.layerGroup(),
  roads: L.layerGroup()
};
```

Recommended layer control:

```js
L.control.layers(null, {
  "Cities": layers.cities,
  "Outposts": layers.outposts,
  "POI": layers.poi,
  "Roads": layers.roads
});
```

## Data consistency constraints

- no outpost without city
- city may exist without nation
- referenced nation must exist and contain a color
- all data must stay compatible with static hosting

## Storage

Initial storage format:

```text
data/
  nations.json
  cities.json
  outposts.json
  poi.json
  roads.json
```

## Next implementation tasks

1. render cities and outposts
2. implement nation to city to outpost color propagation
3. add layer toggling
4. render roads
5. prepare hooks for editing
