import unittest
from unittest.mock import patch

from collectors import actuals_official as official


class Response:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class OfficialParserTests(unittest.TestCase):
    def test_perisher_xml(self):
        xml = ("<snowreport><date> 12/07/2026 7:25 </date>"
               "<new_snow_24hrs_top>15</new_snow_24hrs_top>"
               "<new_snow_7days>15</new_snow_7days>"
               "<snowdepth>34.1</snowdepth></snowreport>")
        with patch.object(official, "get", return_value=Response(text=xml)):
            result = official._collect_perisher_xml("https://example/perisher.xml")
        self.assertEqual((result["snow_24h"], result["natural_depth"]), (15, 34.1))
        self.assertEqual(result["reported_at"], "2026-07-12T07:25:00+10:00")

    def test_hotham_xml_keeps_seconds(self):
        xml = ("<SnowReport><_LastUpdated>2026-07-12T07:33:19</_LastUpdated>"
               "<TwentyFourHourSnowfall>17</TwentyFourHourSnowfall>"
               "<SevenDaySnowfall>17</SevenDaySnowfall>"
               "<CurrentSnowdepth>45</CurrentSnowdepth></SnowReport>")
        with patch.object(official, "get", return_value=Response(text=xml)):
            result = official._collect_hotham_xml("https://example/hotham.xml")
        self.assertEqual(result["reported_at"], "2026-07-12T07:33:19+10:00")

    def test_buller_uses_patrol_stamp_not_widget_refresh(self):
        feed = {
            "last_updated": "2026-07-12T19:30:00+10:00",
            "snow_report": {"snow_last_24_hours": 14, "average_natural": 26},
        }
        page = """<h2>Ski Patrol update</h2><p>
          Sunday 12th July 7:15am: Fresh snow overnight.</p>"""
        with patch.object(official, "get", side_effect=[
                Response(payload=feed), Response(text=page)]):
            result = official._collect_buller_json("https://api.example/widget")
        self.assertEqual(result["reported_at"], "2026-07-12T07:15:00+10:00")
        self.assertEqual(result["report_time_kind"], "documented_measurement")
        self.assertEqual(result["widget_updated_at"], feed["last_updated"])

    def test_falls_timestamp_is_patrol_observation(self):
        payload = {"Patrol": {"PatrolDate": "12 July 2026",
                   "PatrolTime": "6:15 AM", "PatrolFreshSnow": "14",
                   "PatrolNaturalSnowDepth": "43"}}
        with patch.object(official, "get", return_value=Response(payload=payload)):
            result = official._collect_falls_json("https://example/falls.json")
        self.assertEqual(result["report_time_kind"], "patrol_observation")
        self.assertEqual(result["snow_24h"], 14)

    def test_thredbo_prefers_narrative_report_publication_time(self):
        xml = ('<snowReport updated="2026-07-12T06:46:19.000+10:00">'
               '<snow24Hours amount="10"/><snow7Days amount="0"/>'
               '<avgsnowdepth amount="35.4"/></snowReport>')
        page = ('<div class="report-date"><span>'
                '12 Jul 2026, 06:30 AM</span></div>')
        with patch.object(official, "get", side_effect=[
                Response(text=xml), Response(text=page)]):
            result = official._collect_thredbo_xml("https://example/snow.xml")
        self.assertEqual(result["reported_at"], "2026-07-12T06:30:00+10:00")
        self.assertEqual(result["xml_updated_at"], "2026-07-12T06:46:19.000+10:00")


if __name__ == "__main__":
    unittest.main()
