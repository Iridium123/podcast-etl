# Future refactor: acquisition as a phase, `Episode.audio_path` as a field

**Status: idea, deliberately deferred.** Not scheduled. Written down so the
trigger condition and shape aren't lost.

## The observation

The torrent-feed work (PR #70) exposed an asymmetry: torrent sources acquire
audio in a pre-pipeline fetch phase, while RSS sources acquire it via the
`download` pipeline step. That asymmetry costs one validation rule ("no
`download` in a unit3d pipeline") and one compatibility shim — torrent-spawned
episodes get a *synthesized* `download` StepStatus so downstream steps can find
their audio.

The shim exists because downstream steps locate audio by reading
`episode.status["download"].result["path"]` — an acquisition artifact smuggled
through step status. `download` was always acquisition wearing a transformation
costume; the torrent path just made it visible.

## The eventual shape

- **Acquisition is a per-source phase**, not a pipeline step. Each source
  (RSS HTTP download, torrent fetch, whatever comes next) is responsible for
  getting audio onto disk before the pipeline runs. Episodes always arrive at
  the pipeline with audio present.
- **`Episode.audio_path` becomes a real field** (relative to the podcast dir),
  set at acquisition time. Downstream steps read it directly; the
  status-smuggling and the glob fallback in `tag`/`detect_ads`/etc. go away.
- **The `pipeline:` config list becomes pure transformations** — `download`
  disappears from it, and the unit3d-specific validation rule becomes
  unnecessary (nothing to misplace).
- Resumability for RSS acquisition keeps working the way `DownloadStep`
  already does internally: skip when the file exists with the right size.

## Why not now

Zero new capability for a refactor that touches every config's `pipeline:`
list, every step's audio resolution, the resumability/`--overwrite`/`--step
download` semantics, and most of the test suite. The current asymmetry is a
documented, one-rule wart; this trade only flips when the wart starts
multiplying.

## Trigger

Do this when (and only when) a **third acquisition mode** lands — e.g. a
yt-dlp source or watch-folder ingestion. At that point the per-source phase
stops being symmetry-for-its-own-sake and becomes the extension mechanism.

Migration note: the synthesized `download` status is deliberately the only
glue involved — during a transition, steps can prefer `Episode.audio_path`
and fall back to the download status, so both shapes coexist until configs
and on-disk episode JSONs are migrated.
