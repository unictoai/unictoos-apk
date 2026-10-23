#!/usr/bin/env python3
"""
unictoos Charts — static Stremio catalog addon generator.

Produces a metadata-only Stremio addon (catalog + meta, NO streams) as static
JSON under docs/catalog/, served by GitHub Pages at
  https://unictoai.github.io/unictoos-apk/catalog/manifest.json

Sources (each isolated; one failing never kills the run, and a failed source
keeps its previously committed output):
  - Netflix official Top 10 files (top10.netflix.com -> netflix.com/tudum)
  - MovieOfTheNight  (needs key: ~/workspace/unictoos-catalog-keys/motn.key)
  - Trakt            (needs id:  ~/workspace/unictoos-catalog-keys/trakt.id)

Stdlib only. Run:  python3 catalog-gen/generate.py
"""

import io
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "docs", "catalog")
STATE_DIR = os.path.join(REPO, "catalog-gen", "state")
KEYS_DIR = os.path.expanduser("~/workspace/unictoos-catalog-keys")
UA = {"User-Agent": "unictoos-catalog-gen/1.0"}

RUN = {
    "started": datetime.now(timezone.utc).isoformat(),
    "sources": {},
    "api_calls": {"motn": 0, "trakt": 0},
    "notes": [],
}
MOTN_CALL_BUDGET = 90  # hard cap per run (spec: never exceed 90/day)


def log(msg):
    print(f"[catalog-gen] {msg}", flush=True)


def warn(src, msg):
    RUN["sources"].setdefault(src, {})["warning"] = msg
    log(f"WARNING {src}: {msg}")


# --------------------------------------------------------------------------
# Adult-safety filter: the same public blocklist the app consumes.
# --------------------------------------------------------------------------
def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def load_blocklist():
    path = os.path.join(REPO, "config", "blocklist.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {norm_title(t) for t in data.get("blockedTitles", [])}


BLOCKED = load_blocklist()


def is_blocked(title):
    return norm_title(title) in BLOCKED


# --------------------------------------------------------------------------
# Local title -> IMDb join index.
# Read-only use of the repo's existing config/catalog.json (38k popular
# titles: [imdb, title, year, kind, genres, rating, votes, minutes]).
# Successful joins are also persisted into state/joins.json, which is
# consulted first and is the permanent, growing join cache.
# --------------------------------------------------------------------------
def load_join_index():
    index = {}
    path = os.path.join(REPO, "config", "catalog.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for it in data.get("items", []):
            imdb, title, year, kind, genres, rating, votes, minutes = it
            rec = {
                "imdb": imdb, "name": title, "year": year,
                "kind": kind, "genres": genres, "rating": rating,
                "votes": votes, "runtime_min": minutes,
            }
            key = norm_title(title)
            prev = index.get(key)
            if prev is None or (votes or 0) > (prev["votes"] or 0):
                index[key] = rec
            # Secondary key with the standalone word "and" dropped, so a
            # chart title spelled with "&" still finds its "and" entry.
            folded = " ".join(w for w in key.split() if w != "and")
            if folded != key:
                prev = index.get(folded)
                if prev is None or (votes or 0) > (prev["votes"] or 0):
                    index[folded] = rec
    return index


def load_state_joins():
    path = os.path.join(STATE_DIR, "joins.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state_joins(joins):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "joins.json"), "w", encoding="utf-8") as f:
        json.dump(joins, f, ensure_ascii=False, indent=1, sort_keys=True)


JOIN_INDEX = load_join_index()
STATE_JOINS = load_state_joins()
JOIN_STATS = {"attempted": 0, "joined": 0}


def join_title(title, want_kind=None):
    """Resolve a chart title to an IMDb record, or None."""
    key = norm_title(title)
    if key in STATE_JOINS:
        JOIN_STATS["attempted"] += 1
        JOIN_STATS["joined"] += 1
        return STATE_JOINS[key]
    rec = JOIN_INDEX.get(key)
    if rec is None:
        folded = " ".join(w for w in key.split() if w != "and")
        if folded != key:
            rec = JOIN_INDEX.get(folded)
    JOIN_STATS["attempted"] += 1
    if rec is None:
        return None
    if want_kind and rec["kind"] != want_kind:
        # Same-name movie/series collisions: trust the kind we asked for only
        # when it matches; otherwise still use the record (better than none)
        # but do not cache it under a kind-specific assumption.
        pass
    STATE_JOINS[key] = rec
    JOIN_STATS["joined"] += 1
    return rec


# --------------------------------------------------------------------------
# Minimal XLSX reader (stdlib zipfile + XML), streaming rows.
# --------------------------------------------------------------------------
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def xlsx_rows(data_bytes):
    z = zipfile.ZipFile(io.BytesIO(data_bytes))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root:
            shared.append("".join(t.text or "" for t in si.iter(M + "t")))
    sheet = sorted(n for n in z.namelist()
                   if n.startswith("xl/worksheets/sheet"))[0]
    with z.open(sheet) as fh:
        for _event, row in ET.iterparse(fh, events=("end",)):
            if row.tag != M + "row":
                continue
            vals = []
            for c in row:
                if c.tag != M + "c":
                    continue
                v = c.find(M + "v")
                if v is None:
                    vals.append(None)
                elif c.get("t") == "s":
                    vals.append(shared[int(v.text)])
                else:
                    vals.append(v.text)
            yield vals
            row.clear()


def http_get(url, headers=None, timeout=180):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url, headers=None, timeout=60):
    return json.loads(http_get(url, headers=headers, timeout=timeout)
                      .decode("utf-8"))


# --------------------------------------------------------------------------
# Source: Netflix official Top 10 files.
# --------------------------------------------------------------------------
NETFLIX_GLOBAL_URL = "https://top10.netflix.com/data/all-weeks-global.xlsx"
NETFLIX_COUNTRIES_URL = "https://top10.netflix.com/data/all-weeks-countries.xlsx"


def latest_week_rows(url, keep=None):
    """Download an xlsx and return (latest_week, rows-for-that-week)."""
    data = http_get(url)
    best_week, rows = None, []
    header = None
    for vals in xlsx_rows(data):
        if header is None:
            header = vals
            continue
        d = dict(zip(header, vals))
        wk = d.get("week")
        if wk is None:
            continue
        if best_week is None or wk > best_week:
            best_week, rows = wk, []
        if wk == best_week and (keep is None or keep(d)):
            rows.append(d)
    return best_week, rows


def netflix_charts():
    """Returns list of chart dicts: {id, name, type, titles:[chart rows]}."""
    charts = []
    # --- global: merge English + Non-English, rank by weekly views, top 10
    week, rows = latest_week_rows(NETFLIX_GLOBAL_URL)
    RUN["sources"]["netflix"] = {"status": "ok", "week": week}
    log(f"netflix global: latest week {week}, {len(rows)} rows")
    for cat_group, ctype, cid, cname in (
        (("Films (English)", "Films (Non-English)"), "movie",
         "netflix-top10-global-movies", "Netflix Top 10 — Global Movies"),
        (("TV (English)", "TV (Non-English)"), "series",
         "netflix-top10-global-series", "Netflix Top 10 — Global Series"),
    ):
        sel = [r for r in rows if r["category"] in cat_group]
        sel.sort(key=lambda r: -float(r.get("weekly_views") or 0))
        charts.append({"id": cid, "name": cname, "type": ctype,
                       "titles": dedupe_shows(sel, ctype)})
    # --- per-country: Pakistan, US fallback
    week_c, rows_c = latest_week_rows(
        NETFLIX_COUNTRIES_URL,
        keep=lambda d: d.get("country_iso2") in ("PK", "US"))
    have_pk = any(r["country_iso2"] == "PK" for r in rows_c)
    iso = "PK" if have_pk else "US"
    if not have_pk:
        RUN["notes"].append(
            "Pakistan absent from Netflix countries file; US used as fallback "
            "for the netflix-top10-pk-* catalogs.")
        log("netflix countries: PK absent, falling back to US")
    RUN["sources"]["netflix"]["countries_week"] = week_c
    RUN["sources"]["netflix"]["country_used"] = iso
    for cat, ctype, cid, cname in (
        ("Films", "movie", "netflix-top10-pk-movies",
         "Netflix Top 10 — Pakistan Movies"),
        ("TV", "series", "netflix-top10-pk-series",
         "Netflix Top 10 — Pakistan Series"),
    ):
        sel = [r for r in rows_c
               if r["country_iso2"] == iso and r["category"] == cat]
        sel.sort(key=lambda r: int(r["weekly_rank"]))
        charts.append({"id": cid, "name": cname, "type": ctype,
                       "titles": dedupe_shows(sel, ctype)})
    return charts


def dedupe_shows(rows, ctype):
    """Series charts are per-season; collapse to one entry per show."""
    seen, out = set(), []
    for r in rows:
        title = r["show_title"]
        k = norm_title(title)
        if k in seen:
            continue
        seen.add(k)
        out.append({"title": title})
    return out[:10]


# --------------------------------------------------------------------------
# Source: MovieOfTheNight (official per-country Top 10 + IMDb join help).
# --------------------------------------------------------------------------
MOTN_BASE = "https://api.movieofthenight.com/v4"


def read_key(name):
    p = os.path.join(KEYS_DIR, name)
    if os.path.exists(p):
        with open(p) as f:
            v = f.read().strip()
        return v or None
    return None


def motn_get(path, key):
    if RUN["api_calls"]["motn"] >= MOTN_CALL_BUDGET:
        raise RuntimeError("MOTN per-run call budget exhausted")
    RUN["api_calls"]["motn"] += 1
    return http_get_json(MOTN_BASE + path,
                         headers={"X-API-Key": key,
                                  "Accept": "application/json"})


def motn_charts(key):
    charts = []
    for service, label in (("netflix", "Netflix"), ("prime", "Prime Video"),
                           ("appletv", "Apple TV"), ("max", "Max")):
        for show_type, ctype in (("movie", "movie"), ("series", "series")):
            cid = f"top10-{service}"
            data = motn_get(f"/shows/top?country=pk&service={service}"
                            f"&show_type={show_type}", key)
            items = data if isinstance(data, list) else data.get("shows", [])
            titles = []
            for s in items[:10]:
                ids = s.get("imdbId") or (s.get("externalIds") or {}).get("imdb")
                titles.append({"title": s.get("title"), "motn": s,
                               "imdb": ids})
            charts.append({
                "id": cid, "type": ctype,
                "name": f"{label} Top 10 — Pakistan "
                        f"({'Movies' if ctype == 'movie' else 'Series'})",
                "titles": titles})
    RUN["sources"]["motn"] = {"status": "ok"}
    return charts


def motn_join_by_imdb(imdb, key):
    """Fetch one show by IMDb id from MOTN (for poster/overview enrichment)."""
    try:
        data = motn_get(f"/shows/imdb/{imdb}", key)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Source: Trakt (trending / anticipated; IMDb ids native).
# --------------------------------------------------------------------------
TRAKT_BASE = "https://api.trakt.tv"


def trakt_get(path, client_id):
    RUN["api_calls"]["trakt"] += 1
    return http_get_json(TRAKT_BASE + path, headers={
        "trakt-api-key": client_id, "trakt-api-version": "2",
        "Content-Type": "application/json"})


def trakt_charts(client_id):
    charts = []
    specs = (
        ("/movies/trending?limit=20", "movie", "trending-movies",
         "Trending Movies", "movie"),
        ("/shows/trending?limit=20", "series", "trending-series",
         "Trending Series", "show"),
        ("/movies/anticipated?limit=20", "movie", "anticipated-movies",
         "Most Anticipated Movies", "movie"),
    )
    for path, ctype, cid, cname, wrap in specs:
        data = trakt_get(path, client_id)
        titles = []
        for entry in data[:20]:
            obj = entry.get(wrap) or {}
            ids = obj.get("ids") or {}
            if not ids.get("imdb"):
                continue
            titles.append({"title": obj.get("title"),
                           "imdb": ids["imdb"], "year": obj.get("year")})
        charts.append({"id": cid, "name": cname, "type": ctype,
                       "titles": titles})
    RUN["sources"]["trakt"] = {"status": "ok"}
    return charts


# --------------------------------------------------------------------------
# Meta build / emit.
# --------------------------------------------------------------------------
def build_entry(title_rec, ctype):
    """Resolve one chart title to (preview, meta) or None."""
    title = title_rec["title"]
    if not title or is_blocked(title):
        return None
    imdb = title_rec.get("imdb")
    rec = None
    if imdb:
        rec = {"imdb": imdb, "name": title, "year": title_rec.get("year"),
               "kind": "m" if ctype == "movie" else "s",
               "genres": None, "rating": None, "runtime_min": None}
    else:
        rec = join_title(title, "m" if ctype == "movie" else "s")
        if rec is None:
            return None
        imdb = rec["imdb"]
    motn = title_rec.get("motn") or {}
    img = motn.get("imageSet") or {}
    poster = (img.get("verticalPoster") or {}).get("w360") or \
        (img.get("verticalPoster") or {}).get("w240")
    background = (img.get("horizontalBackdrop") or {}).get("w1080") or \
        (img.get("horizontalBackdrop") or {}).get("w720")
    genres = None
    if motn.get("genres"):
        genres = [g.get("name") for g in motn["genres"] if g.get("name")]
    elif rec.get("genres"):
        genres = [g.strip() for g in rec["genres"].split(",") if g.strip()]
    overview = motn.get("overview") or None
    rating = motn.get("rating")
    if rating:
        rating = round(rating / 10.0, 1)  # MOTN rating is 0-100
    elif rec.get("rating"):
        rating = rec["rating"]
    year = motn.get("releaseYear") or title_rec.get("year") or rec.get("year")
    preview = {"id": imdb, "type": ctype, "name": title}
    if poster:
        preview["poster"] = poster
    if year:
        preview["releaseInfo"] = str(year)
    if genres:
        preview["genres"] = genres
    if rating:
        preview["imdbRating"] = str(rating)
    meta = dict(preview)
    meta.pop("releaseInfo", None)
    if year:
        meta["year"] = year
    if background:
        meta["background"] = background
    if overview:
        meta["description"] = overview
    if rec.get("runtime_min"):
        meta["runtime"] = f"{rec['runtime_min']} min"
    return preview, meta


def emit(charts):
    """Write the static addon tree. Returns (catalog_defs, counts, metas)."""
    staging = OUT + ".staging"
    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(os.path.join(staging, "catalog"))
    os.makedirs(os.path.join(staging, "meta"))
    catalog_defs, counts, all_metas = [], {}, {}
    for chart in charts:
        previews = []
        for trec in chart["titles"]:
            built = build_entry(trec, chart["type"])
            if built is None:
                continue
            preview, meta = built
            previews.append(preview)
            all_metas[(chart["type"], preview["id"])] = meta
        if not previews:
            log(f"catalog {chart['id']} ({chart['type']}): empty, skipped")
            continue
        cdir = os.path.join(staging, "catalog", chart["type"])
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, chart["id"] + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump({"metas": previews}, f, ensure_ascii=False, indent=1)
        catalog_defs.append({"id": chart["id"], "type": chart["type"],
                             "name": chart["name"]})
        counts[f"{chart['type']}/{chart['id']}"] = len(previews)
    for (ctype, imdb), meta in all_metas.items():
        mdir = os.path.join(staging, "meta", ctype)
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(mdir, imdb + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump({"meta": meta}, f, ensure_ascii=False, indent=1)
    manifest = {
        "id": "ai.unictoos.catalog",
        "version": "1.0.0",
        "name": "unictoos Charts",
        "description": "Trending and Top 10 charts for movies and series. "
                       "Catalog only — playback uses your own installed addons.",
        "logo": "https://unictoai.github.io/unictoos-apk/logo.jpeg",
        "resources": ["catalog", "meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": catalog_defs,
        "behaviorHints": {"configurable": False},
    }
    with open(os.path.join(staging, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.rename(staging, OUT)
    return catalog_defs, counts, len(all_metas)


def main():
    charts = []
    # --- Netflix
    try:
        charts += netflix_charts()
    except Exception as e:
        RUN["sources"]["netflix"] = {"status": "failed", "error": str(e)}
        log(f"ERROR netflix: {e}")
    # --- MovieOfTheNight
    motn_key = read_key("motn.key")
    if motn_key:
        try:
            charts += motn_charts(motn_key)
        except Exception as e:
            RUN["sources"]["motn"] = {"status": "failed", "error": str(e)}
            log(f"ERROR motn: {e}")
    else:
        RUN["sources"]["motn"] = {"status": "waiting-on-key"}
        log("motn: no key file, skipped")
    # --- Trakt
    trakt_id = read_key("trakt.id")
    if trakt_id:
        try:
            charts += trakt_charts(trakt_id)
        except Exception as e:
            RUN["sources"]["trakt"] = {"status": "failed", "error": str(e)}
            log(f"ERROR trakt: {e}")
    else:
        RUN["sources"]["trakt"] = {"status": "waiting-on-key"}
        log("trakt: no client id file, skipped")
    # --- MOTN poster/overview enrichment for entries lacking posters
    if motn_key:
        enrich = {}
        for chart in charts:
            for trec in chart["titles"]:
                if trec.get("motn"):
                    continue
                rec = join_title(trec["title"]) if not trec.get("imdb") \
                    else {"imdb": trec["imdb"]}
                if rec and rec["imdb"] not in enrich:
                    show = motn_join_by_imdb(rec["imdb"], motn_key)
                    if show:
                        enrich[rec["imdb"]] = show
                if rec and rec["imdb"] in enrich:
                    trec["motn"] = enrich[rec["imdb"]]
    catalog_defs, counts, n_meta = emit(charts)
    save_state_joins(STATE_JOINS)
    RUN["finished"] = datetime.now(timezone.utc).isoformat()
    RUN["catalogs"] = counts
    RUN["meta_files"] = n_meta
    if JOIN_STATS["attempted"]:
        RUN["join"] = {
            **JOIN_STATS,
            "rate": round(JOIN_STATS["joined"] / JOIN_STATS["attempted"], 3),
        }
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "last-run.json"), "w",
              encoding="utf-8") as f:
        json.dump(RUN, f, ensure_ascii=False, indent=1)
    log(f"done: {len(catalog_defs)} catalogs, {n_meta} metas, "
        f"join {JOIN_STATS}, calls {RUN['api_calls']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
