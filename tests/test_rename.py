"""Brand rename compatibility: data locations, persisted IDs and UI branding."""
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image
from roamory.config import database_path, log_path, LEGACY_DB_ENV, LEGACY_LOG_ENV, LEGACY_DB_PATH
from roamory.models import DailyPlan, Activity, TripRequest, initialize_plan
from roamory.storage import TripStore
from roamory.journal import JournalStore
from roamory.timeline import timeline_id


def clean_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("ROAMORY_DB", "ROAMORY_LOG_FILE", LEGACY_DB_ENV, LEGACY_LOG_ENV):
        monkeypatch.delenv(key, raising=False)


def test_new_configuration_has_priority_and_old_configuration_still_works(monkeypatch, tmp_path):
    clean_environment(monkeypatch, tmp_path)
    assert database_path() == Path("data/roamory.sqlite3")
    monkeypatch.setenv(LEGACY_DB_ENV, "old-location.sqlite3")
    assert database_path() == Path("old-location.sqlite3")
    monkeypatch.setenv("ROAMORY_DB", "new-location.sqlite3")
    assert database_path() == Path("new-location.sqlite3")
    assert database_path("explicit.sqlite3") == Path("explicit.sqlite3")
    monkeypatch.setenv(LEGACY_LOG_ENV, "old.log")
    assert log_path() == Path("old.log")
    monkeypatch.setenv("ROAMORY_LOG_FILE", "new.log")
    assert log_path() == Path("new.log")


def test_existing_database_journal_and_image_are_reused_without_rewriting(monkeypatch, tmp_path):
    clean_environment(monkeypatch, tmp_path)
    plan = initialize_plan(TripRequest(destination="Kyoto", start_date=date(2027,4,1), end_date=date(2027,4,1)))
    plan.days[0].activities = [Activity(name="Existing garden")]
    TripStore(LEGACY_DB_PATH).save(plan)
    output = BytesIO()
    Image.new("RGB", (12, 12), "navy").save(output, format="PNG")
    entry = JournalStore(LEGACY_DB_PATH).save(trip_id=plan.trip_id, day_id=plan.days[0].day_id,
        item_type="activity", item_id=plan.days[0].activities[0].id, content="A saved memory",
        new_images=[{"name":"existing.png", "data":output.getvalue()}])
    before = LEGACY_DB_PATH.read_bytes()
    assert TripStore().path == LEGACY_DB_PATH
    assert JournalStore().path == LEGACY_DB_PATH
    reopened = TripStore().get(plan.trip_id)
    assert reopened.days[0].activities[0].name == "Existing garden"
    restored = JournalStore().list_entries()[0]
    assert restored.id == entry.id and restored.content == "A saved memory"
    assert JournalStore().image_bytes(entry.id, entry.images[0].id) == output.getvalue()
    assert LEGACY_DB_PATH.read_bytes() == before
    assert not Path("data/roamory.sqlite3").exists()


def test_timeline_id_is_unchanged_by_brand_rename():
    day = DailyPlan(day_id="existing-day", day_index=1, date=date(2027,4,1))
    assert timeline_id(day,"meal","breakfast") == "fc007bea-a807-5aad-bfab-6c6efc6de0ae"


def test_runtime_brand_and_imports_use_new_name():
    root = Path(__file__).resolve().parents[1]
    from roamory import root_agent
    assert root_agent.name == "roamory"
    assert (root / "roamory/frontend/index.html").is_file()
    assert '<title>Roamory' in (root / "roamory/frontend/index.html").read_text(encoding="utf-8")
    assert 'page_title="Roamory"' in (root / "streamlit_app.py").read_text(encoding="utf-8")
    for source in [root / "streamlit_app.py", *(root / "roamory").rglob("*.py"), *(root / "roamory/frontend").glob("*.js")]:
        if source.name == "config.py":
            continue  # Four intentional compatibility constants, covered above.
        assert "travelmind" not in source.read_text(encoding="utf-8").lower(), source
