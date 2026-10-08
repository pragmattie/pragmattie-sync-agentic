import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from sdlc.clock import utcnow


NEEDS_TZSET = pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is Unix-only")


@pytest.fixture
def tokyo(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@NEEDS_TZSET
def test_utcnow_is_naive_utc_to_the_second(tokyo):
    assert os.environ["TZ"] == "Asia/Tokyo"
    moment = utcnow()
    assert moment.tzinfo is None
    assert moment.microsecond == 0
    real = datetime.now(UTC).replace(tzinfo=None)
    assert abs(real - moment) < timedelta(seconds=2)
    assert abs(datetime.now() - moment) > timedelta(hours=8)  # Tokyo's local time is not used
