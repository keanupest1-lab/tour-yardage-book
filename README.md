# Tour Yardage Books

Generates tour-style yardage book pages (green page + hole page per hole, 4" × 7") for any golf course mapped on OpenStreetMap. Everything runs in the browser: no server, no API keys.

## How it works
1. **Search** (`src/osm.js`): Nominatim finds the course; Overpass returns every golf feature in its bounding box (holes, greens, tees, fairways, bunkers, water, trees, woods, OB).
2. **Build** (`src/course.js`): projects to local metres, assigns features to each `golf=hole` line, and computes front/centre/back, carry rings, fairway widths, bunker entry/carry/flanks, water near/far, straight-line run-outs, throat width and green depths.
3. **Render** (`src/render.js`): draws both pages as SVG. The green page is capped at 1:480 (Rules of Golf limit for green-reading material). Metres or yards.
4. **Print**: "Print book" outputs one 101.6 × 177.8 mm page per side.

## Run locally
```
python3 -m http.server 8000
# open http://localhost:8000
# offline sample: http://localhost:8000/?fixture=fixtures/irene10.json
```

## Deploy
Static site. Deploy the folder as-is to Vercel (framework: Other, no build step), Netlify or GitHub Pages.

## Limits (v1)
- Quality depends on OpenStreetMap mapping. A hole needs a `golf=hole` line and a green; fairways improve most numbers.
- Heights (the small +/− numbers): `scripts/ground_model.py` fits one bare-ground surface per course to laser ground returns (ICESat-2, GEDI) and satellite surface models (Copernicus GLO-30, ALOS AW3D30, NASADEM), with tree cover modelled out and roofs left out; which sources it uses and how smooth the ground is are chosen by predicting laser passes it didn't see. Data jobs: `scripts/fetch_dem.py`, `scripts/fetch_heights.py`. Green contours and slopes still need LiDAR.
- PDF: each page is a 4K picture (2194 × 3840 px) of the rough, corridor and tree canopies, with every line and number on top as vector.
- Respect OSM usage policies: cache course data rather than refetching for every view once traffic grows.

Course data © OpenStreetMap contributors (ODbL).

## Course Data connector (MCP)
`api/mcp.js` is a remote MCP server (Vercel function) that lets Claude build books by itself.

Tools: `find_course`, `get_course_layout`, `get_satellite_image`, `get_elevation`, `render_hole_pages`.

Vercel environment variables:
- `MAPBOX_TOKEN`: Mapbox public token (satellite images and terrain outside the USA)
- `ACCESS_KEY`: any long secret; requests without it are refused

Add to Claude as a custom connector with the URL:
`https://<your-project>.vercel.app/api/mcp?key=<ACCESS_KEY>`
