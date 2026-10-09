"""Small JSON cache on disk so the same URL is not downloaded twice."""
import hashlib
import json
import os
import re
import time
from typing import Callable

import requests

from .config import SETTINGS

_session = requests.Session()
_session.headers["User-Agent"] = "nba-totals-analyzer/0.1"


def _path_for(url: str, cache_dir: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[-1])[-80:]
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    return os.path.join(cache_dir, f"{slug}_{digest}.json")


def get_json(url: str, ttl: int | None = None, cache_dir: str | None = None,
             transform: Callable[[dict], dict] | None = None) -> dict:
    """Return JSON for url, from cache if present.

    ttl=None means the response never expires (finished games, past seasons).
    transform, if given, shrinks the response before it is stored.
    """
    cache_dir = cache_dir or SETTINGS.cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    path = _path_for(url, cache_dir)
    if os.path.exists(path) and (ttl is None or time.time() - os.path.getmtime(path) < ttl):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    for attempt in range(4):
        try:
            resp = _session.get(url, timeout=20)
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    data = resp.json()
    if transform:
        data = transform(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return data
