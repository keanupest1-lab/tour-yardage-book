// Each hole as the book draws it (after tee/green snapping), in lat/lon: for review images.
import fs from 'node:fs';
import { parseOverpass } from '../src/osm.js';
import { buildCourse } from '../src/course.js';
import { mergeEdits } from '../src/edits.js';
const slug = process.argv[2];
const j = JSON.parse(fs.readFileSync(`courses/${slug}.json`, 'utf8'));
const ep = `courses/edits/${slug}.json`;
const ed = fs.existsSync(ep) ? JSON.parse(fs.readFileSync(ep, 'utf8')) : null;
const c = buildCourse(parseOverpass(ed ? mergeEdits(j, ed) : j));
const inv = (p) => { const [lon, lat] = c.proj.inv(p); return [lat, lon]; };
console.log(JSON.stringify({
  holes: c.holes.map((h) => ({ ref: h.ref, par: h.par, line: h.CL.map(inv), start0: inv(h.START0), teeFound: h.teeFound, greenEst: !!h.green.estimated, green: h.green.geom.map(inv) })),
  tees: c.layers.tee.map((t) => t.geom.map(inv)),
}));
