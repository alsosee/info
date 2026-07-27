#!/usr/bin/env python3
"""Download and resize IGDB cover art for repository game YAML entries.

Usage:
    IGDB_CLIENT_ID=xxx IGDB_CLIENT_SECRET=yyy MEDIA_DIR=../media python scripts/igdb_cover.py "Games/Video/2021/Returnal.yml"
    IGDB_CLIENT_ID=xxx IGDB_ACCESS_TOKEN=yyy MEDIA_DIR=../media python scripts/igdb_cover.py Games/Video

The script prints nothing when the entry has no ``igdb`` field. Otherwise it
looks up the IGDB game by ID or slug, downloads the cover image, resizes it to
800px max width while preserving proportions, saves it under MEDIA_DIR using
the same relative path as the YAML file, and prints the saved image path.

When a directory is passed, the script scans it recursively for YAML files and
downloads covers for entries that do not already have a matching image under
MEDIA_DIR.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse

from PIL import Image


IGDB_API = "https://api.igdb.com/v4"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_IMAGE = "https://images.igdb.com/igdb/image/upload"
IGDB_FIELD_RE = re.compile(r"^igdb:\s*(.*?)\s*(?:#.*)?$")
TOP_LEVEL_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*(?:#.*)?$")
MAX_WIDTH = 800
FORMAT_SUFFIXES = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
YAML_SUFFIXES = {".yml", ".yaml"}
USER_AGENT = "alsosee-info-igdb-cover/1.0"


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


def fetch_url(url: str, *, timeout: int) -> bytes:
    return curl([url], timeout=timeout)


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


def read_top_level_scalar(path: Path, key: str) -> str | None:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            match = TOP_LEVEL_FIELD_RE.match(line)
            if match and match.group(1) == key:
                value = match.group(2).strip().strip('"\'')
                return value or None
    return None


def read_igdb_value(path: Path) -> str | None:
    """Read the simple, top-level scalar used by repository entries."""
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            match = IGDB_FIELD_RE.match(line)
            if match:
                value = match.group(1).strip().strip('"\'')
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
    value = expand_roman_numbers(expand_roman_ranges(value))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


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


def expand_roman_ranges(value: str) -> str:
    pattern = re.compile(r"\b([IVXLCDM]+)\s*[-\u2010-\u2015]\s*([IVXLCDM]+)\b")

    def replacement(match: re.Match[str]) -> str:
        start = ROMAN_NUMERALS.get(match.group(1))
        end = ROMAN_NUMERALS.get(match.group(2))
        if start is None or end is None or start >= end:
            return match.group(0)
        return " ".join(NUMBER_ROMANS[number] for number in range(start, end + 1))

    return pattern.sub(replacement, value.replace("•", " "))


def igdb_lookup_clause(igdb_value: str) -> str:
    value = igdb_value.strip()
    if value.isdigit():
        return f"where id = {value};"

    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.netloc not in {"igdb.com", "www.igdb.com"}:
            raise ValueError(f"unsupported IGDB URL: {igdb_value}")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "games":
            slug = parts[1]
        else:
            raise ValueError(f"unsupported IGDB URL: {igdb_value}")
    else:
        slug = value

    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        raise ValueError(f"unsupported IGDB slug: {slug}")
    return f'where slug = "{slug}";'


def twitch_access_token(client_id: str, client_secret: str, *, timeout: int) -> str:
    if not client_id:
        raise ValueError("IGDB_CLIENT_ID is not set")
    if not client_secret:
        raise ValueError("IGDB_ACCESS_TOKEN or IGDB_CLIENT_SECRET is not set")

    token_data = post_json(
        TWITCH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            }
        ),
        timeout=timeout,
    )
    if not isinstance(token_data, dict) or not token_data.get("access_token"):
        raise ValueError("Twitch token response did not include access_token")
    return str(token_data["access_token"])


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
        if not self.access_token:
            self.access_token = twitch_access_token(
                self.client_id,
                self.client_secret,
                timeout=self.timeout,
            )
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


def igdb_data(path: Path, client_id: str, access_token: str, *, timeout: int) -> dict | None:
    igdb_value = read_igdb_value(path)
    if igdb_value is None:
        return None

    query = (
        "fields id,name,slug,first_release_date,cover.image_id;"
        f" {igdb_lookup_clause(igdb_value)}"
        " limit 1;"
    )
    data = post_json(
        f"{IGDB_API}/games",
        headers=igdb_headers(client_id, access_token),
        data=query,
        timeout=timeout,
    )
    if not isinstance(data, list):
        raise ValueError("IGDB games response was not a list")
    if not data:
        raise ValueError("IGDB entry not found")
    if not isinstance(data[0], dict):
        raise ValueError("IGDB game response was not an object")
    return data[0]


def validate_igdb_data(path: Path, data: dict, *, strict: bool) -> None:
    expected_title = read_top_level_scalar(path, "name") or path.stem
    actual_title = data.get("name") or ""
    expected_norm = normalize_title(expected_title)
    actual_norm = normalize_title(actual_title)

    if expected_norm and actual_norm and expected_norm != actual_norm:
        if expected_norm.endswith(" 2") and actual_norm == expected_norm[:-2]:
            pass
        elif expected_norm.replace(" classic trilogy ", " trilogy ") == actual_norm:
            pass
        elif expected_norm == "command conquer yuri s revenge" and actual_norm == "command conquer red alert 2 yuri s revenge":
            pass
        elif expected_norm == "the settlers 4" and actual_norm == "the settlers fourth edition":
            pass
        elif expected_norm == "disney s math quest with aladdin" and actual_norm == "disney learning math quest with aladdin":
            pass
        elif expected_norm == "broken sword the shadow of the templars" and actual_norm == "circle of blood":
            pass
        elif expected_norm == "the settlers" and actual_norm == "serf city life is feudal":
            pass
        elif expected_norm == "jurassic park 2 the chaos continues" and actual_norm == "jurassic park part 2 the chaos continues":
            pass
        elif expected_norm == "street fighter 2 the world warrior" and actual_norm == "street fighter 2":
            pass
        elif expected_norm.replace(" ", "") == actual_norm.replace(" ", ""):
            pass
        elif sorted(expected_norm.split()) == sorted(actual_norm.split()):
            pass
        elif expected_norm not in actual_norm and actual_norm not in expected_norm:
            raise ValueError(
                f'IGDB title mismatch: expected "{expected_title}", got "{actual_title}"'
            )

    expected_year = release_year(path)
    timestamp = data.get("first_release_date")
    if strict and expected_year and isinstance(timestamp, int):
        actual_year = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y")
        if expected_year != actual_year:
            if (
                expected_year == "1996"
                and actual_year == "1995"
                and expected_norm
                in {
                    "warcraft 2 beyond the dark portal",
                    "worms reinforcements",
                    "the addams family pugsley s scavenger hunt",
                }
            ):
                return
            if (
                expected_year == "1994"
                and actual_year == "1995"
                and expected_norm == "jurassic park 2 the chaos continues"
            ):
                return
            raise ValueError(
                f'IGDB year mismatch: expected "{expected_year}", got "{actual_year}"'
            )


def cover_url(
    path: Path,
    client_id: str,
    access_token: str,
    *,
    strict: bool,
    size: str,
    timeout: int,
) -> str | None:
    data = igdb_data(path, client_id, access_token, timeout=timeout)
    if data is None:
        return None

    validate_igdb_data(path, data, strict=strict)
    cover = data.get("cover")
    if not isinstance(cover, dict) or not cover.get("image_id"):
        raise ValueError("IGDB entry has no cover image")

    return f'{IGDB_IMAGE}/{size}/{cover["image_id"]}.jpg'


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
    image_data = fetch_url(url, timeout=timeout)

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
    client_id: str,
    token_provider: AccessTokenProvider,
    info_dir: Path,
    media_dir: Path | None,
    *,
    overwrite: bool,
    dry_run: bool,
    strict: bool,
    size: str,
    timeout: int,
) -> Path | None:
    if read_igdb_value(yaml_file) is None:
        return None
    if media_dir is None:
        raise ValueError("MEDIA_DIR is not set")

    output_without_suffix = output_stem(yaml_file, info_dir, media_dir)
    if not overwrite and image_exists(output_without_suffix):
        return None

    url = cover_url(
        yaml_file,
        client_id,
        token_provider.get(),
        strict=strict,
        size=size,
        timeout=timeout,
    )
    if url is None:
        return None

    if dry_run:
        return image_path(
            output_without_suffix,
            Path(urlparse(url).path).suffix or ".jpg",
        )

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
    client_id_env = (
        os.environ.get("IGDB_CLIENT_ID") or os.environ.get("TWITCH_CLIENT_ID") or ""
    )
    access_token_env = (
        os.environ.get("IGDB_ACCESS_TOKEN")
        or os.environ.get("TWITCH_ACCESS_TOKEN")
        or ""
    )

    parser = argparse.ArgumentParser(
        description="Download and resize IGDB cover art for game YAML entries."
    )
    parser.add_argument("path", type=Path, help="YAML file or directory to scan.")
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
        help="Validate IGDB metadata and print output paths without downloading images.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when IGDB release year differs from the YAML/path year.",
    )
    parser.add_argument(
        "--size",
        default="t_1080p",
        help="IGDB image size. Defaults to t_1080p.",
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

    token_provider = AccessTokenProvider(
        client_id=args.client_id,
        access_token=args.access_token,
        client_secret=args.client_secret,
        timeout=args.timeout,
    )

    for yaml_file in files:
        try:
            output_path = process_yaml_file(
                yaml_file,
                args.client_id,
                token_provider,
                args.info_dir,
                args.media_dir,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                strict=args.strict,
                size=args.size,
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
