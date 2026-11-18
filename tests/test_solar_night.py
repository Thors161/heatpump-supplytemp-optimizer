"""Forecast.Solar omits night hours between consecutive days."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from homeassistant.util import dt as dt_util
from custom_components.heatpump_supplytemp_optimizer.forecast import in_interval,solar_intervals


@pytest.fixture
def daylight(monkeypatch):
    monkeypatch.setattr(dt_util,'DEFAULT_TIME_ZONE',ZoneInfo('Europe/Amsterdam'))
    response={'wh_period':{f'2026-01-{day:02d}T{hour:02d}:00:00+01:00':100
                          for day in [15,16] for hour in range(8,17)}}
    return solar_intervals(response,{'pv_forecast_format':'forecast_solar'})


def at(value):return datetime.fromisoformat(value).timestamp()


@pytest.mark.parametrize('value',['2026-01-15T17:00:00+01:00','2026-01-15T23:45:00+01:00','2026-01-16T00:15:00+01:00','2026-01-16T07:45:00+01:00'])
def test_known_day_night_is_zero(daylight,value):
    assert in_interval(daylight,at(value),solar=True)==0


def test_missing_day_is_not_assumed_zero(daylight):
    assert in_interval(daylight,at('2026-01-17T00:15:00+01:00'),solar=True) is None


def test_daylight_hole_is_not_assumed_zero(daylight):
    missing=at('2026-01-15T10:00:00+01:00')
    daylight=[row for row in daylight if row[0]!=missing]
    assert in_interval(daylight,missing+450,solar=True) is None
    assert in_interval(daylight,missing-450,solar=True)==.1
