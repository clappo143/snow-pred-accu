"""score.POOLED_EXCLUDE: bom_meteye's hotham/buller rows are dropped from
pooled (multi-resort) scores but kept in per-resort views. The Vic ADFD
snow grid can flag Rain at -1C during real snow events (see
collectors/bom_meteye.py), so pooling would score a known artifact."""
import sqlite3

import score


def _db():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE forecasts (resort TEXT, source TEXT, issued_date TEXT,"
        " run TEXT, target_date TEXT, snow_cm REAL)"
    )
    con.execute("CREATE TABLE actuals (resort TEXT, date TEXT, snow_cm REAL)")
    for resort in ("hotham", "perisher"):
        for source in ("bom", "bom_meteye"):
            con.execute(
                "INSERT INTO forecasts VALUES (?, ?, '2026-07-11', 'pm',"
                " '2026-07-12', 10.0)",
                (resort, source),
            )
        con.execute(
            "INSERT INTO actuals VALUES (?, '2026-07-13', 12.0)", (resort,)
        )
    return con


def test_pooled_excludes_bom_meteye_vic():
    con = _db()
    pooled = score.pairs(con, ["hotham", "perisher"])
    meteye_resorts = {  # source col carries no resort; count rows instead
        (s, fc) for s, _r, _l, _d, fc, _a in pooled if s == "bom_meteye"
    }
    assert sum(1 for s, *_ in pooled if s == "bom_meteye") == 1
    assert sum(1 for s, *_ in pooled if s == "bom") == 2
    assert meteye_resorts  # perisher row survives


def test_per_resort_keeps_bom_meteye():
    con = _db()
    rows = score.pairs(con, "hotham")
    assert sum(1 for s, *_ in rows if s == "bom_meteye") == 1
