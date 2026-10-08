# btc-calls

Every 15 minutes, 24/7: score the last BTC call, make the next one, save the log. Free.
It is a diary of a simple rule's accuracy. It places no trades and gives no advice.

## What it does
- Calls "BTC higher or lower than the current price in 15 minutes", with a reason and a probability.
- The rule never changes (rule v1): lean with the last hour's drift. Confidence = 50% + 5% x
  (drift / a typical swing), capped at 55%. It is a formula, so no AI and no cost.
- Scores each call on the close of Coinbase's BTC-USD one-minute candle at its deadline.
- Writes `predictions.csv` (every call), `data/summary.md` (the plain-English scoreboard), and
  `data/status.json` (so your nightly 11 PM email picks it up).

## How to read the scoreboard
- **Hit rate and its 95% range.** Until the range excludes 50%, you can't tell it from a coin flip.
- **Brier score.** A coin flip scores 0.250. Lower is better.
- **"Always guessing HIGHER" hit rate.** If BTC just drifts up, a lazy guess looks smart. The rule has to beat this too.
- The verdict stays "TOO EARLY" until 100 calls are scored (about a day).

## Setup (the repo must be PUBLIC; it holds no keys)
1. Create the repo `neno101/btc-calls` as public and push this folder. Public repos get unlimited
   free GitHub Actions minutes. Private repos get 2,000 a month, and this uses about 2,900.
2. On GitHub: Actions tab, enable workflows, open "btc-calls", click "Run workflow" once.
3. Check that `predictions.csv` gained a new row. After that it runs itself.

## Good to know
- GitHub's scheduler can start a run several minutes late and sometimes skips one. Each call
  carries its own full 15 minutes, so scoring stays valid. Spacing is just uneven.
- If a run fails, GitHub emails you.
- GitHub pauses scheduled jobs in public repos with no activity for 60 days. This one saves a row
  every run, so it should stay active, but the nightly email will show if it ever stops.
- Prices come from Coinbase's public Exchange API. It's the same market as the Coinbase connector,
  a different data feed, so tiny differences are possible.
- This could not be tested against the live Coinbase API from where it was built. The logic was
  tested on mock data. The first manual run is the real test.

## Prefer your own server?
If the qqq-alerts server is running, add one line with `crontab -e` instead of using GitHub:
`*/15 * * * * cd ~/btc-calls && python3 btc_calls.py >> run.log 2>&1`
