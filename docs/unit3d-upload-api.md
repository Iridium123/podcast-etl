# UNIT3D Web Form Upload API

How to programmatically upload torrents to a UNIT3D tracker via its web form. UNIT3D does not expose a public REST API for uploads, so this documents the browser-based flow you must replicate.

## Overview

The upload flow has three phases:

1. **Authenticate** — establish a session via login or remember cookie
2. **Get CSRF token** — load the create page to extract the token
3. **POST the upload** — submit form data + files to `/torrents`

All requests must use a cookie jar / session to maintain state.

## 1. Authentication

### Option A: Remember Cookie

Set the cookie named `remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d` on the tracker's domain, then make any authenticated request (e.g. `GET /torrents/create`) to activate the session.

If the response redirects to `/login`, the cookie is expired or invalid.

### Option B: Username / Password

```
GET /login
```

Extract the CSRF token from the HTML (see [CSRF Tokens](#csrf-tokens)).

```
POST /login
Content-Type: application/x-www-form-urlencoded

_token=<csrf_token>&username=<user>&password=<pass>&remember=on
```

**Success:** 301/302 redirect to the home page. Follow the redirect to finalize the session.

**Failure cases:**
- Redirect to `/login` — bad credentials
- Redirect to `/two-factor-challenge` — account has 2FA; use remember cookie instead
- Any other status — unexpected error

## 2. Get CSRF Token for Upload

```
GET /torrents/create
```

Extract the `_token` value from the HTML response (see [CSRF Tokens](#csrf-tokens)).

## 3. Upload Torrent

```
POST /torrents
Content-Type: multipart/form-data
```

### Required Fields

| Field         | Type   | Description                              |
|---------------|--------|------------------------------------------|
| `_token`      | string | CSRF token from the create page          |
| `name`        | string | Display name for the torrent             |
| `description` | string | Torrent description (plain text or BBCode) |
| `category_id` | string | Category ID (numeric)                    |
| `type_id`     | string | Type ID (numeric)                        |
| `torrent`     | file   | The `.torrent` file (`application/x-bittorrent`) |

### Optional Fields

| Field              | Type   | Default | Description                                 |
|--------------------|--------|---------|---------------------------------------------|
| `imdb`             | string | `"0"`   | IMDb ID (numeric, `0` = none)               |
| `tvdb`             | string | `"0"`   | TVDB ID                                     |
| `tmdb`             | string | `"0"`   | TMDB ID                                     |
| `mal`              | string | `"0"`   | MyAnimeList ID                              |
| `igdb`             | string | `"0"`   | IGDB ID                                     |
| `stream`           | string | `"0"`   | Stream optimized flag                       |
| `sd`               | string | `"0"`   | SD quality flag                             |
| `anon`             | string | `"0"`   | Upload anonymously (`"1"` = yes)            |
| `personal_release` | string | `"0"`   | Mark as personal release (`"1"` = yes)      |
| `mod_queue_opt_in` | string | `"0"`   | Opt in to moderation queue (`"1"` = yes)    |
| `torrent-cover`    | file   | —       | Cover image (JPEG, 1:1 aspect ratio)        |
| `torrent-banner`   | file   | —       | Banner image (JPEG, 16:9 aspect ratio)      |

### Response

**Success:** 301/302 redirect to `/torrents/<id>`. Extract the torrent ID from the `Location` header.

**Validation error:** 301/302 redirect back to `/torrents/create`. Follow the redirect and parse `<li>` elements in the error alert for messages.

**CSRF expired:** HTTP 419.

## CSRF Tokens

Every form submission requires a `_token` value. Extract it from the HTML of the preceding GET request:

```
<input type="hidden" name="_token" value="abcdef123456...">
```

Regex pattern (covers both attribute orderings):

```
name="_token"\s+value="([^"]+)"
value="([^"]+)"\s+name="_token"
```

## Example (pseudocode)

```
session = new HttpSession()

# Authenticate
page = session.get("https://tracker.example.com/login")
token = extractCsrfToken(page.body)
session.post("https://tracker.example.com/login", form={
    "_token": token,
    "username": "myuser",
    "password": "mypass",
    "remember": "on",
})

# Get upload token
create_page = session.get("https://tracker.example.com/torrents/create")
token = extractCsrfToken(create_page.body)

# Upload
response = session.post("https://tracker.example.com/torrents",
    multipart={
        "_token": token,
        "name": "My Podcast - Episode 1 [2026-03-01/MP3-128kbps]",
        "description": "Episode description here",
        "category_id": "14",
        "type_id": "9",
        "torrent": file("episode.torrent"),
        "torrent-cover": file("cover.jpg"),   # optional
        "torrent-banner": file("banner.jpg"), # optional
        "anon": "0",
        "imdb": "0",
        "tvdb": "0",
        "tmdb": "0",
        "mal": "0",
        "igdb": "0",
    },
    follow_redirects=false,
)

if response.status == 301 or response.status == 302:
    location = response.headers["Location"]
    if "torrents/create" in location:
        # Validation error — follow redirect and parse error messages
    else:
        # Success — torrent ID is in the location path
```
