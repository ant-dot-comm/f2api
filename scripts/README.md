# F2 results updater

Pulls F2 race results straight from the FIA timing PDFs (which are real text, not
scans) and writes them into `races/2026/resullts.json` — no screenshots.

## Setup (once)

```bash
pip install -r scripts/requirements.txt
```

## Each race weekend

The just-finished event is the latest on the FIA season page, so auto-discovery
works:

```bash
python scripts/update_f2.py --event Budapest          # write locally
python scripts/update_f2.py --event Budapest --push    # ...and git commit + push
```

`--event` takes the FIA event name (see the `EVENTS` map in `update_f2.py`); add a
row there for each new round.

## Backfilling an older round

The FIA page only serves the latest event without JavaScript, so for past events
grab the four PDF URLs from that event's page on fia.com and pass them explicitly:

```bash
python scripts/update_f2.py --event Monaco \
  --race1-cls  URL   --race1-grid URL \
  --race2-cls  URL   --race2-grid URL
```

## Notes / gotchas

- Wants the **final classification** PDFs (Race 1 sprint, Race 2 feature) and the
  **final grid** PDFs. Names vary a little per event (`final_grid` vs
  `final_starting_grid`); if a `final` grid isn't published, a `provisional` grid
  is fine.
- Some grids (e.g. Monaco's feature grid) are published only as a **graphic** with
  no extractable text. Those must be filled in by hand for that one race.
- Handles Finished / DNF / DNS rows and over-the-hour race times.
