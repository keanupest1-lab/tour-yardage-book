// Elevation sampling.
// USA: USGS 3DEP (1 m lidar where flown, no key).  Elsewhere: Mapbox Terrain-RGB tiles (needs MAPBOX_TOKEN).
import { PNG } from 'pngjs';

const inUS = (lon, lat) => lon > -125 && lon < -66 && lat > 24 && lat < 50;
const tileCache = new Map();

async function usgs(points) {
  const out = [];
  for (let i = 0; i < points.length; i += 200) {
    const batch = points.slice(i, i + 200);
    const geometry = JSON.stringify({ points: batch, spatialReference: { wkid: 4326 } });
    const url = 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples'
      + `?geometry=${encodeURIComponent(geometry)}&geometryType=esriGeometryMultipoint&returnFirstValueOnly=true&interpolation=RSP_BilinearInterpolation&f=json`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`USGS ${res.status}`);
    const j = await res.json();
    if (!j.samples) throw new Error('USGS returned no samples');
    const byId = new Map(j.samples.map((s) => [s.locationId, parseFloat(s.value)]));
    batch.forEach((p, k) => out.push([p[0], p[1], round1(byId.get(k))]));
  }
  return { source: 'USGS 3DEP (lidar-based where available, ~1 m)', points: out };
}

function lonLatToTile(lon, lat, z) {
  const n = 2 ** z;
  const x = ((lon + 180) / 360) * n;
  const r = (lat * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n;
  return [x, y];
}

async function terrainTile(z, x, y, token) {
  const key = `${z}/${x}/${y}`;
  if (tileCache.has(key)) return tileCache.get(key);
  const url = `https://api.mapbox.com/v4/mapbox.terrain-rgb/${z}/${x}/${y}.pngraw?access_token=${token}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Mapbox terrain ${res.status}`);
  const png = PNG.sync.read(Buffer.from(await res.arrayBuffer()));
  if (tileCache.size > 200) tileCache.clear();
  tileCache.set(key, png);
  return png;
}

async function mapbox(points, token) {
  if (!token) throw new Error('MAPBOX_TOKEN is not set on the server.');
  const z = 15;
  const out = [];
  for (const [lon, lat] of points) {
    const [fx, fy] = lonLatToTile(lon, lat, z);
    const tx = Math.floor(fx), ty = Math.floor(fy);
    const png = await terrainTile(z, tx, ty, token);
    const px = Math.min(png.width - 1, (fx - tx) * png.width), py = Math.min(png.height - 1, (fy - ty) * png.height);
    const h = (ix, iy) => {
      const i = (png.width * Math.min(png.height - 1, iy) + Math.min(png.width - 1, ix)) << 2;
      return -10000 + (png.data[i] * 65536 + png.data[i + 1] * 256 + png.data[i + 2]) * 0.1;
    };
    const x0 = Math.floor(px), y0 = Math.floor(py), dx = px - x0, dy = py - y0;
    const v = h(x0, y0) * (1 - dx) * (1 - dy) + h(x0 + 1, y0) * dx * (1 - dy) + h(x0, y0 + 1) * (1 - dx) * dy + h(x0 + 1, y0 + 1) * dx * dy;
    out.push([lon, lat, round1(v)]);
  }
  return { source: 'Mapbox Terrain-RGB (blended global DEM, roughly 5-30 m detail; fine for hole contours, not for green slopes)', points: out };
}

const round1 = (v) => (Number.isFinite(v) ? Math.round(v * 10) / 10 : null);

export async function sampleElevation(points, token) {
  const allUS = points.every(([lon, lat]) => inUS(lon, lat));
  if (allUS) {
    try { return await usgs(points); } catch (e) { /* fall through to Mapbox */ }
  }
  return mapbox(points, token);
}

export function gridPoints(bbox, spacingM) {
  const [s, n, w, e] = bbox;
  const lat0 = (s + n) / 2;
  const dLat = spacingM / 110540, dLon = spacingM / (111320 * Math.cos((lat0 * Math.PI) / 180));
  const pts = [];
  for (let lat = s; lat <= n; lat += dLat) for (let lon = w; lon <= e; lon += dLon) pts.push([+lon.toFixed(7), +lat.toFixed(7)]);
  return pts;
}
