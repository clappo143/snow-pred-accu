"""Ground truth: resort-reported 24h snowfall via OnTheSnow's embedded JSON."""
from __future__ import annotations

import json
import re

from .common import get

SOURCE = "onthesnow"
URL = "https://www.onthesnow.com/australia/perisher/skireport"


def collect() -> float:
    """Reported snowfall (cm) in the last 24 hours."""
    html = get(URL).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ not found")
    data = json.loads(m.group(1))
    return float(data["props"]["pageProps"]["fullResort"]["snow"]["last24"])
