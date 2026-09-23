#!/usr/bin/env python3
"""Normalize known entity aliases and slash-bearing company paths."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

ALIASES = {
    "FOX": "Fox",
    "20th Century-Fox": "20th Century Fox",
    "Ubi Soft": "Ubisoft",
    "Stellan Skarsgard": "Stellan Skarsgård",
}

COMPANY_PATHS = {
    "1C/Cenega": "1C-Cenega",
    "Adelstein/Parouse Productions": "Adelstein-Parouse Productions",
    "Bill/Phillips Productions": "Bill-Phillips Productions",
    "Bórd Scannán na hÉireann/Irish Film Board": "Bórd Scannán na hÉireann-Irish Film Board",
    "Craven/Maddalena Films": "Craven-Maddalena Films",
    "Cruise/Wagner Productions": "Cruise-Wagner Productions",
    "Don Simpson/Jerry Bruckheimer Films": "Don Simpson-Jerry Bruckheimer Films",
    "Emmett/Furla/Oasis Films": "Emmett-Furla-Oasis Films",
    "Gold/Miller Productions": "Gold-Miller Productions",
    "Green/Epstein Productions": "Green-Epstein Productions",
    "Hear/Say Productions": "Hear-Say Productions",
    "Jinks/Cohen Company": "Jinks-Cohen Company",
    "K/O Paper Products": "K-O Paper Products",
    "Lego System A/S": "Lego System A-S",
    "MGM/UA Distribution Co.": "MGM-UA Distribution Co.",
    "MGM/UA Entertainment Co.": "MGM-UA Entertainment Co.",
    "MacDonald/Parkes Productions": "Parkes-MacDonald Productions",
    "Michaels/Goldwyn": "Michaels-Goldwyn",
    "NB/GG Pictures": "NB-GG Pictures",
    "Neufeld/Rehme Productions": "Neufeld-Rehme Productions",
    "PDI/DreamWorks": "PDI-DreamWorks",
    "Parkes/MacDonald Productions": "Parkes-MacDonald Productions",
    "Rocklin/Faust Productions": "Rocklin-Faust Productions",
    "Roth/Arnold Productions": "Roth-Arnold Productions",
    "Roth/Kirschenbaum Films": "Roth-Kirschenbaum Films",
    "Steve Roth/Oak Productions": "Steve Roth-Oak Productions",
    "The Affleck/Middleton Project": "The Affleck-Middleton Project",
    "The Kennedy/Marshall Company": "The Kennedy-Marshall Company",
    "Tolkin/Ruddy Productions": "Tolkin-Ruddy Productions",
    "Unison/Paladin": "Unison-Paladin",
    "WDR/Arte": "WDR-Arte",
    "Witt/Thomas Productions": "Witt-Thomas Productions",
    "ZA/UM": "ZA-UM",
    "ZDF/ARTE": "ZDF-Arte",
    "ZDF/Arte": "ZDF-Arte",
    "Zadan/Meron Production": "Zadan-Meron Production",
    "Zanuck/Brown Company": "Zanuck-Brown Company",
    "Zide/Perry Productions": "Zide-Perry Productions",
    "Zucker/Abrahams/Zucker Productions": "Zucker-Abrahams-Zucker Productions",
}

SPLIT_COMPANIES = {
    "Fox / National Geographic Channel": ["Fox", "National Geographic Channel"],
    "Netflix / PBS Kids": ["Netflix", "PBS Kids"],
}

DISPLAY_NAMES = {
    "Parkes-MacDonald Productions": "Parkes/MacDonald Productions",
    "ZDF-Arte": "ZDF/Arte",
}

SCALAR_LINE = re.compile(
    r"^(?P<prefix>\s*(?:-\s+|[A-Za-z_][A-Za-z0-9_]*:\s+))"
    r"(?P<value>.*?)(?P<newline>\r?\n)?$"
)


def scalar(value: str) -> str:
    if (
        re.fullmatch(r"[A-Za-z0-9À-ž][A-Za-z0-9À-ž .,&+'!()-]*", value)
        and ": " not in value
        and value.lower() not in {"null", "true", "false", "yes", "no"}
    ):
        return value
    return json.dumps(value, ensure_ascii=False)


def tracked_yaml() -> list[Path]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "*.yml"], cwd=ROOT
    )
    return [ROOT / item.decode() for item in raw.split(b"\0") if item]


def replace_scalars(
    text: str,
    replacements: dict[str, str],
    *,
    preserve_name: bool = False,
) -> tuple[str, int]:
    result: list[str] = []
    changed = 0
    for line in text.splitlines(keepends=True):
        match = SCALAR_LINE.match(line)
        if not match:
            result.append(line)
            continue
        if preserve_name and match.group("prefix").lstrip().startswith("name:"):
            result.append(line)
            continue
        raw_value = match.group("value")
        try:
            parsed = yaml.safe_load(raw_value)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, str) and parsed in replacements:
            result.append(
                match.group("prefix")
                + scalar(replacements[parsed])
                + (match.group("newline") or "")
            )
            changed += 1
        else:
            result.append(line)
    return "".join(result), changed


def split_company_scalars(text: str) -> tuple[str, int]:
    result: list[str] = []
    changed = 0
    for line in text.splitlines(keepends=True):
        match = re.match(
            r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*):\s+"
            r"(?P<value>.*?)(?P<newline>\r?\n)?$",
            line,
        )
        if not match:
            result.append(line)
            continue
        try:
            parsed = yaml.safe_load(match.group("value"))
        except yaml.YAMLError:
            parsed = None
        if not isinstance(parsed, str) or parsed not in SPLIT_COMPANIES:
            result.append(line)
            continue
        indent = match.group("indent")
        result.append(f"{indent}{match.group('key')}:\n")
        result.extend(
            f"{indent}  - {scalar(value)}\n" for value in SPLIT_COMPANIES[parsed]
        )
        changed += 1
    return "".join(result), changed


def company_files() -> dict[Path, str]:
    result: dict[Path, str] = {}
    for original, canonical in COMPANY_PATHS.items():
        display = DISPLAY_NAMES.get(canonical, original)
        result[ROOT / "Companies" / f"{canonical}.yml"] = display
    for name in ("National Geographic Channel", "PBS Kids"):
        result[ROOT / "Companies" / f"{name}.yml"] = name
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    replacements = ALIASES | COMPANY_PATHS
    edits: dict[Path, str] = {}
    replacement_count = 0
    split_count = 0
    for path in tracked_yaml():
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        updated, count = replace_scalars(
            text,
            replacements,
            preserve_name=path.parent == ROOT / "Companies",
        )
        updated, splits = split_company_scalars(updated)
        if updated != text:
            edits[path] = updated
            replacement_count += count
            split_count += splits

    companies = {
        path: name for path, name in company_files().items() if not path.exists()
    }
    print(f"reference replacements: {replacement_count}")
    print(f"composite company fields split: {split_count}")
    print(f"files edited: {len(edits)}")
    print(f"company records created: {len(companies)}")
    if not args.write:
        return 0

    for path, text in edits.items():
        yaml.safe_load(text)
        path.write_text(text, encoding="utf-8")
    for path, name in companies.items():
        path.write_text(f"name: {scalar(name)}\n", encoding="utf-8")
        yaml.safe_load(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
