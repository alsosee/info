# alsosee/info

## General Repository Rules

- Before committing data changes, validate touched YAML parses.
- Check `_finder/schema.yml` before adding new top-level fields. Prefer existing canonical fields over aliases.
- Use plural schema fields where they already exist: `authors`, `publishers`, `directors`, `writers`, `producers`, `editors`, `developers`, `composers`, `designers`.
- Do not add singular aliases like `author` or `publisher`; normalize them to `authors` and `publishers`.
- If CI reports `Unknown field`, either fix the content to an existing field or add the field to `_finder/schema.yml` when it is legitimate reusable metadata.
- Keep commits scoped by content area when possible (`Movies`, `People`, `Shows`, `Games`, schema/workflow fixes separately). Do not mix unrelated staged changes into a commit.
- Use Conventional Commits for commit messages, for example `fix: model comic issues as objects` or `chore: update schema docs`.
- Never skip commit verification. Do not use `git commit --no-verify`; fix the failing check instead.
- When asked to review or commit a subset of directories, inspect and stage only those paths.
- Avoid committing generated/local/project-noise files unless explicitly requested.
- No `ref: Characters/...` entries in movie/show character lists unless the user explicitly asks for them. Keep character `name`, `actor`, and `voice` fields.

## References and Series

- Repository references are root-relative paths without `.yml` when they refer to actual files.
- Some fields, especially `series`, may be plain strings rather than file references; do not force them to resolve unless the surrounding data uses paths.
- Missing-reference scans are useful, but not every missing reference is a blocker. Franchise strings like `Star Wars`, `Rocky`, or `Avatar` may be intentional plain metadata.
- If a concrete target exists, prefer the precise path (for example `Movies/Series/Pitch Perfect`) over a bare string when the local pattern supports it.

## CI and Finder

- `_finder/schema.yml` is the source for finder schema validation and unknown-field annotations.
- GitHub Actions Node runtime warnings should be fixed by moving actions to Node 24-compatible major versions when available.
- Meilisearch `521` errors from `search.alsosee.info` are usually upstream/proxy runtime failures, not content schema failures. If schema annotations are clean and `521` persists, retry CI or fix retry/backoff in the finder service.

## Companies Schema

Files live at `Companies/{Company Name}.yml`.

Fields (from `Warner Bros. Pictures.yml`):
- `name` — display name
- `description` — one-line description (plain string, not block scalar)
- `website` — official site URL
- `founded` — YYYY-MM-DD
- `founders` — list of founder names
- `wikipedia` — Wikipedia URL
- `twitter` / `instagram` / `facebook` / `youtube` — social URLs

## Shows Schema

Show season files live at `Shows/{year}/{Show Name}.yml` for the first season or limited/standalone entry, and `Shows/{year}/{Show Name}, Season {N}.yml` for later seasons.

Fields (from `CriticalRole.yml`):
- `name` — display name
- `website` — official site URL
- Other social/streaming fields may apply (infer from existing files)
- For multi-season shows, use `series: Show Name` as a plain string. Do not point it to a `Series` subdirectory under `Shows`; generated series pages infer the path from `Shows`.

## Video Game Schema

Files live at `Games/Video/{year}/{title}.yml`.
Also: `Games/Tabletop/{title}.yml`, `Games/Awards/{award}/{year}.yml`.

Fields (from `The Last of Us.yml`):
- `name`, `description`, `released`, `genres` (list)
- `wikipedia`, `metacritic`, `fandom`, `ign`, `spotify` — source/review URLs (no imdb/tmdb)
- `website` — official site
- `developers` — string or list
- `publishers` — string or list
- `directors`, `designer`, `writers`, `composers` — production credits
- `characters` — list with `name` + `actor:` (uses `actor:` not `voice:` per existing game data)

## Music Schema

Album files live at `Music/{year}/{Album Name}.yml`.

Fields:
- `name`, `description`, `released` (YYYY-MM-DD), `genres` (list)
- `artists` — path(s) to `People/` or `Bands/` entries (string or list)
- `label` — record label (string, or path to `Companies/`)
- `wikipedia`, `spotify`, `apple_music`, `discogs`, `metacritic`, `pitchfork`, `rateyourmusic` — external refs
- `tracks` — list with `name`, `length` (e.g. `3m54s`), optional `featuring`, `wikipedia`, `spotify`

Solo artists live in `People/` (same as actors/directors). Add `spotify` field for music-specific link.

## Bands Schema

Files live at `Bands/{Band Name}.yml`.

Fields:
- `name` — display name
- `description` — one-line description
- `formed` — year (integer)
- `disbanded` — year (integer, omit if still active)
- `members` — canonical/classic lineup (informal, not a complete roster — actual credits live on album/concert files)
- `wikipedia`, `spotify`, `website` — external refs
- `twitter` / `instagram` / `facebook` / `youtube` — social URLs

## Concerts Schema

Concert files live at `Events/{year}/{YYYY-MM-DD} {Artist} at {Venue}.yml`.

Fields:
- `name` — display name (e.g. "Prince at Madison Square Garden")
- `date` — YYYY-MM-DD
- `description` — summary
- `artist` — path to `People/` or `Bands/` entry
- `venue` — path to `Locations/` entry or plain string
- `wikipedia`, `setlist` (setlist.fm URL) — external refs

## Note on Field Reference

`references/fields.md` lives in the skill bundle at `/Users/chuhlomin/.Codex/skills/alsosee-add/references/fields.md`, not in this repo.
