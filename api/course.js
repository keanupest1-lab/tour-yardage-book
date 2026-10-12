// Server-side course data fetch with shared caching (Vercel CDN caches each course for a day).
import { fetchCourseData } from '../src/osm.js';

export const config = { maxDuration: 60 };

export default async function handler(req, res) {
  const raw = new URL(req.url, 'http://x').searchParams.get('bbox') || '';
  const bbox = raw.split(',').map(Number);
  res.setHeader('Content-Type', 'application/json');
  if (bbox.length !== 4 || bbox.some((v) => !Number.isFinite(v))) {
    res.statusCode = 400; return res.end(JSON.stringify({ error: 'bbox must be south,north,west,east' }));
  }
  try {
    const json = await fetchCourseData(bbox, () => {}, { timeoutMs: 13000 });
    res.setHeader('Cache-Control', 'public, s-maxage=86400, stale-while-revalidate=604800');
    res.end(JSON.stringify(json));
  } catch (e) {
    console.error('course fetch failed', raw, e.message);
    res.statusCode = 502; res.end(JSON.stringify({ error: e.message }));
  }
}
