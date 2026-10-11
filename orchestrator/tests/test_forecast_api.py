from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc import forecast, forecaster
from sdlc.db import Base
from sdlc.forecaster import REAL_NOTE, SIMULATED_NOTE, TRAIL
from sdlc.tables import Epic, Forecast
from tests.test_forecaster import _forecast

URL = "/api/v1/signals/forecast"
DAY = date(2026, 10, 6)


@pytest.fixture
def db(client, sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        yield db


def _milestone(db, name, state="open"):
    db.add(
        Epic(
            source="github",
            external_id=f"milestone-{name.split()[0]}",
            name=name,
            state=state,
            created_at=datetime(2026, 9, 1),
        )
    )


def test_real_epics_come_first_in_milestone_order_with_labels_and_notes(client, db):
    for name in ("M10 Launch", "M7 Planner", "M5 Forecasting"):
        _milestone(db, name)
        _forecast(db, "epic", name, DAY, DAY, DAY, source="github")
    _forecast(db, "epic", "Pipeline", DAY, DAY, DAY)
    _forecast(db, "epic", "Contacts", DAY, DAY, DAY)
    db.commit()

    epics = client.get(URL).json()["epics"]
    assert [(epic["label"], epic["kind_note"]) for epic in epics] == [
        ("Forecasting (M5)", REAL_NOTE),
        ("Planner (M7)", REAL_NOTE),
        ("Launch (M10)", REAL_NOTE),
        ("Contacts", SIMULATED_NOTE),
        ("Pipeline", SIMULATED_NOTE),
    ]
    assert epics[0]["source"] == "github" and epics[-1]["source"] == "synthetic"
    assert {"end_date", "on_time_probability", "remaining_real", "at_risk"} <= set(epics[0])


def test_moved_against_the_previous_forecast(client, db):
    _forecast(db, "sprint", "Sprint 13", DAY, date(2026, 10, 9), date(2026, 10, 12))
    _forecast(db, "sprint", "Sprint 13", DAY, date(2026, 10, 12), date(2026, 10, 13), hour=15)
    db.commit()

    sprint = client.get(URL).json()["sprint"]
    assert sprint["kind_note"] == SIMULATED_NOTE
    assert sprint["p50"] == "2026-10-12"
    assert sprint["moved"] == {
        "p50_days": 3,
        "p85_days": 1,
        "previous_p50": "2026-10-09",
        "previous_p85": "2026-10-12",
        "previous_at": "2026-10-06T09:00:00",
    }


def test_the_trail_holds_the_newest_thirty_oldest_first(client, db):
    first = date(2026, 8, 1)
    for offset in range(TRAIL + 10):
        day = first + timedelta(days=offset)
        _forecast(db, "epic", "Pipeline", day, day + timedelta(days=20), None)
    db.commit()

    trail = client.get(URL).json()["epics"][0]["trail"]
    assert len(trail) == TRAIL
    assert trail[0]["at"] == f"{first + timedelta(days=10)}T09:00:00"
    assert trail[-1]["at"] == f"{first + timedelta(days=TRAIL + 9)}T09:00:00"
    assert [point["at"] for point in trail] == sorted(point["at"] for point in trail)
    assert set(trail[0]) == {"at", "p50", "p85"}


def test_a_subject_with_one_forecast(client, db):
    only = _forecast(db, "epic", "Pipeline", DAY, date(2026, 11, 2), None)
    db.commit()

    body = client.get(URL).json()
    (pipeline,) = body["epics"]
    assert pipeline["id"] == only.id
    assert pipeline["moved"] == dict.fromkeys(forecaster.MOVED_KEYS)
    assert pipeline["trail"] == [{"at": "2026-10-06T09:00:00", "p50": "2026-11-02", "p85": None}]
    assert body["saved"] == 1 and body["as_of"] == "2026-10-06"
    assert body["sprint"] is None


def test_an_empty_database_gives_empty_lists(client, db):
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {
        "epics": [],
        "sprint": None,
        "proposals": {},
        "saved": 0,
        "as_of": None,
    }


def test_a_closed_milestone_is_not_listed_and_its_forecasts_are_kept(client, db):
    _milestone(db, "M5 Forecasting", state="closed")
    closed = _forecast(db, "epic", "M5 Forecasting", DAY, DAY, DAY, source="github")
    db.commit()

    body = client.get(URL).json()
    assert body["epics"] == []
    assert body["saved"] == 1
    assert client.get(f"{URL}/{closed.id}").json()["subject"] == "M5 Forecasting"


def test_one_forecast_and_a_404(client, db):
    row = _forecast(db, "sprint", "Sprint 13", DAY, date(2026, 10, 9), None)
    db.commit()

    response = client.get(f"{URL}/{row.id}")
    assert response.status_code == 200
    assert response.json() == forecaster.serialize(db.get(Forecast, row.id))
    assert client.get(f"{URL}/{row.id + 1}").status_code == 404


def test_the_endpoint_never_simulates(client, db, monkeypatch):
    _milestone(db, "M5 Forecasting")
    _forecast(db, "epic", "M5 Forecasting", DAY, DAY, DAY, source="github")
    _forecast(db, "epic", "Pipeline", DAY, DAY, DAY)
    _forecast(db, "sprint", "Sprint 13", DAY, DAY, DAY)
    db.commit()
    calls = []

    def count(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(forecast, "simulate", count)
    monkeypatch.setattr(forecaster, "sprint_forecast", count)
    monkeypatch.setattr(forecaster, "epic_forecast", count)

    body = client.get(URL).json()
    assert len(body["epics"]) == 2 and body["sprint"]["subject"] == "Sprint 13"
    assert calls == []
