"""Mountainwatch's weather graph, expressed as 7am-to-7am snow windows.

The page's visible daily snow row is a midnight-to-midnight summary.  The
resort reports used for scoring cover 7am-to-7am, so this collector reads the
weather graph's underlying three-hour JSON instead.  Snow within each
three-hour source block is apportioned uniformly: in particular, one third of
the 6am-9am value belongs before 7am and two thirds after it.  This is a
neutral assumption; Mountainwatch does not publish a finer-grained split.

The graph table is deliberately parsed as a scoped HTML structure.  Narrative
article dates must never be allowed to anchor graph values.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from resorts import Resort

from .common import TZ, get, today

SOURCE = "mountainwatch"
URL = "https://www.mountainwatch.com/australia/{slug}/weather/"
_GRAPH_HOSTS = frozenset({"www.mountainwatch.com", "mountainwatch.com"})
_GRAPH_PATH = re.compile(r"^/forecastgraph/([A-Za-z0-9 _-]+)\.json$")
_GET_JSON_CALL = re.compile(
    r"jQuery\.getJSON\(\s*(?P<quote>['\"])(?P<url>.*?)(?P=quote)", re.DOTALL,
)
_SHORT_DAY = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})$")
_SNOW_CELL = re.compile(r"^Snow:\s*([0-9]+(?:\.[0-9]+)?)\s*cm$")
_DATE_DISPLAY = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"((?:12|[1-9])(?:am|pm))\s*-\s*((?:12|[1-9])(?:am|pm))$"
)
_SHORT_TO_WEEKDAY = {day: i for i, day in enumerate(
    ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
)}


@dataclass(frozen=True)
class _Block:
    start: dt.datetime
    snow_cm: float
    valid: bool


class _GraphTableParser(HTMLParser):
    """Collect cells only from the weather graph's seven-day table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_graph_table = False
        self._scope_depth = 0
        self._row: list[tuple[str, str]] | None = None
        self._cell_class = ""
        self._cell_text: list[str] | None = None
        self.rows: list[list[tuple[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        # Mountainwatch has used this id on both a wrapping div and its table.
        if attrs_dict.get("id") == "sevenDayForecast":
            self._in_graph_table = True
            self._scope_depth = 1
            return
        if not self._in_graph_table:
            return
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}:
            self._scope_depth += 1
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_class = attrs_dict.get("class") or ""
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_graph_table:
            return
        if tag in {"td", "th"} and self._cell_text is not None and self._row is not None:
            text = " ".join("".join(self._cell_text).split())
            self._row.append((self._cell_class, text))
            self._cell_text = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}:
            self._scope_depth -= 1
        if self._scope_depth == 0:
            self._in_graph_table = False

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)


def _graph_table(html: str) -> tuple[list[tuple[str, int]], list[float]]:
    parser = _GraphTableParser()
    parser.feed(html)
    parser.close()

    headers: list[tuple[str, int]] | None = None
    snow_values: list[float] | None = None
    for row in parser.rows:
        labels = [_SHORT_DAY.fullmatch(text) for _class, text in row]
        if len(row) == 7 and all(labels):
            headers = [(m.group(1), int(m.group(2))) for m in labels if m]
        if row and all("forecast-chart-snow" in css for css, _text in row):
            values = [_SNOW_CELL.fullmatch(text) for _css, text in row]
            if all(values):
                snow_values = [float(m.group(1)) for m in values if m]

    if headers is None:
        raise ValueError("weather graph has no seven abbreviated date headers")
    if snow_values is None or len(snow_values) != len(headers):
        raise ValueError("weather graph snow row does not match its date headers")
    return headers, snow_values


def _forecastgraph_url(html: str) -> str:
    """Return Mountainwatch's embedded graph endpoint in request-safe form."""
    for match in _GET_JSON_CALL.finditer(html):
        candidate = match.group("url")
        try:
            parsed = urlsplit(candidate)
            decoded_path = unquote(parsed.path, encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        if (
            parsed.scheme == "https"
            and parsed.hostname in _GRAPH_HOSTS
            and parsed.port is None
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and _GRAPH_PATH.fullmatch(decoded_path)
        ):
            return urlunsplit(("https", parsed.hostname, quote(decoded_path, safe="/"), "", ""))
    raise ValueError("no safe forecastgraph JSON URL found in weather graph page")


def _anchor_graph_dates(
    headers: list[tuple[str, int]], collected_on: dt.date,
) -> list[dt.date]:
    """Resolve graph header days near collection time and verify each rollover."""
    if len(headers) != 7:
        raise ValueError(f"expected seven graph dates, found {len(headers)}")
    if any(day not in _SHORT_TO_WEEKDAY or not 1 <= dom <= 31
           for day, dom in headers):
        raise ValueError("weather graph has an invalid abbreviated date header")
    first_day, first_dom = headers[0]
    candidates = [
        collected_on + dt.timedelta(days=offset)
        for offset in range(-10, 11)
        if (collected_on + dt.timedelta(days=offset)).day == first_dom
        and (collected_on + dt.timedelta(days=offset)).weekday() == _SHORT_TO_WEEKDAY[first_day]
    ]
    if len(candidates) != 1:
        raise ValueError(
            "graph header date is implausible for collection date "
            f"{collected_on}: {first_day} {first_dom:02d}"
        )
    dates = [candidates[0] + dt.timedelta(days=i) for i in range(7)]
    for date, (short_day, dom) in zip(dates, headers):
        if date.day != dom or date.weekday() != _SHORT_TO_WEEKDAY[short_day]:
            raise ValueError("weather graph headers are not consecutive dates")
    return dates


def _hour(clock: str) -> int:
    number, period = int(clock[:-2]), clock[-2:]
    return (number % 12) + (12 if period == "pm" else 0)


def _blocks(payload: object, graph_dates: list[dt.date]) -> list[_Block]:
    if not isinstance(payload, dict) or not isinstance(payload.get("mwgraphdatas"), list):
        raise ValueError("forecastgraph JSON has no mwgraphdatas array")
    rows = payload["mwgraphdatas"]
    if len(rows) != 56:
        raise ValueError(f"forecastgraph JSON expected 56 three-hour blocks, found {len(rows)}")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("forecastgraph JSON contains a non-object block")

    first_display = rows[0].get("DateDisplay")
    first_match = _DATE_DISPLAY.fullmatch(first_display) if isinstance(first_display, str) else None
    if not first_match:
        raise ValueError("forecastgraph JSON has an invalid first DateDisplay")
    first_name = first_match.group(1)
    if first_name != graph_dates[0].strftime("%A"):
        raise ValueError("forecastgraph first weekday does not align with scoped graph headers")

    expected = dt.datetime.combine(
        graph_dates[0], dt.time(_hour(first_match.group(2))), tzinfo=TZ,
    )
    blocks: list[_Block] = []
    for index, row in enumerate(rows):
        display = row.get("DateDisplay")
        match = _DATE_DISPLAY.fullmatch(display) if isinstance(display, str) else None
        if not match:
            raise ValueError(f"forecastgraph block {index} has invalid DateDisplay")
        start_hour, end_hour = _hour(match.group(2)), _hour(match.group(3))
        if (end_hour - start_hour) % 24 != 3:
            raise ValueError(f"forecastgraph block {index} is not three hours long")
        if match.group(1) != expected.strftime("%A") or start_hour != expected.hour:
            raise ValueError("forecastgraph blocks are not a contiguous dated sequence")
        try:
            snow_cm = float(row["Snow"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"forecastgraph block {index} has invalid Snow") from exc
        if not math.isfinite(snow_cm) or snow_cm < 0 or "Valid" not in row:
            raise ValueError(f"forecastgraph block {index} has invalid snow/validity data")
        blocks.append(_Block(expected, snow_cm, row["Valid"] in (True, 1, "1")))
        expected += dt.timedelta(hours=3)
    return blocks


def _calendar_sanity(
    blocks: list[_Block], graph_dates: list[dt.date], visible: list[float],
) -> None:
    """Check full calendar days against the table, allowing table rounding."""
    for date, visible_cm in zip(graph_dates, visible):
        start = dt.datetime.combine(date, dt.time(), tzinfo=TZ)
        end = start + dt.timedelta(days=1)
        day_blocks = [b for b in blocks if start <= b.start < end]
        if len(day_blocks) != 8 or not all(b.valid for b in day_blocks):
            continue
        graph_total = sum(b.snow_cm for b in day_blocks)
        if abs(graph_total - visible_cm) > 0.25:
            raise ValueError(
                f"weather graph table disagrees with JSON on {date}: "
                f"table {visible_cm}cm vs JSON {graph_total}cm"
            )


def _windows_7am(blocks: list[_Block]) -> dict[dt.date, float]:
    """Aggregate only completely-covered local 7am-to-7am windows."""
    if not blocks:
        return {}
    out: dict[dt.date, float] = {}
    first_day = min(block.start.date() for block in blocks) - dt.timedelta(days=1)
    last_day = max(block.start.date() for block in blocks)
    day = first_day
    while day <= last_day:
        start = dt.datetime.combine(day, dt.time(7), tzinfo=TZ)
        end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(7), tzinfo=TZ)
        covered = dt.timedelta()
        total = 0.0
        for block in blocks:
            block_end = block.start + dt.timedelta(hours=3)
            overlap_start, overlap_end = max(start, block.start), min(end, block_end)
            if overlap_end <= overlap_start or not block.valid:
                continue
            overlap = overlap_end - overlap_start
            covered += overlap
            total += block.snow_cm * (overlap / dt.timedelta(hours=3))
        if covered == end - start:
            out[day] = round(total, 2)
        day += dt.timedelta(days=1)
    return out


def collect(resort: Resort) -> dict[dt.date, float]:
    html = get(URL.format(slug=resort.mountainwatch_slug)).text
    headers, visible_snow = _graph_table(html)
    graph_dates = _anchor_graph_dates(headers, today())
    payload = get(_forecastgraph_url(html)).json()
    blocks = _blocks(payload, graph_dates)
    _calendar_sanity(blocks, graph_dates, visible_snow)
    out = _windows_7am(blocks)
    if not out:
        raise ValueError("no complete valid 7am-to-7am window in forecastgraph JSON")
    return out
