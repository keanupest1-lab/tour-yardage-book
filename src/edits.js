// Course edits made in the editor, stored separately from the OpenStreetMap snapshot and merged on load.
// Edits format: { version: 1, slug, name, updated, holes: { "1": { par, line: [[lat, lon], ...] } },
//                 shapes: [{ id, kind: green|fairway|bunker|water|tee, coords: [[lat, lon], ...] }],
//                 trees: [[lat, lon, crown radius m], ...] }

export const slugify = (s) => String(s).toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

export function emptyEdits(slug, name) {
  return { version: 1, slug, name, updated: null, holes: {}, shapes: [] };
}

const SHAPE_TAGS = {
  green: { golf: 'green' }, fairway: { golf: 'fairway' }, bunker: { golf: 'bunker' },
  water: { golf: 'water_hazard' }, tee: { golf: 'tee' },
};

export function editsToElements(edits) {
  const els = [];
  let id = -1;
  for (const [ref, h] of Object.entries(edits.holes || {})) {
    if (!h.line || h.line.length < 2) continue;
    const tags = { golf: 'hole', ref: String(ref), source: 'editor' };
    if (h.par) tags.par = String(h.par);
    if (h.note) tags.note = String(h.note);
    if (h.dist) tags.dist = String(h.dist);
    if (h.name) tags.name = String(h.name);
    if (h.si) tags.handicap = String(h.si);
    els.push({ type: 'way', id: id--, tags, geometry: h.line.map(([lat, lon]) => ({ lat, lon })) });
  }
  for (const s of edits.shapes || []) {
    if (!s.coords || s.coords.length < 3 || !SHAPE_TAGS[s.kind]) continue;
    const ring = s.coords.map(([lat, lon]) => ({ lat, lon }));
    ring.push({ ...ring[0] });
    els.push({ type: 'way', id: id--, tags: { ...SHAPE_TAGS[s.kind], source: 'editor' }, geometry: ring });
  }
  // tree crowns found in the imagery: [lat, lon, crown radius m]
  for (const [lat, lon, r] of edits.trees || []) els.push({ type: 'node', id: id--, lat, lon, tags: { natural: 'tree', crown_radius: String(r), source: 'imagery' } });
  return els;
}

// OSM snapshot + edits -> one Overpass-style document. Edited holes replace OSM hole lines with the same number.
export function mergeEdits(base, edits) {
  if (!edits) return base;
  const editedRefs = new Set(Object.entries(edits.holes || {}).filter(([, h]) => h.line && h.line.length >= 2).map(([r]) => String(r)));
  const kept = (base?.elements || [])
    .filter((e) => !(e.tags && e.tags.golf === 'hole' && editedRefs.has(String(e.tags.ref))))
    .map((e) => {
      // par-only edits on OpenStreetMap holes (copy, never mutate the snapshot)
      const h = e.tags?.golf === 'hole' ? edits.holes?.[String(e.tags.ref)] : null;
      if (!h || (!h.par && !h.name)) return e;
      return { ...e, tags: { ...e.tags, ...(h.par ? { par: String(h.par) } : {}), ...(h.name ? { name: String(h.name) } : {}) } };
    });
  return { ...base, elements: [...kept, ...editsToElements(edits)] };
}

export function hasEdits(edits) {
  return !!edits && (Object.keys(edits.holes || {}).length > 0 || (edits.shapes || []).length > 0);
}
