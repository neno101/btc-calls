"""btc-calls: a BTC "higher or lower in 15 minutes" call, scored automatically.

Each run does three things, then saves:
  1. SCORE: any earlier call whose deadline has passed is graded against the closing price of the
     Coinbase BTC-USD one-minute candle that ends at its deadline.
  2. CALL: makes the next call with a fixed rule (rule v1, below) and logs it with a reason.
  3. REPORT: rewrites data/summary.md and data/status.json.

Rule v1 (never changes within a version): the line is the current price, so there is no cushion.
Lean with the last hour's drift. Confidence = 50% + 5% x (drift / a typical swing), capped at 55%.
It is a plain formula. No AI is involved, so it costs nothing and runs the same way every time.
Standard library only. Public Coinbase market data, no keys needed.
"""
import csv, json, math, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from statistics import pstdev
from zoneinfo import ZoneInfo

BASE = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"
HORIZON_MIN = int(os.getenv("HORIZON_MIN", "15"))
MIN_GAP_MIN = int(os.getenv("MIN_GAP_MIN", "10"))     # never make two calls closer together than this
RULE = "v1"
TARGET_N = 100                                       # calls to score before drawing any conclusion
CT = ZoneInfo("America/Chicago")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "predictions.csv")
DATA = os.path.join(HERE, "data")
FIELDS = ["id", "made_at_CT", "asset", "price_source", "threshold", "resolve_time_CT",
          "resolve_candle_start_epoch", "price_at_call", "call", "prob_higher", "rationale",
          "price_at_resolve", "outcome", "correct", "brier_score", "rule", "horizon_min"]

def get(path, **params):
    """GET with retries. Returns parsed JSON."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btc-calls/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(2 * (i + 1))
    raise RuntimeError(f"Coinbase request failed after retries: {url} ({last})")

def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def ct_str(ts): return datetime.fromtimestamp(ts, CT).strftime("%Y-%m-%d %H:%M")
def parse_ct(s): return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=CT).timestamp())

def load():
    if not os.path.exists(LOG): return []
    with open(LOG, newline="") as f: return list(csv.DictReader(f))

def save(rows):
    tmp = LOG + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", restval="")
        w.writeheader(); w.writerows(rows)
    os.replace(tmp, LOG)

def fetch_candles():
    """Most recent ~300 one-minute candles, newest first: [time, low, high, open, close, volume]."""
    return get(f"/products/{PRODUCT}/candles", granularity=60)

def score(rows, closes_by_start, now):
    done = 0
    for r in rows:
        if r.get("price_at_resolve") or not r.get("resolve_candle_start_epoch"): continue
        start = int(r["resolve_candle_start_epoch"])
        if start + 60 + 5 > now: continue                      # candle hasn't closed yet
        close = closes_by_start.get(start)
        if close is None:                                      # older than the window: ask for that candle directly
            for c in get(f"/products/{PRODUCT}/candles", granularity=60, start=iso(start), end=iso(start + 60)):
                if int(c[0]) == start: close = float(c[4])
        if close is None:
            if now - (start + 60) > 7200:                      # a missing candle after 2 hours: give up, don't score
                r["price_at_resolve"] = "NO DATA"; r["outcome"] = "NO DATA"; r["correct"] = "n/a"
                print(f"call #{r['id']}: no candle data, marked NO DATA")
            else:
                print(f"call #{r['id']}: candle {start} not available yet, will retry")
            continue
        k, ph = float(r["threshold"]), float(r["prob_higher"])
        out = "HIGHER" if close > k else "LOWER" if close < k else "TIE"
        r["price_at_resolve"] = f"{close:.2f}"; r["outcome"] = out
        if out == "TIE":
            r["correct"] = "n/a"; r["brier_score"] = ""
        else:
            r["correct"] = "YES" if out == r["call"] else "NO"
            r["brier_score"] = f"{(ph - (1 if out == 'HIGHER' else 0)) ** 2:.3f}"
        print(f"scored call #{r['id']}: {r['call']} vs {k} -> closed {close:.2f} ({out}) {r['correct']}")
        done += 1
    return done

def make_call(rows, candles, now):
    if rows:
        last = max(parse_ct(r["made_at_CT"]) for r in rows)
        if now - last < MIN_GAP_MIN * 60:
            print("last call was less than %d minutes ago; skipping" % MIN_GAP_MIN); return None
    done = sorted([c for c in candles if int(c[0]) < now // 60 * 60], key=lambda c: c[0])[-60:]
    if len(done) < 45:
        print("not enough candle history; skipping"); return None
    closes = [float(c[4]) for c in done]
    t = get(f"/products/{PRODUCT}/ticker")
    bid, ask = float(t.get("bid") or 0), float(t.get("ask") or 0)
    mid = (bid + ask) / 2 if bid and ask else float(t["price"])
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    s1 = pstdev(rets); window = len(closes)
    drift = mid / closes[0] - 1
    ratio = drift / (s1 * math.sqrt(window)) if s1 > 0 else 0.0
    p = round(0.5 + 0.05 * max(-1.0, min(1.0, ratio)), 3)
    deadline = math.ceil((now + HORIZON_MIN * 60) / 60) * 60
    row = {"id": str(max([int(r["id"]) for r in rows] + [0]) + 1), "made_at_CT": ct_str(now), "asset": PRODUCT,
           "price_source": "Coinbase Exchange public API", "threshold": f"{mid:.2f}", "resolve_time_CT": ct_str(deadline),
           "resolve_candle_start_epoch": str(deadline - 60), "price_at_call": f"{mid:.2f}",
           "call": "HIGHER" if ratio >= 0 else "LOWER", "prob_higher": f"{p:.3f}",
           "rationale": (f"Rule v1: lean with the last hour's drift ({drift*100:+.2f}%, ${mid-closes[0]:+,.0f}, "
                         f"{ratio:+.1f}x a typical swing). Line is the current price, so close to a coin flip."),
           "rule": RULE, "horizon_min": str(HORIZON_MIN)}
    rows.append(row)
    print(f"call #{row['id']}: {row['call']} ({p:.1%} higher) vs {mid:.2f}, deadline {row['resolve_time_CT']} CT")
    return row

def wilson(h, n, z=1.96):
    if n == 0: return 0.0, 1.0
    p = h / n; d = 1 + z * z / n; c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d, (c + a) / d

def report(rows, now):
    os.makedirs(DATA, exist_ok=True)
    mine = [r for r in rows if r.get("rule") == RULE and r.get("horizon_min") == str(HORIZON_MIN)]
    scored = [r for r in mine if r.get("correct") in ("YES", "NO")]
    n = len(scored); hits = sum(r["correct"] == "YES" for r in scored)
    lo, hi = wilson(hits, n)
    briers = [float(r["brier_score"]) for r in scored if r.get("brier_score")]
    avg_b = sum(briers) / len(briers) if briers else None
    ups = sum(r["outcome"] == "HIGHER" for r in scored)
    pending = sum(1 for r in rows if not r.get("price_at_resolve"))
    if n < TARGET_N: verdict = f"TOO EARLY ({n} of {TARGET_N} calls scored)"
    elif lo > 0.5: verdict = "BEATING A COIN FLIP (95% range is above 50%)"
    elif hi < 0.5: verdict = "WORSE THAN A COIN FLIP (95% range is below 50%)"
    else: verdict = "NOT DISTINGUISHABLE FROM A COIN FLIP (95% range includes 50%)"
    L = [f"# BTC calls: rule {RULE}, {HORIZON_MIN}-minute horizon", "", f"**Verdict:** {verdict}", ""]
    if n:
        L += [f"**Hit rate:** {hits/n:.1%} ({hits} of {n}). 95% range: {lo:.0%} to {hi:.0%}.",
              f"**Average Brier score:** {avg_b:.3f} (a coin flip is 0.250; lower is better).",
              f"**BTC rose in {ups/n:.0%} of these windows**, so always guessing HIGHER would have hit {ups/n:.0%}."]
    L += [f"**Pending calls:** {pending}", "", "Last 5 calls:", ""]
    for r in rows[-5:]:
        res = f"{r['outcome']} {r['correct']}" if r.get("outcome") else "pending"
        L.append(f"- #{r['id']} {r['made_at_CT']} CT: {r['call']} ({float(r['prob_higher']):.0%} higher) -> {res}")
    with open(os.path.join(DATA, "summary.md"), "w") as f: f.write("\n".join(L) + "\n")
    with open(os.path.join(DATA, "status.json"), "w") as f:
        json.dump({"project": "btc-calls", "rule": RULE, "horizon_min": HORIZON_MIN, "verdict": verdict,
                   "scored": n, "hits": hits, "hit_rate_pct": round(hits / n * 100, 1) if n else None,
                   "ci95_pct": [round(lo * 100), round(hi * 100)] if n else None,
                   "avg_brier": round(avg_b, 3) if avg_b is not None else None,
                   "always_higher_hit_rate_pct": round(ups / n * 100, 1) if n else None,
                   "pending": pending, "updated": iso(now)}, f, indent=2)

def main(now=None):
    now = int(now or time.time())
    rows = load()
    candles = fetch_candles()
    score(rows, {int(c[0]): float(c[4]) for c in candles}, now)
    make_call(rows, candles, now)
    save(rows); report(rows, now)

if __name__ == "__main__":
    try: main()
    except Exception as e:
        print("btc-calls failed:", e, file=sys.stderr); sys.exit(1)
