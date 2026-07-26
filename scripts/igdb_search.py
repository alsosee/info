#!/usr/bin/env python3
"""Search IGDB for game YAML entries that do not have an igdb field.

Usage:
    IGDB_CLIENT_ID=xxx IGDB_CLIENT_SECRET=yyy python scripts/igdb_search.py Games/Video/2025
    IGDB_CLIENT_ID=xxx IGDB_ACCESS_TOKEN=yyy python scripts/igdb_search.py Games/Video/2025 --apply

By default the script is read-only. With ``--apply``, it inserts the best
high-confidence candidate URL into entries that are missing ``igdb``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import yaml


IGDB_API = "https://api.igdb.com/v4"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
YAML_SUFFIXES = {".yml", ".yaml"}
USER_AGENT = "alsosee-info-igdb-search/1.0"


def curl(args: list[str], *, timeout: int) -> bytes:
    command = [
        "curl",
        "-fsSL",
        "--max-time",
        str(timeout),
        "-H",
        f"User-Agent: {USER_AGENT}",
        *args,
    ]
    try:
        return subprocess.check_output(command)
    except subprocess.TimeoutExpired as error:
        raise URLError("curl timed out") from error
    except subprocess.CalledProcessError as error:
        raise URLError(f"curl failed with exit code {error.returncode}") from error


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    data: str | None,
    timeout: int,
) -> dict | list:
    args: list[str] = []
    for key, value in headers.items():
        args.extend(["-H", f"{key}: {value}"])
    if data is not None:
        args.extend(["--data-binary", data])
    args.append(url)
    return json.loads(curl(args, timeout=timeout))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class AccessTokenProvider:
    def __init__(
        self,
        *,
        client_id: str,
        access_token: str,
        client_secret: str,
        timeout: int,
    ) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.client_secret = client_secret
        self.timeout = timeout

    def get(self) -> str:
        if self.access_token:
            return self.access_token
        if not self.client_id:
            raise ValueError("IGDB_CLIENT_ID is not set")
        if not self.client_secret:
            raise ValueError("IGDB_ACCESS_TOKEN or IGDB_CLIENT_SECRET is not set")

        token_data = post_json(
            TWITCH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                }
            ),
            timeout=self.timeout,
        )
        if not isinstance(token_data, dict) or not token_data.get("access_token"):
            raise ValueError("Twitch token response did not include access_token")
        self.access_token = str(token_data["access_token"])
        return self.access_token


def igdb_headers(client_id: str, access_token: str) -> dict[str, str]:
    if not client_id:
        raise ValueError("IGDB_CLIENT_ID is not set")
    if not access_token:
        raise ValueError("IGDB_ACCESS_TOKEN or IGDB_CLIENT_SECRET is not set")
    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "text/plain",
    }


def yaml_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]

    return sorted(
        child
        for child in path.rglob("*")
        if child.is_file() and child.suffix.lower() in YAML_SUFFIXES
    )


def is_game_entry(path: Path) -> bool:
    parts = path.parts
    return "Games" in parts and "Awards" not in parts


def read_entry(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("YAML document is not a mapping")
    return data


def release_year(entry: dict) -> str | None:
    value = entry.get("released")
    if value is None:
        return None

    match = re.match(r"^(\d{4})", str(value))
    return match.group(1) if match else None


def path_year(path: Path) -> str | None:
    for part in reversed(path.parts):
        if re.match(r"^\d{4}$", part):
            return part
    return None


def normalize_title(value: str) -> str:
    value = expand_roman_numbers(value)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "game"


def quote_apicalypse(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


ROMAN_NUMERALS = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
}
NUMBER_ROMANS = {value: key for key, value in ROMAN_NUMERALS.items()}
ROMAN_PATTERN = re.compile(r"\b(?:I|II|III|IV|V|VI|VII|VIII|IX|X)\b")


def expand_roman_numbers(value: str) -> str:
    return ROMAN_PATTERN.sub(
        lambda match: str(ROMAN_NUMERALS.get(match.group(0), match.group(0))),
        value,
    )


def roman_range_variants(title: str) -> list[str]:
    variants: list[str] = []
    pattern = re.compile(r"\b([IVXLCDM]+)\s*[-\u2010-\u2015]\s*([IVXLCDM]+)\b")

    def replace_with(separator: str):
        def replacement(match: re.Match[str]) -> str:
            start = ROMAN_NUMERALS.get(match.group(1))
            end = ROMAN_NUMERALS.get(match.group(2))
            if start is None or end is None or start >= end:
                return match.group(0)
            values = [NUMBER_ROMANS[number] for number in range(start, end + 1)]
            return separator.join(values)

        return pattern.sub(replacement, title)

    for separator in ["•", " star ", " "]:
        variant = replace_with(separator)
        if variant != title and variant not in variants:
            variants.append(variant)
    return variants


def title_variants(title: str) -> list[str]:
    variants: list[str] = []
    for variant in [
        title,
        expand_roman_numbers(title),
        title.replace("–", "-").replace("—", "-"),
        re.sub(r":?\s+Deluxe Edition$", "", title, flags=re.IGNORECASE),
        re.sub(r":?\s+Starring Lara Croft$", "", title, flags=re.IGNORECASE),
        re.sub(r"\s+", " ", re.sub(r"[\u2010-\u2015]", "-", title)).strip(),
        *roman_range_variants(title),
    ]:
        if variant and variant not in variants:
            variants.append(variant)
    return variants


def igdb_url(result: dict) -> str:
    slug = result.get("slug") or slugify(str(result.get("name") or "game"))
    return f"https://www.igdb.com/games/{slug}"


def result_year(result: dict) -> str | None:
    timestamp = result.get("first_release_date")
    if not isinstance(timestamp, int):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y")


def search_games(
    client_id: str,
    access_token: str,
    title: str,
    *,
    limit: int,
    timeout: int,
) -> list[dict]:
    query = (
        f'search "{quote_apicalypse(title)}";'
        " fields id,name,slug,first_release_date,total_rating_count,hypes;"
        " where version_parent = null;"
        f" limit {limit};"
    )
    data = post_json(
        f"{IGDB_API}/games",
        headers=igdb_headers(client_id, access_token),
        data=query,
        timeout=timeout,
    )
    if not isinstance(data, list):
        raise ValueError("IGDB games response was not a list")
    return [item for item in data if isinstance(item, dict)]


def search_game_variants(
    client_id: str,
    access_token: str,
    title: str,
    *,
    limit: int,
    timeout: int,
) -> list[dict]:
    results: list[dict] = []
    seen_ids: set[int] = set()
    for variant in title_variants(title):
        for result in search_games(
            client_id,
            access_token,
            variant,
            limit=limit,
            timeout=timeout,
        ):
            result_id = result.get("id")
            if isinstance(result_id, int) and result_id in seen_ids:
                continue
            if isinstance(result_id, int):
                seen_ids.add(result_id)
            results.append(result)
    return results


def candidate_score(expected_title: str, expected_year: str | None, result: dict) -> float:
    expected_norm = normalize_title(expected_title)
    actual_title = str(result.get("name") or "")
    actual_norm = normalize_title(actual_title)
    title_similarity = SequenceMatcher(None, expected_norm, actual_norm).ratio()
    expected_slug = slugify(expected_title)
    actual_slug = str(result.get("slug") or "")

    score = title_similarity * 100
    if expected_slug == actual_slug:
        score += 25

    actual_year = result_year(result)
    if expected_year and actual_year:
        if expected_year == actual_year:
            score += 15
        else:
            score -= 25
    elif expected_year and not actual_year:
        score -= 15

    score += min(float(result.get("total_rating_count") or 0), 100) / 100
    score += min(float(result.get("hypes") or 0), 1000) / 1000
    return score


def ranked_candidates(
    expected_title: str,
    expected_year: str | None,
    results: list[dict],
) -> list[dict]:
    return sorted(
        results,
        key=lambda result: candidate_score(expected_title, expected_year, result),
        reverse=True,
    )


def is_high_confidence(expected_title: str, expected_year: str | None, result: dict) -> bool:
    expected_norm = normalize_title(expected_title)
    actual_norm = normalize_title(str(result.get("name") or ""))
    title_similarity = SequenceMatcher(None, expected_norm, actual_norm).ratio()
    slug_exact = slugify(expected_title) == str(result.get("slug") or "")
    actual_year = result_year(result)
    year_ok = not expected_year or not actual_year or expected_year == actual_year

    return year_ok and (slug_exact or title_similarity >= 0.92)


def format_date(result: dict) -> str:
    timestamp = result.get("first_release_date")
    if not isinstance(timestamp, int):
        return "unknown-date"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


def print_candidates(
    path: Path,
    entry: dict,
    year: str | None,
    results: list[dict],
    limit: int,
) -> None:
    title = str(entry.get("name") or path.stem)
    year_label = year or "unknown-year"

    print(f"{path}")
    print(f"  title: {title}")
    print(f"  year: {year_label}")

    if not results:
        print("  candidates: none")
        return

    for result in results[:limit]:
        result_title = result.get("name") or "Untitled"
        score = candidate_score(title, year, result)
        print(
            "  - "
            f"{igdb_url(result)} "
            f"| {result_title} "
            f"({format_date(result)}) "
            f"score={score:.2f}"
        )


def top_level_key(line: str) -> str | None:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", line)
    return match.group(1) if match else None


def block_end(lines: list[str], start_index: int) -> int:
    for index in range(start_index + 1, len(lines)):
        if top_level_key(lines[index]) is not None:
            return index
    return len(lines)


def insert_igdb(path: Path, url: str, *, overwrite: bool) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if top_level_key(line) == "igdb":
            if overwrite:
                lines[index] = f"igdb: {url}"
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return

    if not overwrite and any(top_level_key(line) == "igdb" for line in lines):
        return

    preferred_after = [
        "opencritic",
        "metacritic",
        "steam",
        "wikipedia",
        "genres",
        "released",
        "developers",
        "publishers",
        "description",
        "name",
    ]
    key_indexes = {
        key: index
        for index, line in enumerate(lines)
        if (key := top_level_key(line)) is not None
    }

    insert_index = len(lines)
    for key in preferred_after:
        if key in key_indexes:
            insert_index = block_end(lines, key_indexes[key])
            break

    lines.insert(insert_index, f"igdb: {url}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    load_dotenv(Path(".env"))
    client_id_env = (
        os.environ.get("IGDB_CLIENT_ID") or os.environ.get("TWITCH_CLIENT_ID") or ""
    )
    access_token_env = (
        os.environ.get("IGDB_ACCESS_TOKEN")
        or os.environ.get("TWITCH_ACCESS_TOKEN")
        or ""
    )

    parser = argparse.ArgumentParser(
        description="Search IGDB for game YAML entries missing igdb."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Game YAML file or directory to scan.",
    )
    parser.add_argument(
        "--client-id",
        default=client_id_env,
        help="IGDB/Twitch client ID. Defaults to IGDB_CLIENT_ID or TWITCH_CLIENT_ID.",
    )
    parser.add_argument(
        "--access-token",
        default=access_token_env,
        help="IGDB/Twitch access token. Defaults to IGDB_ACCESS_TOKEN or TWITCH_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--client-secret",
        default=(
            os.environ.get("IGDB_CLIENT_SECRET")
            or os.environ.get("TWITCH_CLIENT_SECRET")
            or ""
        ),
        help="IGDB/Twitch client secret for requesting an app access token.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum candidates to print per entry.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Search entries even when they already have igdb.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Insert the best high-confidence candidate URL into entries missing igdb.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="With --apply and --all, replace existing igdb values.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds.",
    )
    args = parser.parse_args()

    if args.limit < 1:
        print("error: --limit must be positive", file=sys.stderr)
        return 1

    had_error = False
    printed = False
    files: list[Path] = []
    for path in args.paths:
        if not path.exists():
            print(f"error: path does not exist: {path}", file=sys.stderr)
            had_error = True
            continue
        if path.is_file() and path.suffix.lower() not in YAML_SUFFIXES:
            print(f"error: not a YAML file: {path}", file=sys.stderr)
            had_error = True
            continue
        files.extend(yaml_files(path))

    token_provider = AccessTokenProvider(
        client_id=args.client_id,
        access_token=args.access_token,
        client_secret=args.client_secret,
        timeout=args.timeout,
    )

    for yaml_file in sorted(set(files)):
        if not is_game_entry(yaml_file):
            continue

        try:
            entry = read_entry(yaml_file)
            if entry.get("igdb") and not args.all:
                continue

            title = str(entry.get("name") or yaml_file.stem)
            year = release_year(entry) or path_year(yaml_file)
            results = ranked_candidates(
                title,
                year,
                search_games(
                    args.client_id,
                    token_provider.get(),
                    title,
                    limit=max(args.limit, 10),
                    timeout=args.timeout,
                )
                if len(title_variants(title)) == 1
                else search_game_variants(
                    args.client_id,
                    token_provider.get(),
                    title,
                    limit=max(args.limit, 10),
                    timeout=args.timeout,
                ),
            )
        except (
            OSError,
            ValueError,
            yaml.YAMLError,
            json.JSONDecodeError,
            HTTPError,
            URLError,
        ) as error:
            print(f"error: {yaml_file}: {error}", file=sys.stderr)
            had_error = True
            continue

        if printed:
            print()
        print_candidates(yaml_file, entry, year, results, args.limit)

        if args.apply and results and (not entry.get("igdb") or args.overwrite):
            result = results[0]
            if is_high_confidence(title, year, result):
                url = igdb_url(result)
                insert_igdb(yaml_file, url, overwrite=args.overwrite)
                print(f"  applied: {url}")
            else:
                print("  applied: skipped low-confidence match")
        printed = True

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
