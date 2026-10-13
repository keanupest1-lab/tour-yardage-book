"""Re-score routing against the saved evaluation cases (shape maps + answer keys from the eval branch),
without satellite downloads or the AI model. For tuning the solver.

  python3 ml/eval_local.py <dir with *_mask.png + *_case.json> [course-substring ...]
Env: ROUTE_KW='{"guess_greens": false}' passes options to route_from_mask.
"""
import glob, json, math, os, sys, time
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A


def run(d, only=(), kw=None, log=print):
    kw = kw or {}
    rows = []
    for cpath in sorted(glob.glob(os.path.join(d, "*_case.json"))):
        c = json.load(open(cpath))
        if only and not any(o.lower() in c["name"].lower() for o in only): continue
        lab = np.array(Image.open(cpath.replace("_case.json", "_mask.png")))
        m = A.Mosaic(Image.new("RGB", (lab.shape[1], lab.shape[0])), c["z"], c["tx0"], c["ty0"])
        mpp = m.mpp
        truth = c["truth"]; card = [tuple(x) for x in c["card"]]
        club = m.ll2px(*c["club"]) if c["club"] else m.ll2px(*truth[0][0])
        t = time.time()
        res = A.route_from_mask(lab, mpp, card, start_px=club, end_px=club, turn_px={8: club}, log=lambda *a: None, **kw)
        ok = 0; tee = []
        if res:
            for h, tr in zip(res["holes"], truth):
                if math.dist(h["green_px"], m.ll2px(*tr[-1])) * mpp < 35: ok += 1
                tee.append(math.dist(h["points"][0], m.ll2px(*tr[0])) * mpp)
        rows.append((c["name"], ok, round(float(np.median(tee)), 1) if tee else None, res and res["runner_up_gap"], round(time.time() - t)))
        log(f"{c['name'][:34]:34s} {ok:2d}/18  tee {rows[-1][2]}  gap {rows[-1][3]}  {rows[-1][4]}s")
    if rows:
        log(f"TOTAL {sum(r[1] for r in rows)}/{18 * len(rows)} = {sum(r[1] for r in rows) / (18 * len(rows)):.0%}")
    return rows


if __name__ == "__main__":
    import route_solver2 as RS
    for k, v in json.loads(os.environ.get("RS_PARAMS", "{}")).items(): setattr(RS, k, v)
    run(sys.argv[1], sys.argv[2:], json.loads(os.environ.get("ROUTE_KW", "{}")))
