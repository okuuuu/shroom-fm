from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from shroom_fm.meps import (
    accumulate_meps_features,
    meps_dataset_to_points,
    meps_hourly_url,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _fake_dataset(temp_k: float, rh_frac: float) -> xr.Dataset:
    # Tiny 2x2 grid in the real LCC projection's coordinate space, centered near
    # Estonia's real LCC x/y (from the live-verified projection_lcc parameters).
    x = np.array([300000.0, 301000.0])
    y = np.array([700000.0, 701000.0])
    temp = np.full((1, 2, 2), temp_k, dtype=np.float32)
    rh = np.full((1, 2, 2), rh_frac, dtype=np.float32)
    lon, lat = np.meshgrid([24.7, 24.8], [59.4, 59.5])
    return xr.Dataset(
        {
            "air_temperature_2m": (("time", "y", "x"), temp),
            "relative_humidity_2m": (("time", "y", "x"), rh),
            "latitude": (("y", "x"), lat),
            "longitude": (("y", "x"), lon),
        },
        coords={"time": [np.datetime64("2026-08-15T00:00:00")], "y": y, "x": x},
    )


def test_meps_hourly_url_builds_archive_path():
    url = meps_hourly_url(_utc(2026, 8, 15, 6))
    assert url == (
        "https://thredds.met.no/thredds/dodsC/metpparchive/2026/08/15/"
        "met_analysis_1_0km_nordic_20260815T06Z.nc"
    )


def test_meps_dataset_to_points_converts_units_and_flattens_grid():
    dataset = _fake_dataset(temp_k=283.15, rh_frac=0.8)

    points = meps_dataset_to_points(dataset)

    assert len(points) == 4
    assert points["temp_c"].iloc[0] == pytest.approx(10.0)
    assert points["rh_pct"].iloc[0] == pytest.approx(80.0)
    assert points.crs == "EPSG:3301"


def test_accumulate_meps_features_computes_day_night_means(monkeypatch):
    # 2 fake hours: one clearly "day" (noon local), one clearly "night" (02:00 local)
    fetched = {}

    def fake_fetch(hour, bbox):
        if hour == _utc(2026, 8, 14, 9):  # 12:00 EEST — day
            return _fake_dataset(temp_k=293.15, rh_frac=0.5)  # 20C
        if hour == _utc(2026, 8, 14, 23):  # 02:00 EEST next day — night
            return _fake_dataset(temp_k=283.15, rh_frac=0.9)  # 10C
        return None

    monkeypatch.setattr("shroom_fm.meps.fetch_meps_hourly", fake_fetch)

    now = _utc(2026, 8, 15, 0)  # only these 2 fake hours will resolve; rest are gaps
    bbox = (24.0, 59.0, 25.0, 60.0)

    points, coverage, newest = accumulate_meps_features(now, bbox)

    assert coverage == pytest.approx(2 / 72)
    row0 = points.iloc[0]
    assert row0["temp_mean_3d"] == pytest.approx((20.0 + 10.0) / 2)
    assert row0["temp_night_mean_3d"] == pytest.approx(10.0)
    assert row0["rh_night_mean_3d"] == pytest.approx(90.0)


class _MockDataset:
    """Mock xarray Dataset that bypasses coordinate slicing for testing."""
    def __init__(self, dataset):
        self._ds = dataset

    def __getitem__(self, key):
        return self._ds[key]

    def sel(self, **kwargs):
        """Pretend slice returns full dataset (avoids coordinate mismatch issues in tests)."""
        return self._ds

    def load(self):
        return self._ds.load()

    def close(self):
        self._ds.close()

    @property
    def sizes(self):
        return self._ds.sizes


def test_fetch_meps_hourly_rejects_latest_dataset_with_mismatched_time(monkeypatch):
    from shroom_fm.meps import MEPS_LATEST_URL, fetch_meps_hourly

    requested_hour = _utc(2026, 8, 15, 6)
    wrong_time_dataset = _fake_dataset(temp_k=280.0, rh_frac=0.5)  # time is 2026-08-15T00:00:00

    def fake_open_dataset(url):
        if url == MEPS_LATEST_URL:
            return _MockDataset(wrong_time_dataset)
        raise OSError("archive file not found")

    monkeypatch.setattr("shroom_fm.meps.xr.open_dataset", fake_open_dataset)

    result = fetch_meps_hourly(requested_hour, (24.0, 59.0, 25.0, 60.0))

    assert result is None


def test_fetch_meps_hourly_accepts_latest_dataset_with_matching_time(monkeypatch):
    from shroom_fm.meps import MEPS_LATEST_URL, fetch_meps_hourly

    requested_hour = _utc(2026, 8, 15, 0)  # matches _fake_dataset's hardcoded time
    matching_dataset = _fake_dataset(temp_k=280.0, rh_frac=0.5)

    def fake_open_dataset(url):
        if url == MEPS_LATEST_URL:
            return _MockDataset(matching_dataset)
        raise OSError("archive file not found")

    monkeypatch.setattr("shroom_fm.meps.xr.open_dataset", fake_open_dataset)

    result = fetch_meps_hourly(requested_hour, (24.0, 59.0, 25.0, 60.0))

    assert result is not None


def test_fetch_meps_hourly_treats_sel_failure_as_unavailable(monkeypatch):
    # A transient OSError during .sel()/.load() (both can trigger lazy OPeNDAP network
    # I/O) must be treated as "this hour unavailable", not escape and abort the whole
    # accumulate_meps_features run. Exercises BOTH loop attempts (archive url with
    # verify_time=False, and MEPS_LATEST_URL with verify_time=True) — the fake dataset's
    # "time" value matches expected_time so the verify_time branch passes through to
    # .sel(), which then raises OSError on both attempts.
    from shroom_fm.meps import fetch_meps_hourly

    requested_hour = _utc(2026, 8, 15, 6)
    expected_time = np.datetime64("2026-08-15T06:00:00")

    class _TimeValues:
        values = np.array([expected_time])

    class _FailingDataset:
        def __getitem__(self, key):
            if key == "time":
                return _TimeValues()
            raise KeyError(key)

        def sel(self, **kwargs):
            raise OSError("transient network failure during lazy read")

        def close(self):
            pass

    def fake_open_dataset(url):
        return _FailingDataset()

    monkeypatch.setattr("shroom_fm.meps.xr.open_dataset", fake_open_dataset)

    result = fetch_meps_hourly(requested_hour, (24.0, 59.0, 25.0, 60.0))

    assert result is None
