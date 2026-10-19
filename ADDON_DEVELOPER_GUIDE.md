# unictoos Addon Developer Guide

**Draft v1 — 2026-10-19.** This guide explains how to build a content addon for the unictoos app. If you can write basic JavaScript or serve a JSON file, you can build one.

## 1. What is an addon?

An addon is a small package, hosted on the web, that tells unictoos where to find movies and shows. The app itself ships with no content sources; users paste your addon's URL into the app to install it.

There are two kinds:

| | Stremio addon (recommended) | Vega provider (advanced) |
|---|---|---|
| What it is | Plain JSON files on a web server | JavaScript modules the app downloads and runs in a sandbox |
| Code runs on the phone? | No — the app just reads your JSON | Yes — inside a locked sandbox with only network access |
| Best for | A fixed catalog (your own films, public-domain collections, metadata) | Sites that need custom logic (search, scraping, login-free APIs) |
| Difficulty | Easy | Medium |

You can also install community Stremio addons directly — unictoos speaks the open Stremio addon protocol.

## 2. Ground rules (non-negotiable)

1. **HTTPS for your provider URLs.** The manifest URL you give users, and every module download, must be `https://` — plain-`http://` provider URLs are rejected outright (the only exception is `http://localhost` for local development). Inside your modules and in media stream links, plain HTTP technically works, but use HTTPS everywhere: HTTP traffic can be read or tampered with on the network.
2. **No adult content.** The app refuses adult addons automatically at install time and filters adult results everywhere. Don't build one.
3. **Only lawful sources.** You are responsible for what your addon points to. Link only content you own, content in the public domain, or Creative Commons-licensed content. unictoos is "the clean way to watch" — keep it that way.
4. **Keep it light.** Each JavaScript module must be under 2 MB. Network requests time out after 15–30 seconds.
5. **No surprises.** Your addon must not ask for passwords, show its own ads, or phone home with user data.

## 3. Try it first

Before building, install a working addon so you see the end result:

1. Open unictoos → Settings → Add provider.
2. Paste `https://raw.githubusercontent.com/unictoai/unictoos-test-provider/main/manifest.json`
3. You should see "Public Domain Movies" with 12 classic public-domain films.

That test provider is open source and is the reference implementation for everything below.

## 4. Option A: Stremio addon

A Stremio addon is just a `manifest.json` plus a few JSON endpoints. No code runs on the user's phone.

### 4.1 manifest.json

Serve this at the root of your HTTPS host. The app requires `id`, `name`, `version`, `resources`, and `catalogs`.

```json
{
  "id": "com.example.myfilms",
  "name": "My Classic Films",
  "description": "Public-domain classics, curated by me.",
  "version": "1.0.0",
  "resources": ["catalog", "meta", "stream"],
  "types": ["movie"],
  "catalogs": [
    {
      "type": "movie",
      "id": "classics",
      "name": "Classics",
      "extra": [{ "name": "search", "isRequired": false }]
    }
  ],
  "logo": "https://example.com/logo.png",
  "contactEmail": "me@example.com"
}
```

Field notes:

- `id` must be unique across all addons (reverse-domain style works well).
- `resources` lists what your addon serves: `catalog`, `meta`, `stream`, `subtitles`. You can also use objects like `{"name": "stream", "types": ["movie"], "idPrefixes": ["tt"]}` to limit a resource to certain ID prefixes.
- `catalogs[].extra` with `{"name": "search"}` makes that catalog searchable from the app's search bar.
- Top-level `idPrefixes` applies to every resource unless a resource overrides it.

### 4.2 Endpoints the app calls

Replace `{base}` with the folder your `manifest.json` lives in.

| Endpoint | Returns |
|---|---|
| `{base}/catalog/{type}/{id}.json` | `{"metas": [{id, type, name, poster, background, description, releaseInfo, imdbRating}]}` |
| `{base}/catalog/{type}/{id}/skip={n}.json` | Next page of the same catalog |
| `{base}/catalog/{type}/{id}/search={query}.json` | Search results (only for catalogs with the `search` extra) |
| `{base}/meta/{type}/{id}.json` | `{"meta": {id, type, name, poster, background, description, genres, runtime, releaseInfo, videos: [{id, title, season, episode, released, thumbnail, overview}]}}` |
| `{base}/stream/{type}/{videoId}.json` | `{"streams": [{url, name, title, description, subtitles: [{url, lang}], behaviorHints}]}` |
| `{base}/subtitles/{type}/{id}.json` | `{"subtitles": [{url, lang}]}` (optional) |

Response shapes above are pseudocode (real JSON needs quoted keys). For series, `videoId` is `seriesId:season:episode` (for example `tt12345:1:3`). For movies it is just the ID.

Stream notes:

- Stream URLs may be `http://` or `https://` (the app accepts both), but prefer HTTPS. Entries with only an info-hash or YouTube ID are skipped.
- Put quality info in the text: the app reads `1080p`, `720p`, `4k`, etc. from the name/title/description.
- If your streams need request headers (referer, user-agent), pass them as `behaviorHints: { proxyHeaders: { request: { "Referer": "..." } } }` — the app forwards them.
- Subtitles ride along inside each stream's `subtitles` array, or via the dedicated subtitles endpoint.

### 4.3 Hosting

Any static HTTPS host works: GitHub Pages, Netlify, Cloudflare Pages, or a raw file on GitHub. Users install with either the full manifest URL (`https://example.com/manifest.json`) or just the base (`https://example.com`) — the app tries both.

## 5. Option B: Vega provider

A Vega provider is a manifest plus a set of CommonJS JavaScript modules. The app downloads the modules once, caches them on the device, and runs them inside a sandbox where the only superpower is `fetch` (proxied through the app). Cached modules are deleted when the user uninstalls the addon.

### 5.1 Folder layout

```
manifest.json
dist/
  myprovider/
    catalog.js     (required)
    posts.js       (required)
    meta.js        (required)
    stream.js      (required)
    episodes.js    (optional — for series)
    settings.js    (optional — reserved for future use)
```

### 5.2 manifest.json

Unlike Stremio, this manifest is a **JSON array** — one entry per provider. One manifest can carry several providers.

```json
[
  {
    "value": "myprovider",
    "display_name": "My Classic Films",
    "version": "1.0.0",
    "icon": "https://example.com/logo.png",
    "type": "global"
  }
]
```

- `value` must match the folder name under `dist/` (URL-encoded if it has special characters).
- `display_name` is what users see. `version` is your version string.

### 5.3 Modules

Every module is CommonJS: define `async` functions and attach them to `module.exports`. Each function receives one `args` object and returns a value (or a Promise of one). **Export names must match exactly** — the sandbox calls them by name.

Two naming notes before the reference: `provider` and `providerValue` in the args are always the same string — your manifest entry's `value`. And `getEpisodes` is the odd one out: it receives `url`, not `link` — that `url` is the `episodesLink` you returned from `getMeta` (see below), i.e. the page that lists the episodes.

| Module | Export | Exact args | Returns |
|---|---|---|---|
| `catalog.js` | `catalog` | `{ providerValue }` | `[{ title, filter }]` — empty `filter` means "latest" |
| `posts.js` | `getPosts` | `{ filter, page, providerValue }` — `page` starts at 1, ~20 items per page | `[{ id, title, link, image, provider?, tag? }]` |
| `posts.js` | `getSearchPosts` (optional) | `{ searchQuery, page, providerValue }` | same shape as `getPosts` |
| `meta.js` | `getMeta` | `{ link, provider }` — `link` is the post's `link` | `{ title, image, description, rating, type, linkList? }` |
| `stream.js` | `getStream` | `{ link, type, provider, providerValue }` — `link` is the episode/movie link | `[{ server, link, type, quality, headers?, subtitles? }]` |
| `episodes.js` | `getEpisodes` (series only) | `{ url, providerValue }` — `url` is the `episodesLink` from `getMeta`'s `linkList` | `[{ id, title, link, description }]` |

**catalog.js** — required. Export: `catalog`

```js
async function catalog(args) {
  // args = { providerValue }
  return [
    { title: 'All Films', filter: '' },   // empty filter = latest
    { title: 'Horror', filter: 'horror' },
  ];
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports.catalog = catalog;
}
```

Returns the browsable sections. Each `{title, filter}` becomes a row in the app.

**posts.js** — required. Exports: `getPosts`, `getSearchPosts` (optional but recommended)

```js
async function getPosts(args) {
  // args = { filter, page, providerValue } — page starts at 1, ~20 items per page
  return [
    {
      id: 'night_of_the_living_dead',
      title: 'Night of the Living Dead (1968)',
      link: 'night_of_the_living_dead',   // passed to getMeta/getStream as args.link
      image: 'https://example.com/posters/night.jpg',
      tag: 'HD',
    },
  ];
}

async function getSearchPosts(args) {
  // args = { searchQuery, page, providerValue }
  return [];
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports.getPosts = getPosts;
  module.exports.getSearchPosts = getSearchPosts;
}
```

`id` should be stable and unique. `link` is the opaque reference your other modules receive — it can be an ID, a slug, or a URL.

**meta.js** — required. Export: `getMeta`

```js
async function getMeta(args) {
  // args = { link, provider } — link is the post's link from getPosts
  return {
    title: 'Night of the Living Dead',
    image: 'https://example.com/posters/night.jpg',
    description: 'A group of people hide from zombies...',
    rating: '7.8',
    type: 'movie', // or 'series'
  };
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports.getMeta = getMeta;
}
```

For series, `getMeta` should also return a `linkList` array — this is how the app finds episodes:

```js
async function getMeta(args) {
  // args = { link, provider } — link is the post's link from getPosts
  return {
    title: 'Example Show',
    image: 'https://example.com/posters/show.jpg',
    description: 'A great series.',
    type: 'series',
    linkList: [
      // Option A: point at a page listing episodes -> the app calls
      // getEpisodes({ url: <episodesLink>, providerValue })
      { title: 'Season 1', episodesLink: 'https://example.com/show/s1' },
      // Option B: list episodes directly -> no episodes.js call needed
      { title: 'Season 1', directLinks: [{ title: 'S1 E1', link: 'ep1-play-link', type: 'series' }] },
    ],
  };
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports.getMeta = getMeta;
}
```

For movies, `linkList[].directLinks` entries become the stream targets (one per quality) — each `link` is passed to `getStream` as `args.link`. If a movie has no `directLinks`, the post's own `link` goes to `getStream` instead.

**stream.js** — required. Export: `getStream`

```js
async function getStream(args) {
  // args = { link, type, provider, providerValue }
  return [
    {
      server: 'My Server',
      link: 'https://example.com/videos/night.mp4',
      type: 'direct',
      quality: '720p',
      headers: { Referer: 'https://example.com/' }, // optional
      subtitles: [{ title: 'English', language: 'en', uri: 'https://example.com/subs/night.srt' }], // optional
    },
  ];
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports.getStream = getStream;
}
```

Return one entry per available source. `link` must be a direct `http(s)://` media URL (HTTPS preferred). If your source needs headers, include them — the player will send them.

**episodes.js** — optional (series only). Export: `getEpisodes`

```js
async function getEpisodes(args) {
  // args = { url, providerValue }
  return [
    { id: 's1e1', title: 'Pilot', link: 'episode-1-link', description: '...' },
  ];
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports.getEpisodes = getEpisodes;
}
```

Each episode's `link` is passed to `getStream` as `args.link`, exactly like a movie post. Remember: `args.url` here is the `episodesLink` you returned from `getMeta` — typically a page you fetch and parse for the episode list.

### 5.4 Sandbox rules and limits

- Your code runs isolated from the app. The only network API is `fetch` (proxied through the app).
- `fetch` accepts `http://` and `https://` URLs, but use HTTPS — plain-HTTP responses can be tampered with on the network.
- Hard limits, enforced by the app: each module ≤ 2 MB of source; each `fetch` call times out after 30 s and its body is truncated at 6 MB; a single module call (load + run) is killed after 60 s.
- Modules are cached on the device after install; bump your manifest `version` and re-install when you ship updates, or users can reinstall to refresh.

### 5.5 Complete working example

The public-domain test provider is a full, working Vega provider with 12 real films. Read it end to end before writing your own: `https://github.com/unictoai/unictoos-test-provider`

## 6. Installing and sharing your addon

1. Host your files on any static HTTPS host (GitHub Pages and `raw.githubusercontent.com` both work).
2. In unictoos: Settings → Add provider → paste the manifest URL (or just the base URL).
3. The app detects the type automatically, validates the manifest, and installs it.
4. Share the URL with users — that's it. No app update needed on your side or theirs.

## 7. Pre-share checklist

- [ ] Manifest loads over HTTPS and returns valid JSON.
- [ ] Every required module exists at `dist/{value}/{name}.js` and sets `module.exports`.
- [ ] Export names match exactly (`catalog`, `getPosts`, `getMeta`, `getStream`, …).
- [ ] Browsing, search, details, and playback all work on a real phone.
- [ ] No adult content, no pirated content, no trackers.
- [ ] You bumped `version` since the last published change.

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Could not detect a provider" | URL doesn't serve a manifest at the URL or at `URL/manifest.json`; or the JSON shape is wrong (array = Vega, object with `id`+`name` = Stremio). |
| "Provider URLs must use HTTPS" | You're using `http://`. Switch to HTTPS (localhost is exempt for dev). |
| "Required module posts.js could not be downloaded" | Wrong path — modules must live at `dist/{value}/posts.js` relative to the manifest. |
| "missing module.exports" | The file downloaded but has no `module.exports` / `exports.` assignment. |
| Install works but catalogs are empty | `catalog.js` must export a function named exactly `catalog` returning `[{title, filter}]`. |
| Search returns nothing | Vega: export `getSearchPosts`. Stremio: add `{"name": "search"}` to a catalog's `extra`. |
| Streams don't play | `link` must be a direct `http(s)://` media URL (HTTPS preferred). Info-hash-only and YouTube-ID-only entries are skipped. |
| Adult content warning on install | The name, URL, or catalogs tripped the adult filter. Rename and remove adult entries. |

---

*Questions or a broken addon? Contact unictoos.help@gmail.com. This guide will grow as the addon system does — the app, not this document, is the final authority on the format.*
