# unictoos Charts — catalog generator

Generates **unictoos Charts** (`ai.unictoos.catalog`), a metadata-only
Stremio-format addon — catalogs + meta, **no streams, no sources** — served
as static JSON from this repo's GitHub Pages:

**https://unictoai.github.io/unictoos-apk/catalog/manifest.json**

It gives unictoos (and any Stremio client) a homepage of charts. Playback
always stays with the user's own installed addons.

## Run it

```bash
python3 catalog-gen/generate.py     # stdlib only, ~1 min
```

Output is written to `docs/catalog/` (manifest + `catalog/{type}/{id}.json`
+ a pre-rendered `meta/{type}/{imdbId}.json` for every title, since Pages
is static-only). Commit + push only when the output actually changed.

## Data sources

| Source | Rows | Needs |
| --- | --- | --- |
| Netflix official Top 10 files (top10.netflix.com) | Global + Pakistan Top 10, movies & series, weekly | nothing |
| MovieOfTheNight `/shows/top` (country=pk) | Official Top 10 for Netflix / Prime Video / Apple TV / Max in Pakistan, with native IMDb IDs, posters, overviews | API key |
| Trakt trending / anticipated | Global trending movies & series, most-anticipated movies | client ID |

Each source is isolated: if one fails, the run keeps that source's
previously committed output and logs a warning in
`catalog-gen/state/last-run.json` (per-source status, catalog counts,
IMDb-join rate, API calls used). MovieOfTheNight calls are hard-capped at
90 per run.

## Keys (never commit these)

Drop key files into `~/workspace/unictoos-catalog-keys/` (dir mode 0700):

- `motn.key` — MovieOfTheNight API key
- `trakt.id` — Trakt client ID

Either may be absent; the generator skips that source and says so in the
run log. Trakt catalogs are omitted from the manifest entirely until a
client ID exists (no empty catalogs, ever).

## Title → IMDb joins

Chart rows from Netflix arrive without IDs. They are joined locally
against the repo's existing title index (`config/catalog.json`) with a
votes-based tiebreak, and every successful join is cached permanently in
`catalog-gen/state/joins.json` (committed — it is what keeps repeat runs
cheap). Titles that cannot be joined are dropped from the catalogs (a
Stremio catalog entry needs a valid `tt…` id). Unjoined titles are counted
in the run log's join rate.

## Safety

Every title passes the same public blocklist the app consumes
(`config/blocklist.json`, normalized exact-title match) before it can
enter any catalog or meta file. A blocked title never ships.

## Refresh cadence

Intended: a scheduled job runs the generator (weekly after Netflix
publishes its new week on Tuesdays; daily once the MovieOfTheNight key is
in, for the per-service Pakistan charts). Scheduling is managed outside
this repo.
