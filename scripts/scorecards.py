"""Finds and reads published scorecards (GolfPass) for courses, so mapping can be checked and routed against them.

Input  courses/scorecard-requests.txt:  slug | course name to search [| golfpass url]
Output courses/scorecards/<slug>.json  {source, url, tees: {name: [18 lengths]}, unit, par: [18], si: [18]}
       courses/scorecards/raw/<slug>.html (the page, for checking the parser)
"""
import html, json, os, re, sys, time, urllib.parse, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-GB,en;q=0.9"}


def get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def find_golfpass(name, log=print):
    q = f"golfpass {name} scorecard"
    for engine in ("https://html.duckduckgo.com/html/?q=", "https://www.bing.com/search?q="):
        try:
            page = get(engine + urllib.parse.quote(q))
        except Exception as e:
            log("  search failed", engine.split("/")[2], e); continue
        page = urllib.parse.unquote(html.unescape(page))
        m = re.findall(r"https?://(?:www\.)?golfpass\.com/(?:travel-advisor/)?courses/(\d+)-([a-z0-9-]+)", page)
        if m:
            cid, sl = m[0]
            return f"https://www.golfpass.com/courses/{cid}-{sl}/scorecard-and-layout"
        time.sleep(2)
    return None


def cells(row_html):
    out = []
    for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.S | re.I):
        t = html.unescape(re.sub(r"<[^>]+>", " ", c)); t = re.sub(r"\s+", " ", t).strip()
        out.append(t)
    return out


def nums(vals):
    return [int(v) for v in vals if re.fullmatch(r"\d{1,4}", v)]


def to18(ns):
    """a scorecard row: 9 holes, OUT, 9 holes, IN, TOTAL (or plain 18) -> the 18 hole values"""
    if len(ns) >= 21: return ns[:9] + ns[10:19]
    if len(ns) == 20: return ns[:9] + ns[10:19]
    if len(ns) in (18, 19): return ns[:18]
    if len(ns) in (9, 10, 11): return ns[:9]
    return None


def parse(page):
    rows = [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I)]
    tees, par, si = {}, None, None
    for r in rows:
        if len(r) < 10: continue
        label = r[0]; vals = to18(nums(r[1:]))
        if not vals: continue
        lab = label.lower()
        if lab.startswith("par") and par is None: par = vals
        elif lab.startswith(("handicap", "hcp", "si", "stroke")) and si is None: si = vals
        elif label and not lab.startswith(("hole", "par", "handicap", "hcp")) and all(60 <= v <= 700 for v in vals):
            tees.setdefault(label, vals)
    unit = "yd"
    if re.search(r"\b(metres|meters)\b", page, re.I) and not re.search(r"\byards\b", page, re.I): unit = "m"
    return {"tees": tees, "par": par, "si": si, "unit": unit}


def main():
    req = "courses/scorecard-requests.txt"
    os.makedirs("courses/scorecards/raw", exist_ok=True)
    for line in open(req):
        line = line.strip()
        if not line or line.startswith("#"): continue
        p = [x.strip() for x in line.split("|")]
        slug, name = p[0], p[1]
        url = p[2] if len(p) > 2 and p[2] else None
        files = [u for u in p[3:] if u]
        print("==", slug, flush=True)
        # the club's own scorecard / course map pictures or PDFs, saved as they are (read by eye)
        for k, f in enumerate(files):
            try:
                ext = os.path.splitext(urllib.parse.urlparse(f).path)[1].lower() or ".bin"
                with urllib.request.urlopen(urllib.request.Request(f, headers=UA), timeout=60) as r: data = r.read()
                dest = f"courses/scorecards/raw/{slug}-{k + 1}{ext}"
                open(dest, "wb").write(data); print(f"  saved {dest} ({len(data)} bytes) from {f}")
            except Exception as e:
                print("  file FAILED", f, repr(e))
        try:
            url = url or find_golfpass(name)
            if not url: print("  no GolfPass page found"); continue
            page = get(url)
            open(f"courses/scorecards/raw/{slug}.html", "w").write(page)
            sc = parse(page)
            sc.update({"source": "GolfPass", "url": url, "name": name, "fetched": time.strftime("%Y-%m-%d")})
            json.dump(sc, open(f"courses/scorecards/{slug}.json", "w"), indent=1)
            print(f"  {url}\n  tees {list(sc['tees'])} par {sc['par']}")
        except Exception as e:
            print("  FAILED", repr(e))
        time.sleep(3)


if __name__ == "__main__":
    main()
