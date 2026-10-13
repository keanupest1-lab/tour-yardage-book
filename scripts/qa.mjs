// What the book will show for every library hole: does it start on a tee box, end on a real (not estimated) green,
// and is its green shared with another hole? Usage: node scripts/qa.mjs [slug ...]
import fs from 'node:fs';
import { parseOverpass } from '../src/osm.js';
import { buildCourse } from '../src/course.js';
import { mergeEdits } from '../src/edits.js';
const idx = JSON.parse(fs.readFileSync('courses/index.json', 'utf8'));
const want = process.argv.slice(2);
const report = {};
for (const c of idx.courses) {
  if (want.length && !want.includes(c.slug)) continue;
  const j = JSON.parse(fs.readFileSync(`courses/${c.slug}.json`, 'utf8'));
  const ep = `courses/edits/${c.slug}.json`;
  const ed = fs.existsSync(ep) ? JSON.parse(fs.readFileSync(ep, 'utf8')) : null;
  const course = buildCourse(parseOverpass(ed ? mergeEdits(j, ed) : j));
  let groups = course.groups.length ? course.groups : [{ name: c.name, holes: course.holes.map((_, i) => i) }];
  if (ed?.groups) groups = groups.filter((g) => ed.groups[g.name] !== null).map((g) => ({ ...g, name: ed.groups[g.name] || g.name }));
  report[c.slug] = [];
  for (const g of groups) {
    const hs = g.holes.map((i) => course.holes[i]);
    let ok = 0; const bad = [];
    for (const h of hs) {
      const shared = hs.filter((o) => o !== h && o.green === h.green && !h.green.estimated).map((o) => o.ref);
      const p = [];
      if (!h.teeFound) p.push('no tee box');
      if (h.green.estimated) p.push('green estimated');
      if (shared.length) p.push(`green shared with ${shared.join(',')}`);
      if (p.length) bad.push(`${h.ref}: ${p.join(', ')}`); else ok++;
      report[c.slug].push({ group: g.name, ref: h.ref, teeFound: h.teeFound, greenEstimated: !!h.green.estimated, shared, length: Math.round(h.lengthM) });
    }
    console.log(`== ${c.slug} / ${g.name}: ${ok}/${hs.length} holes start on a tee box and end on a mapped green`);
    if (bad.length) console.log('   ' + bad.join(' | '));
  }
}
fs.writeFileSync('courses/qa.json', JSON.stringify(report, null, 1));
