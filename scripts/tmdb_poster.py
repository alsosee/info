#!/usr/bin/env python3
"""Download and resize TMDB posters for repository YAML entries.

Usage:
    TMDB_API_KEY=xxx MEDIA_DIR=../media python scripts/tmdb_poster.py "Movies/2000/Memento.yml"
    TMDB_API_KEY=xxx MEDIA_DIR=../media python scripts/tmdb_poster.py Movies/2000

The script prints nothing when the entry has no ``tmdb`` field. Otherwise it
downloads the original poster, resizes it to 800px max width while preserving
proportions, saves it under MEDIA_DIR using the same relative path as the YAML
file, and prints the saved image path.

When a directory is passed, the script scans it recursively for YAML files and
downloads posters for entries that do not already have a matching image under
MEDIA_DIR.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from PIL import Image


TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/original"
TMDB_ID_RE = re.compile(r"^(\d+)")
TMDB_FIELD_RE = re.compile(r"^tmdb:\s*(.*?)\s*(?:#.*)?$")
TOP_LEVEL_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*(?:#.*)?$")
MAX_WIDTH = 800
FORMAT_SUFFIXES = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
YAML_SUFFIXES = {".yml", ".yaml"}


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


def read_top_level_scalar(path: Path, key: str) -> str | None:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            match = TOP_LEVEL_FIELD_RE.match(line)
            if match and match.group(1) == key:
                value = match.group(2).strip().strip('"\'')
                return value or None
    return None


def read_tmdb_url(path: Path) -> str | None:
    """Read the simple, top-level scalar used by repository entries."""
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            match = TMDB_FIELD_RE.match(line)
            if match:
                value = match.group(1).strip('"\'')
                return value or None
    return None


def release_year(path: Path) -> str | None:
    released = read_top_level_scalar(path, "released")
    if released:
        match = re.match(r"^(\d{4})", released)
        if match:
            return match.group(1)

    for part in reversed(path.parts):
        if re.match(r"^\d{4}$", part):
            return part
    return None


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def tmdb_api_path(tmdb_url: str) -> str:
    parsed = urlparse(tmdb_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported TMDB URL: {tmdb_url}")
    if parsed.netloc not in {"themoviedb.org", "www.themoviedb.org"}:
        raise ValueError(f"unsupported TMDB URL: {tmdb_url}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] not in {"movie", "tv"}:
        raise ValueError(f"unsupported TMDB URL: {tmdb_url}")

    id_match = TMDB_ID_RE.match(parts[1])
    if not id_match:
        raise ValueError(f"unsupported TMDB URL: {tmdb_url}")

    media_type = parts[0]
    media_id = id_match.group(1)
    if media_type == "movie":
        return f"/movie/{media_id}"

    if len(parts) >= 4 and parts[2] == "season" and parts[3].isdigit():
        return f"/tv/{media_id}/season/{parts[3]}"
    return f"/tv/{media_id}"


def tmdb_data(path: Path, api_key: str, *, timeout: int) -> dict | None:
    tmdb_url = read_tmdb_url(path)
    if tmdb_url is None:
        return None

    if not api_key:
        raise ValueError("TMDB_API_KEY is not set")

    query = urlencode({"api_key": api_key})
    with urlopen(f"{TMDB_API}{tmdb_api_path(tmdb_url)}?{query}", timeout=timeout) as response:
        return json.load(response)


def validate_tmdb_data(path: Path, data: dict, *, strict: bool) -> None:
    expected_title = read_top_level_scalar(path, "name") or path.stem
    actual_title = data.get("title") or data.get("name") or ""
    expected_norm = normalize_title(expected_title)
    actual_norm = normalize_title(actual_title)

    if expected_norm and actual_norm and expected_norm != actual_norm:
        # Allow files named with a shorter franchise title to use a fuller TMDB title.
        if expected_norm not in actual_norm and actual_norm not in expected_norm:
            raise ValueError(
                f'TMDB title mismatch: expected "{expected_title}", got "{actual_title}"'
            )

    expected_year = release_year(path)
    actual_date = data.get("release_date") or data.get("first_air_date") or ""
    actual_year = actual_date[:4] if re.match(r"^\d{4}", actual_date) else None
    if strict and expected_year and actual_year and expected_year != actual_year:
        raise ValueError(
            f'TMDB year mismatch: expected "{expected_year}", got "{actual_year}"'
        )


def poster_url(path: Path, api_key: str, *, strict: bool, timeout: int) -> str | None:
    data = tmdb_data(path, api_key, timeout=timeout)
    if data is None:
        return None

    validate_tmdb_data(path, data, strict=strict)
    poster_path = data.get("poster_path")
    if not poster_path:
        raise ValueError("TMDB entry has no poster")

    return f"{TMDB_IMAGE}{poster_path}"


def output_stem(yaml_file: Path, info_dir: Path, media_dir: Path) -> Path:
    try:
        relative_path = yaml_file.resolve().relative_to(info_dir.resolve())
    except ValueError:
        raise ValueError(f"YAML file is outside info directory: {yaml_file}")

    suffix = relative_path.suffix
    if suffix:
        relative_path = relative_path.parent / relative_path.name[: -len(suffix)]
    return media_dir / relative_path


def image_path(output_without_suffix: Path, suffix: str) -> Path:
    return output_without_suffix.parent / f"{output_without_suffix.name}{suffix}"


def image_exists(output_without_suffix: Path) -> bool:
    return any(
        image_path(output_without_suffix, suffix).exists()
        for suffix in FORMAT_SUFFIXES.values()
    )


def remove_existing_images(output_without_suffix: Path, keep: Path) -> None:
    for suffix in FORMAT_SUFFIXES.values():
        path = image_path(output_without_suffix, suffix)
        if path != keep and path.exists():
            path.unlink()


def save_resized_image(url: str, output_without_suffix: Path, *, timeout: int) -> Path:
    with urlopen(url, timeout=timeout) as response:
        image_data = response.read()

    with Image.open(BytesIO(image_data)) as image:
        image_format = image.format or "JPEG"
        suffix = FORMAT_SUFFIXES.get(image_format.upper(), f".{image_format.lower()}")
        output_path = image_path(output_without_suffix, suffix)

        if image.width > MAX_WIDTH:
            height = round(image.height * MAX_WIDTH / image.width)
            image = image.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs = {}
        if image_format.upper() == "JPEG":
            image = image.convert("RGB")
            save_kwargs = {"quality": 90, "optimize": True}
        image.save(output_path, format=image_format, **save_kwargs)
        remove_existing_images(output_without_suffix, output_path)

    return output_path


def process_yaml_file(
    yaml_file: Path,
    api_key: str,
    info_dir: Path,
    media_dir: Path | None,
    *,
    overwrite: bool,
    dry_run: bool,
    strict: bool,
    timeout: int,
) -> Path | None:
    if read_tmdb_url(yaml_file) is None:
        return None
    if media_dir is None:
        raise ValueError("MEDIA_DIR is not set")

    output_without_suffix = output_stem(yaml_file, info_dir, media_dir)
    if not overwrite and image_exists(output_without_suffix):
        return None

    url = poster_url(yaml_file, api_key, strict=strict, timeout=timeout)
    if url is None:
        return None

    if dry_run:
        return image_path(output_without_suffix, Path(urlparse(url).path).suffix or ".jpg")

    return save_resized_image(url, output_without_suffix, timeout=timeout)


def yaml_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        child
        for child in path.rglob("*")
        if child.is_file() and child.suffix.lower() in YAML_SUFFIXES
    )


def main() -> int:
    load_dotenv(Path(".env"))
    media_dir_env = os.environ.get("MEDIA_DIR")

    parser = argparse.ArgumentParser(
        description="Download and resize TMDB posters for YAML entries."
    )
    parser.add_argument("path", type=Path, help="YAML file or directory to scan.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TMDB_API_KEY", ""),
        help="TMDB API key. Defaults to TMDB_API_KEY.",
    )
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=Path(media_dir_env) if media_dir_env else None,
        help="Media output directory. Defaults to MEDIA_DIR.",
    )
    parser.add_argument(
        "--info-dir",
        type=Path,
        default=Path.cwd(),
        help="Info repository root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing matching image files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate TMDB metadata and print output paths without downloading.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when TMDB release year differs from the YAML/path year.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    args = parser.parse_args()

    if args.path.is_dir() and args.media_dir is None:
        print("error: MEDIA_DIR is not set", file=sys.stderr)
        return 1
    if not args.path.exists():
        print(f"error: path does not exist: {args.path}", file=sys.stderr)
        return 1
    if args.path.is_file() and args.path.suffix.lower() not in YAML_SUFFIXES:
        print(f"error: not a YAML file: {args.path}", file=sys.stderr)
        return 1

    had_error = False
    try:
        files = yaml_files(args.path)
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for yaml_file in files:
        try:
            output_path = process_yaml_file(
                yaml_file,
                args.api_key,
                args.info_dir,
                args.media_dir,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                strict=args.strict,
                timeout=args.timeout,
            )
        except (OSError, ValueError, json.JSONDecodeError, HTTPError, URLError) as error:
            print(f"error: {yaml_file}: {error}", file=sys.stderr)
            had_error = True
            continue

        if output_path:
            print(output_path)

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
