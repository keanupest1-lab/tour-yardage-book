// MCP tools for the Course Data connector.
import { z } from 'zod';
import { searchCourses, fetchCourseData, parseOverpass } from '../src/osm.js';
import { buildCourse } from '../src/course.js';
import { greenPage, holePage } from '../src/render.js';
import { sampleElevation, gridPoints } from './elevation.js';

const BBOX = z.array(z.number()).length(4).describe('Course bounding box [south, north, west, east] in degrees, as returned by find_course');
const courseCache = new Map();

async function loadCourse(bbox) {
  const key = bbox.map((v) => v.toFixed(5)).join(',');
  const hit = courseCache.get(key);
  if (hit && Date.now() - hit.t < 30 * 60 * 1000) return hit.course;
  // COURSE_FIXTURE lets local tests run without network access
  const raw = process.env.COURSE_FIXTURE ? JSON.parse((await import('node:fs')).readFileSync(process.env.COURSE_FIXTURE, 'utf8')) : await fetchCourseData(bbox);
  const course = buildCourse(parseOverpass(raw));
  if (courseCache.size > 20) courseCache.clear();
  courseCache.set(key, { t: Date.now(), course });
  return course;
}

const r1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
const json = (obj) => ({ content: [{ type: 'text', text: JSON.stringify(obj) }] });
const fail = (msg) => ({ isError: true, content: [{ type: 'text', text: msg }] });

function holeSummary(h) {
  return {
    hole: h.ref, par: h.par, stroke_index: h.si || null, tagged_length: h.tagDist,
    back_tee_to_green_m: { front: r1(h.toFront), centre: r1(h.toCentre), back: r1(h.toBack) },
    green_m: { depth: r1(h.greenDepth), width: r1(h.greenWidth) },
    mapped: { fairway: h.fairways.length > 0, tees: h.tees.length, bunkers: h.bunkers.length, water: h.water.length, trees: h.trees.length },
    warnings: h.warnings,
  };
}

function holeGeo(course, h) {
  const inv = (p) => course.proj.inv(p).map((v) => +v.toFixed(6));
  const feat = (kind, geom, closed = true) => ({
    type: 'Feature', properties: { kind, hole: h.ref },
    geometry: closed ? { type: 'Polygon', coordinates: [[...geom, geom[0]].map(inv)] } : { type: 'LineString', coordinates: geom.map(inv) },
  });
  return [
    feat('line_of_play', h.CL, false), feat('green', h.green.geom),
    ...h.fairways.map((f) => feat('fairway', f.geom)), ...h.tees.map((t) => feat('tee', t.geom)),
    ...h.bunkers.map((b) => feat('bunker', b.geom)), ...h.water.map((w) => feat('water', w.geom)),
    ...h.trees.map((t) => ({ type: 'Feature', properties: { kind: 'tree', hole: h.ref, radius_m: t.r }, geometry: { type: 'Point', coordinates: inv(t.c) } })),
  ];
}

export function registerTools(server, env) {
  server.registerTool('find_course', {
    title: 'Find a golf course',
    description: 'Search OpenStreetMap for golf courses by name and town. Returns name, place, centre point and bounding box to pass to the other tools.',
    inputSchema: { query: z.string().min(2).describe('Course name, ideally with town or country, e.g. "Irene Country Club"') },
  }, async ({ query }) => {
    try { return json({ results: await searchCourses(/golf/i.test(query) ? query : `${query} golf`) }); }
    catch (e) { return fail(`Search failed: ${e.message}`); }
  });

  server.registerTool('get_course_layout', {
    title: 'Get course layout',
    description: 'Fetch every mapped hole on a course from OpenStreetMap with computed tour-book numbers (front/centre/back from the back tee, green depth and width). Set include_geometry for GeoJSON shapes of greens, fairways, tees, bunkers, water, trees and the line of play.',
    inputSchema: {
      bbox: BBOX,
      holes: z.array(z.string()).optional().describe('Only these hole numbers, e.g. ["1","10"]'),
      include_geometry: z.boolean().optional().describe('Include GeoJSON shapes (larger response)'),
    },
  }, async ({ bbox, holes, include_geometry }) => {
    try {
      const course = await loadCourse(bbox);
      const list = course.holes.filter((h) => !holes || holes.includes(String(h.ref)));
      const out = { holes_mapped: course.holes.length, holes: list.map(holeSummary), attribution: '© OpenStreetMap contributors (ODbL)' };
      if (!course.holes.length) out.note = 'No golf=hole lines are mapped for this course. Use get_satellite_image and trace it instead.';
      if (include_geometry) out.geojson = { type: 'FeatureCollection', features: list.flatMap((h) => holeGeo(course, h)) };
      return json(out);
    } catch (e) { return fail(`Layout failed: ${e.message}`); }
  });

  server.registerTool('get_satellite_image', {
    title: 'Get satellite image',
    description: 'Licensed Mapbox satellite image of an area (e.g. one hole). The image is north-up and exactly covers the bbox, so pixel positions map linearly to longitude/latitude.',
    inputSchema: {
      bbox: BBOX.describe('Area to show [south, north, west, east]'),
      size: z.number().int().min(256).max(1280).optional().describe('Longest side in pixels (default 1024)'),
    },
  }, async ({ bbox, size = 1024 }) => {
    if (!env.MAPBOX_TOKEN) return fail('MAPBOX_TOKEN is not set on the server.');
    const [s, n, w, e] = bbox;
    const lat0 = (s + n) / 2;
    const wm = (e - w) * 111320 * Math.cos((lat0 * Math.PI) / 180), hm = (n - s) * 110540;
    const W = wm >= hm ? size : Math.max(64, Math.round((size * wm) / hm));
    const H = hm > wm ? size : Math.max(64, Math.round((size * hm) / wm));
    const url = `https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/[${w},${s},${e},${n}]/${W}x${H}?attribution=false&logo=false&access_token=${env.MAPBOX_TOKEN}`;
    try {
      const res = await fetch(url);
      if (!res.ok) return fail(`Mapbox returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
      const buf = Buffer.from(await res.arrayBuffer());
      return { content: [
        { type: 'image', data: buf.toString('base64'), mimeType: res.headers.get('content-type') || 'image/png' },
        { type: 'text', text: JSON.stringify({ bbox, width_px: W, height_px: H, metres_per_px: r1((wm / W) * 100) / 100, attribution: '© Mapbox © Maxar' }) },
      ] };
    } catch (err) { return fail(`Image failed: ${err.message}`); }
  });

  server.registerTool('get_elevation', {
    title: 'Get elevation',
    description: 'Ground height in metres for points or a grid. USA uses USGS 3DEP lidar (~1 m); elsewhere Mapbox terrain (coarser, fine for hole contours and plus/minus, not for green slopes).',
    inputSchema: {
      points: z.array(z.array(z.number()).length(2)).max(500).optional().describe('[[lon, lat], ...]'),
      bbox: BBOX.optional().describe('Grid area [south, north, west, east] (use with spacing_m)'),
      spacing_m: z.number().min(2).max(100).optional().describe('Grid spacing in metres (default 10)'),
    },
  }, async ({ points, bbox, spacing_m = 10 }) => {
    try {
      let pts = points;
      if (!pts && bbox) pts = gridPoints(bbox, spacing_m);
      if (!pts || !pts.length) return fail('Give points or a bbox.');
      if (pts.length > 2500) return fail(`That grid is ${pts.length} points; increase spacing_m or shrink the bbox (max 2500).`);
      return json(await sampleElevation(pts, env.MAPBOX_TOKEN));
    } catch (e) { return fail(`Elevation failed: ${e.message}`); }
  });

  server.registerTool('render_hole_pages', {
    title: 'Render yardage book pages',
    description: 'Render one hole as two tour-style pocket pages (green page + hole page, 4x7 in) as SVG, with the computed numbers. Convert the SVGs to PDF/PNG for the user.',
    inputSchema: {
      bbox: BBOX,
      hole: z.string().describe('Hole number, e.g. "10"'),
      units: z.enum(['m', 'yd']).optional().describe('Metres (default) or yards'),
    },
  }, async ({ bbox, hole, units = 'm' }) => {
    try {
      const course = await loadCourse(bbox);
      const h = course.holes.find((x) => String(x.ref) === String(hole));
      if (!h) return fail(`Hole ${hole} is not mapped. Mapped holes: ${course.holes.map((x) => x.ref).join(', ') || 'none'}.`);
      return { content: [
        { type: 'text', text: JSON.stringify(holeSummary(h)) },
        { type: 'text', text: greenPage(h, course, { units }) },
        { type: 'text', text: holePage(h, course, { units }) },
      ] };
    } catch (e) { return fail(`Render failed: ${e.message}`); }
  });
}
