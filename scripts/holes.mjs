// Prints each library course's holes as the app builds them (map data + editor/AI edits): ref, par, length.
import fs from 'node:fs';
import { parseOverpass } from '../src/osm.js';
import { buildCourse } from '../src/course.js';
import { mergeEdits } from '../src/edits.js';
const idx = JSON.parse(fs.readFileSync('courses/index.json', 'utf8'));
const out = {};
for (const c of idx.courses) {
  const j = JSON.parse(fs.readFileSync(`courses/${c.slug}.json`, 'utf8'));
  const ep = `courses/edits/${c.slug}.json`;
  const json = fs.existsSync(ep) ? mergeEdits(j, JSON.parse(fs.readFileSync(ep, 'utf8'))) : j;
  const course = buildCourse(parseOverpass(json));
  const groups = course.groups.length ? course.groups.map((g) => ({ name: g.name, holes: g.holes })) : [{ name: c.name, holes: course.holes.map((h, i) => i) }];
  out[c.slug] = groups.map((g) => ({ name: g.name, holes: g.holes.map((i) => { const h = course.holes[i]; return [h.ref, h.par, Math.round(h.tagDist || h.toCentre || h.lengthM), Math.round(h.toCentre || h.lengthM)]; }) }));
}
console.log(JSON.stringify(out));
