#!/usr/bin/env python3
"""Purge cached responses for source URLs listed in _redirects."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path


API = "https://api.cloudflare.com/client/v4"
SITE_HOST = "alsosee.info"
BATCH_SIZE = 30


def request(url: str, token: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    method = "POST" if body is not None else "GET"
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as response:
        result = json.load(response)
    if not result.get("success"):
        raise RuntimeError(result.get("errors") or "Cloudflare API request failed")
    return result


def redirect_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        source = line.split()[0]
        if "*" in source:
            continue
        quoted = urllib.parse.quote(urllib.parse.unquote(source), safe="/%:@&+$,;=-_.!~*'()")
        urls.append(f"https://{SITE_HOST}{quoted}")
    return urls


def main() -> None:
    token = os.environ["CF_API_TOKEN"]
    query = urllib.parse.urlencode({"name": SITE_HOST})
    zones = request(f"{API}/zones?{query}", token)["result"]
    if len(zones) != 1:
        raise RuntimeError(f"expected one zone for {SITE_HOST}, found {len(zones)}")

    urls = redirect_urls(Path("_redirects"))
    for index in range(0, len(urls), BATCH_SIZE):
        request(
            f"{API}/zones/{zones[0]['id']}/purge_cache",
            token,
            {"files": urls[index : index + BATCH_SIZE]},
        )
    print(f"Purged {len(urls)} redirect source URLs")


if __name__ == "__main__":
    main()
