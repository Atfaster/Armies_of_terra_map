/* Surface records are loaded per 512-block region; colony geometry is read-only. */
(() => {
  'use strict';
  const navigation = new URLSearchParams(location.search);
  const navX = navigation.get('x'), navZ = navigation.get('z');
  if (navX !== null && navZ !== null && /^-?\d+$/.test(navX) && /^-?\d+$/.test(navZ) && Math.abs(Number(navX)) <= 12512 && Math.abs(Number(navZ)) <= 12512) {
    map.whenReady(() => queueMicrotask(() => map.setView(gameToLatLng(Number(navX) + 0.5, Number(navZ) + 0.5), 1)));
  }
  const panel = L.control({position: 'bottomright'});
  let box;
  panel.onAdd = () => {
    box = L.DomUtil.create('div', 'coord-box');
    box.style.cssText = 'max-width:340px;white-space:pre-line;pointer-events:none;line-height:1.4';
    box.textContent = 'Наведите на карту: сведения о поверхности';
    return box;
  };
  panel.addTo(map);
  const readJSON = async path => {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  };
  const index = readJSON('surface/index.json');
  const labels = readJSON('surface/labels.json').catch(() => ({}));
  const cache = new Map();
  let cursor = null, ticket = 0, timer = null;
  function label(id, type, dictionary) {
    if (!id) return 'нет данных';
    const key = `${type}.${id.replace(':', '.').replaceAll('/', '.')}`;
    return dictionary[key] || id;
  }
  async function region(key, filename) {
    if (cache.has(key)) {
      const value = cache.get(key);cache.delete(key);cache.set(key, value);return value;
    }
    const pending = (async () => {
      if (!('DecompressionStream' in window)) throw new Error('Для подсказок обновите браузер');
      const response = await fetch(`surface/${filename}`);
      if (!response.ok) throw new Error('Не удалось загрузить сведения');
      const bytes = await response.arrayBuffer();
      const signature = new Uint8Array(bytes, 0, Math.min(2, bytes.byteLength));
      let data;
      if (signature[0] === 0x1f && signature[1] === 0x8b) {
        if (!('DecompressionStream' in window)) throw new Error('??? ????????? ???????? ???????');
        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
        data = await new Response(stream).json();
      } else {
        data = JSON.parse(new TextDecoder().decode(bytes));
      }
      const cells = new Uint32Array(512 * 512);
      for (const [position, entry] of data.cells) cells[position] = entry + 1;
      return {cells, palette: data.palette};
    })();
    cache.set(key, pending);
    while (cache.size > 6) cache.delete(cache.keys().next().value);
    try { return await pending; } catch (error) { cache.delete(key);throw error; }
  }
  async function update(point, serial) {
    const {x,z} = point;
    const prefix = `X: ${x}  Z: ${z}`;
    try {
      const [manifest, dictionary] = await Promise.all([index, labels]);
      if (serial !== ticket) return;
      const tx = Math.floor(x / 512), tz = Math.floor(z / 512), key = `${tx},${tz}`;
      const file = manifest.tiles[key];
      if (!file) { box.textContent = `${prefix}\nНет сведений о поверхности`;return; }
      box.textContent = `${prefix}\nЗагрузка сведений…`;
      const data = await region(key, file);
      if (serial !== ticket) return;
      const position = (z - tz * 512) * 512 + x - tx * 512;
      const entry = data.cells[position];
      if (!entry) {box.textContent = `${prefix}\nНет сведений о поверхности`;return;}
      const [id, properties, y, biome, surfaceY] = data.palette[entry - 1];
      const lines = [prefix, `Блок: ${label(id, 'block', dictionary)}`, `ID: ${id}`];
      if (y !== null && y !== undefined) lines.push(`Высота: ${y}`);
      if (biome) lines.push(`Биом: ${label(biome, 'biome', dictionary)}`);
      const state = Object.entries(properties).map(([k,v]) => `${k}=${v}`).join(', ');
      if (state) lines.push(`Свойства: ${state}`);
      box.textContent = lines.join('\n');
    } catch (error) {
      if (serial === ticket) box.textContent = `${prefix}\n${error.message || 'Сведения недоступны'}`;
    }
  }
  map.on('mousemove', event => {
    // Raster pixels cover [x,x+1), including negative world coordinates.
    const next = {x: Math.floor(event.latlng.lng), z: Math.floor(-event.latlng.lat)};
    if (cursor && cursor.x === next.x && cursor.z === next.z) return;
    cursor = next;const serial = ++ticket;
    clearTimeout(timer);timer = setTimeout(() => update(next, serial), 60);
  });
  map.getContainer().addEventListener('mouseleave', () => {
    ++ticket;cursor = null;clearTimeout(timer);box.textContent = 'Наведите на карту: сведения о поверхности';
  });
  const colonies = L.layerGroup().addTo(map);
  layerControl.addOverlay(colonies, 'Границы колоний');
  readJSON('colonies.json').then(items => {
    const renderer = L.canvas({padding: 0.3});
    for (const item of items) {
      const color = '#' + ((item.colour ?? 0x00ff00) & 0xffffff).toString(16).padStart(6, '0');
      const text = document.createElement('div');
      text.textContent = `${item.colony_name || 'Колония'} · ID ${item.id} · ${item.chunks} чанков`;
      const edges = item.edges.map(edge => edge.map(([x,z]) => gameToLatLng(x,z)));
      if (!edges.length) continue;
      L.polyline(edges, {color, weight:3, renderer}).bindTooltip(text).bindPopup(text.cloneNode(true)).addTo(colonies);
      const name = document.createElement('span');
      name.textContent = item.colony_name || `Колония ${item.id}`;
      name.style.cssText = 'display:inline-block;background:#101820dd;color:white;border-radius:4px;padding:2px 5px;white-space:nowrap';
      L.marker(edges[0][0], {interactive:false, icon:L.divIcon({html:name, className:'colony-name', iconSize:null})}).addTo(colonies);
    }
  }).catch(() => {
    const warning = L.control({position:'topright'});
    warning.onAdd = () => {const el=L.DomUtil.create('div','coord-box');el.textContent='Границы колоний недоступны';return el;};
    warning.addTo(map);
  });
})();
