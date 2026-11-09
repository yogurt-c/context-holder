"""기기 로컬 앱 설정."""

import json

import pytest

from context_holder import appsettings
from context_holder.appsettings import Settings


def test_defaults_and_round_trip_keep_other_keys(tmp_path):
    path = tmp_path / "app-state.json"
    assert appsettings.load(path) == Settings()
    path.write_text(json.dumps({"pause_until": "forever"}))
    appsettings.update(path, daily_at="08:00", weekends=True, theme="dark")
    data = json.loads(path.read_text())
    assert data["pause_until"] == "forever" and data["daily_at"] == "08:00"
    assert appsettings.load(path).daily_time.hour == 8


@pytest.mark.parametrize(
    "content",
    ["{broken", "[1]", '{"daily_at": "25:99"}', '{"notify": true, "unknown": 1, "theme": "neon"}'],
)
def test_broken_or_odd_files_fall_back(tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content)
    loaded = appsettings.load(path)
    assert loaded.daily_at == "09:30" and loaded.theme == "system"


def test_update_rejects_bad_values(tmp_path):
    path = tmp_path / "s.json"
    with pytest.raises(ValueError):
        appsettings.update(path, daily_at="9시")
    with pytest.raises(ValueError):
        appsettings.update(path, theme="neon")
    assert not path.exists()
