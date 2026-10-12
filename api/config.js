// Public browser config. Only a public Mapbox token (pk.*) is ever returned.
export default function handler(req, res) {
  const t = process.env.MAPBOX_TOKEN || '';
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'public, max-age=300');
  res.end(JSON.stringify({ mapboxToken: t.startsWith('pk.') ? t : null }));
}
