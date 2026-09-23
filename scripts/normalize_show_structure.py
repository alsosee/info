#!/usr/bin/env python3
"""Normalize series membership and season-level director credits.

The script preserves the surrounding YAML text instead of serializing whole
documents. Run without --write to preview the number of affected files.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SHOWS = ROOT / "Shows"
SEASON_SUFFIX = re.compile(r"^(.*), Season (\d+)$")
TOP_LEVEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")


def scalar(value: str) -> str:
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .&+'!-]*", value)
        and ": " not in value
        and value.lower() not in {"null", "true", "false", "yes", "no"}
    ):
        return value
    return json.dumps(value, ensure_ascii=False)


def values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def field_block(key: str, field_values: list[str]) -> list[str]:
    if len(field_values) == 1:
        return [f"{key}: {scalar(field_values[0])}\n"]
    result = [f"{key}:\n"]
    result.extend(f"  - {scalar(value)}\n" for value in field_values)
    return result


def replace_top_level_field(text: str, key: str, field_values: list[str]) -> str:
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(f"{key}:")),
        None,
    )
    replacement = field_block(key, field_values)

    if start is not None:
        end = start + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and TOP_LEVEL.match(line):
                break
            end += 1
        lines[start:end] = replacement
        return "".join(lines)

    name_index = next(
        index for index, line in enumerate(lines) if line.startswith("name:")
    )
    lines[name_index + 1 : name_index + 1] = replacement
    return "".join(lines)


def remove_episode_field(text: str, key: str) -> str:
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    index = 0
    marker = f"    {key}:"
    while index < len(lines):
        if not lines[index].startswith(marker):
            result.append(lines[index])
            index += 1
            continue

        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip(" ")) <= 4:
                break
            index += 1
    return "".join(result)


def load_shows() -> tuple[dict[Path, dict], list[tuple[Path, str]]]:
    documents: dict[Path, dict] = {}
    errors: list[tuple[Path, str]] = []
    for path in sorted(SHOWS.rglob("*.yml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as error:
            errors.append((path, str(error).splitlines()[0]))
            continue
        if isinstance(document, dict):
            documents[path] = document
    return documents, errors


def series_names(documents: dict[Path, dict]) -> dict[Path, str]:
    groups: dict[str, list[Path]] = {}
    for path in documents:
        match = SEASON_SUFFIX.match(path.stem)
        base = match.group(1) if match else path.stem
        groups.setdefault(base, []).append(path)

    additions: dict[Path, str] = {}
    for base, paths in groups.items():
        if not any(SEASON_SUFFIX.match(path.stem) for path in paths):
            continue

        existing = [
            documents[path].get("series")
            for path in paths
            if isinstance(documents[path].get("series"), str)
        ]
        if existing:
            canonical = Counter(existing).most_common(1)[0][0]
        else:
            first = next((path for path in paths if path.stem == base), None)
            name = documents[first].get("name") if first else None
            canonical = name if isinstance(name, str) else base
            canonical = SEASON_SUFFIX.sub(r"\1", canonical)

        for path in paths:
            if not documents[path].get("series"):
                additions[path] = canonical
    return additions


def director_changes(
    documents: dict[Path, dict],
) -> tuple[dict[Path, list[str]], set[Path]]:
    merged: dict[Path, list[str]] = {}
    remove_episode_directors: set[Path] = set()

    for path, document in documents.items():
        episodes = document.get("episodes")
        if not isinstance(episodes, list) or not episodes:
            continue

        episode_sets: list[tuple[str, ...]] = []
        episode_union: list[str] = []
        complete = True
        for episode in episodes:
            if not isinstance(episode, dict):
                complete = False
                continue
            directors = values(episode.get("directors"))
            if not directors:
                complete = False
                continue
            episode_sets.append(tuple(directors))
            episode_union.extend(directors)

        episode_union = unique(episode_union)
        if not episode_union:
            continue

        combined = unique(values(document.get("directors")) + episode_union)
        if combined != values(document.get("directors")):
            merged[path] = combined

        if (
            len(episodes) >= 2
            and complete
            and len(episode_sets) == len(episodes)
            and len(set(episode_sets)) == 1
        ):
            remove_episode_directors.add(path)

    return merged, remove_episode_directors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    documents, errors = load_shows()
    series = series_names(documents)
    directors, remove_directors = director_changes(documents)
    touched = set(series) | set(directors) | remove_directors

    print(f"series additions: {len(series)}")
    print(f"director unions: {len(directors)}")
    print(f"redundant episode-director removals: {len(remove_directors)}")
    print(f"files touched: {len(touched)}")
    for path, error in errors:
        print(f"skipped invalid YAML: {path.relative_to(ROOT)}: {error}")

    if not args.write:
        return 0

    for path in sorted(touched):
        text = path.read_text(encoding="utf-8")
        if path in series:
            text = replace_top_level_field(text, "series", [series[path]])
        if path in directors:
            text = replace_top_level_field(text, "directors", directors[path])
        if path in remove_directors:
            text = remove_episode_field(text, "directors")
        yaml.safe_load(text)
        path.write_text(text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
