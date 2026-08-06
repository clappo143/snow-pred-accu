"""Focused regression coverage for Mountainwatch's graph-backed collector."""
import datetime as dt
import json
import unittest
from pathlib import Path
from unittest import mock

from collectors import mountainwatch as mw
from collectors.common import TZ
from resorts import RESORTS


FIXTURE = Path(__file__).parent / "fixtures" / "mountainwatch_weather_graph.html"
ANCHOR = dt.date(2026, 8, 2)


def _clock(hour: int) -> str:
    period = "am" if hour < 12 else "pm"
    value = hour % 12 or 12
    return f"{value}{period}"


def _payload(snow: dict[dt.datetime, float] | None = None,
             invalid: set[dt.datetime] | None = None) -> dict:
    snow, invalid = snow or {}, invalid or set()
    start = dt.datetime(2026, 8, 2, 3, tzinfo=TZ)
    rows = []
    for i in range(56):
        block_start = start + dt.timedelta(hours=3 * i)
        block_end = block_start + dt.timedelta(hours=3)
        rows.append({
            "DateDisplay": (
                f"{block_start.strftime('%A')} {_clock(block_start.hour)} - "
                f"{_clock(block_end.hour)}"
            ),
            "Snow": snow.get(block_start, 0.0),
            "Valid": 0 if block_start in invalid else 1,
        })
    return {"mwgraphdatas": rows}


class _Response:
    def __init__(self, *, text: str | None = None, payload: dict | None = None):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class MountainwatchCollectorTests(unittest.TestCase):
    def setUp(self):
        self.html = FIXTURE.read_text()
        self.headers, self.visible = mw._graph_table(self.html)
        self.dates = mw._anchor_graph_dates(self.headers, ANCHOR)

    def test_scopes_abbreviated_headers_to_weather_graph(self):
        self.assertEqual(self.headers[0], ("Sun", 2))
        self.assertEqual(self.dates[0], ANCHOR)
        self.assertEqual(self.dates[-1], dt.date(2026, 8, 8))
        self.assertEqual(
            mw._forecastgraph_url(self.html),
            "https://www.mountainwatch.com/forecastgraph/Perisher.json",
        )
        with mock.patch.object(
            mw, "get", side_effect=[
                _Response(text=self.html), _Response(payload=_payload()),
            ],
        ), mock.patch.object(mw, "today", return_value=ANCHOR):
            result = mw.collect(RESORTS["perisher"])
        self.assertIn(ANCHOR, result)
        self.assertNotIn(dt.date(2026, 7, 31), result)

    def test_forecastgraph_url_accepts_space_named_endpoints(self):
        self.assertEqual(
            mw._forecastgraph_url(
                "jQuery.getJSON('https://www.mountainwatch.com/forecastgraph/Falls Creek.json')"
            ),
            "https://www.mountainwatch.com/forecastgraph/Falls%20Creek.json",
        )
        self.assertEqual(
            mw._forecastgraph_url(
                'jQuery.getJSON("https://mountainwatch.com/forecastgraph/Mount%20Buller.json")'
            ),
            "https://mountainwatch.com/forecastgraph/Mount%20Buller.json",
        )

    def test_forecastgraph_url_rejects_wrong_host_and_traversal(self):
        for candidate in (
            "https://example.test/forecastgraph/Perisher.json",
            "https://www.mountainwatch.com/forecastgraph/%2e%2e/secret.json",
        ):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, "no safe"):
                mw._forecastgraph_url(f"jQuery.getJSON('{candidate}')")

    def test_splits_six_to_nine_block_at_seven_am(self):
        sunday_six = dt.datetime(2026, 8, 2, 6, tzinfo=TZ)
        monday_six = dt.datetime(2026, 8, 3, 6, tzinfo=TZ)
        blocks = mw._blocks(_payload({sunday_six: 3.0, monday_six: 6.0}), self.dates)
        self.assertEqual(mw._windows_7am(blocks)[ANCHOR], 4.0)

    def test_invalid_block_prevents_incomplete_window(self):
        monday_six = dt.datetime(2026, 8, 3, 6, tzinfo=TZ)
        blocks = mw._blocks(_payload(invalid={monday_six}), self.dates)
        self.assertNotIn(ANCHOR, mw._windows_7am(blocks))

    def test_nonconsecutive_headers_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, "not consecutive"):
            mw._anchor_graph_dates(
                [("Sat", 1), ("Mon", 3), ("Tue", 4), ("Wed", 5),
                 ("Thu", 6), ("Fri", 7), ("Sat", 8)],
                ANCHOR,
            )


if __name__ == "__main__":
    unittest.main()
