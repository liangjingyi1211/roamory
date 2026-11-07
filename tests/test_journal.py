from datetime import date
from pathlib import Path
from io import BytesIO
import base64
import json

import pytest
from streamlit.testing.v1 import AppTest
from PIL import Image

from travelmind.journal import JournalStore, apply_journal_event
from travelmind.models import Activity, Hotel, Restaurant, TripRequest, initialize_plan
from travelmind.storage import TripStore


@pytest.fixture
def journal_setup(tmp_path, monkeypatch):
    path = tmp_path / "v7.sqlite3"
    monkeypatch.setenv("TRAVELMIND_DB", str(path))
    monkeypatch.setattr("travelmind.places.audit", lambda *a, **k: None)
    monkeypatch.setattr("travelmind.agent.audit", lambda *a, **k: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("journal must not call agents or Places")
    monkeypatch.setattr("travelmind.service.generate_plan", forbidden)
    monkeypatch.setattr("travelmind.places.client.search", forbidden)
    monkeypatch.setattr("travelmind.places.client.search_nearby", forbidden)
    plan = initialize_plan(TripRequest(destination="Kyoto", start_date=date(2027, 4, 1), end_date=date(2027, 4, 2)))
    plan.days[0].activities = [Activity(name="Garden")]
    plan.days[0].lunch = Restaurant(name="Lunch Cafe")
    plan.days[0].hotel = Hotel(name="Quiet Hotel")
    store = TripStore(path)
    store.save(plan)
    return store, store.get(plan.trip_id), JournalStore(path)


def note_args(plan, kind="activity"):
    day = plan.days[0]
    item = day.activities[0] if kind == "activity" else day.lunch if kind == "restaurant" else day.hotel
    return dict(trip_id=plan.trip_id, day_id=day.day_id, item_type=kind, item_id=item.id)


def raw_trip(store, trip_id):
    with store.connect() as db:
        return db.execute("SELECT payload,version,updated FROM trips WHERE id=?", (trip_id,)).fetchone()


@pytest.mark.parametrize("kind", ["activity", "restaurant", "hotel"])
def test_note_create_reopen_edit_leaves_plan_untouched(journal_setup, kind):
    store, plan, journals = journal_setup
    before = raw_trip(store, plan.trip_id)
    args = note_args(plan, kind)
    entry = journals.save(**args, content="  First impression\nSecond line  ")
    assert entry.content == "First impression\nSecond line"
    assert entry.day_date == plan.days[0].date
    assert entry.created_at.tzinfo is not None
    reloaded = JournalStore(store.path).list_entries(plan.trip_id)[0]
    assert reloaded.id == entry.id
    updated = journals.save(**args, content="A new detail", expected_revision=reloaded.revision)
    assert updated.id == entry.id and updated.created_at == entry.created_at
    assert updated.revision == 2 and updated.updated_at >= entry.updated_at
    assert len(journals.list_entries()) == 1
    assert raw_trip(store, plan.trip_id) == before


def test_note_guards_and_conflicts_are_atomic(journal_setup):
    store, plan, journals = journal_setup
    args = note_args(plan)
    for bad in ("", "   ", "x" * 10001):
        with pytest.raises(ValueError):
            journals.save(**args, content=bad)
    with pytest.raises(ValueError):
        journals.save(**{**args, "day_id": plan.days[1].day_id}, content="Wrong day")
    with pytest.raises(ValueError):
        journals.save(**{**args, "item_type": "restaurant"}, content="Wrong type")
    with pytest.raises(ValueError):
        apply_journal_event(journals, plan.trip_id, {"kind": "journal", **args, "trip_id": "other", "content": "Wrong trip"})
    assert journals.list_entries() == []
    with pytest.raises(ValueError):
        journals.save(**args, content="Invalid revision", expected_revision=True)
    entry = journals.save(**args, content="Original")
    with pytest.raises(ValueError):
        journals.save(**args, content="Duplicate creation")
    journals.save(**args, content="Newer", expected_revision=entry.revision)
    with pytest.raises(ValueError):
        journals.save(**args, content="Stale", expected_revision=entry.revision)
    assert journals.list_entries()[0].content == "Newer"


def test_replaced_place_keeps_its_original_note(journal_setup):
    store, plan, journals = journal_setup
    args = note_args(plan, "restaurant")
    entry = journals.save(**args, content="The lunch I remember")
    store.edit(plan.trip_id, plan.days[0].day_id, "lunch", "save", name="Another Cafe")
    assert journals.list_entries()[0].place_name == "Lunch Cafe"
    journals.save(**args, content="Edited memory", expected_revision=entry.revision)
    current = store.get(plan.trip_id)
    assert current.days[0].lunch.id != entry.item_id
    journals.save(**note_args(current, "restaurant"), content="A different lunch")
    assert len(journals.list_entries()) == 2


def test_journal_schema_is_additive_and_not_part_of_plan(journal_setup):
    store, plan, journals = journal_setup
    before = raw_trip(store, plan.trip_id)
    JournalStore(store.path)
    assert raw_trip(store, plan.trip_id) == before
    assert "journals" not in plan.model_dump()
    assert "estimated_total_cost" not in plan.model_dump()


APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def test_home_library_and_journal_edit(journal_setup, monkeypatch):
    store, plan, journals = journal_setup
    note = journals.save(**note_args(plan), content="A peaceful afternoon <script>alert(1)</script>")
    home = AppTest.from_file(APP).run(timeout=20)
    assert not home.exception
    html = "\n".join(m.value for m in home.markdown)
    assert 'home-hero' in html and '<svg' in html and 'view=journals' in html and 'view=trips' in html
    assert any(button.label == "创建行程" for button in home.button)
    monkeypatch.setattr("travelmind.ui.workspace", lambda plan, ack, conversation=None: None)
    library = AppTest.from_file(APP)
    library.query_params["view"] = "trips"
    library.run(timeout=20)
    assert not library.exception and library.subheader[0].value == "Kyoto"
    library.button[0].click().run(timeout=20)
    assert not library.exception and library.query_params["trip"] == plan.trip_id
    diary = AppTest.from_file(APP)
    diary.query_params["view"] = "journals"
    diary.run(timeout=20)
    assert not diary.exception
    assert any('&lt;script&gt;' in m.value for m in diary.markdown)
    assert not diary.expander
    diary.button[0].click().run(timeout=20)
    diary.text_area[0].set_value("Updated diary text")
    next(button for button in diary.button if button.label == "保存记录").click().run(timeout=20)
    assert not diary.exception
    assert journals.list_entries()[0].content == "Updated diary text"
    assert journals.list_entries()[0].id == note.id


def test_workspace_journal_event_does_not_mutate_trip(journal_setup, monkeypatch):
    store, plan, journals = journal_setup
    before = raw_trip(store, plan.trip_id)
    events = [{"event_id": "journal-save", "kind": "journal", **note_args(plan), "content": "From the card", "revision": None}]
    monkeypatch.setattr("travelmind.ui.workspace", lambda plan, ack, conversation=None: events.pop() if events else None)
    app = AppTest.from_file(APP)
    app.query_params["trip"] = plan.trip_id
    app.run(timeout=20)
    assert not app.exception
    assert journals.list_entries()[0].content == "From the card"
    assert raw_trip(store, plan.trip_id) == before


def test_home_creation_failure_retains_input_then_uses_existing_service(journal_setup, monkeypatch):
    store, existing, _ = journal_setup
    before = raw_trip(store, existing.trip_id)
    async def failed(request):
        raise ValueError("temporary planning failure")
    monkeypatch.setattr("travelmind.service.generate_plan", failed)
    home = AppTest.from_file(APP).run(timeout=20)
    home.text_input[0].set_value("Lisbon, Portugal")
    home.text_input[1].set_value("architecture, coast")
    home.text_input[2].set_value("vegetarian")
    home.button[0].click().run(timeout=20)
    assert not home.exception and home.error
    assert home.text_input[0].value == "Lisbon, Portugal"
    captured = []
    async def successful(request):
        captured.append(request)
        return initialize_plan(request), ["activity_agent", "food_agent", "hotel_agent"]
    monkeypatch.setattr("travelmind.service.generate_plan", successful)
    monkeypatch.setattr("travelmind.ui.workspace", lambda plan, ack, conversation=None: None)
    home.button[0].click().run(timeout=20)
    assert not home.exception
    created = store.get(home.query_params["trip"])
    assert created.destination == "Lisbon, Portugal"
    assert captured[0].preferences.interests == ["architecture", "coast"]
    assert captured[0].preferences.food_preferences == ["vegetarian"]
    assert raw_trip(store, existing.trip_id) == before


def photo(name="coast.png", color="navy"):
    output = BytesIO()
    Image.new("RGB", (64, 40), color).save(output, format="PNG")
    return {"name": name, "data": output.getvalue()}


def test_journal_images_persist_replace_remove_and_keep_plan(journal_setup):
    store, plan, journals = journal_setup
    args = note_args(plan)
    before = raw_trip(store, plan.trip_id)
    upload = photo()
    entry = journals.save(**args, content="", new_images=[upload])
    image = entry.images[0]
    assert image.mime_type == "image/png" and image.width == 64 and image.height == 40
    reopened = JournalStore(store.path)
    assert reopened.image_bytes(entry.id, image.id) == upload["data"]
    assert reopened.image_bytes("another-note", image.id) is None
    assert reopened.workspace_entries(plan.trip_id)[0]["images"][0]["thumbnail_url"].startswith("data:image/jpeg;base64,")
    updated = journals.save(**args, content="A coast to remember", expected_revision=entry.revision)
    assert updated.images[0].id == image.id
    replacement = journals.save(**args, content="New photo", expected_revision=updated.revision,
        keep_image_ids=[], new_images=[photo("city.png", "blue")])
    assert journals.image_bytes(entry.id, image.id) is None
    assert replacement.images[0].name == "city.png"
    cleared = journals.save(**args, content="Text remains", expected_revision=replacement.revision, keep_image_ids=[])
    assert cleared.images == []
    assert journals.image_bytes(entry.id, replacement.images[0].id) is None
    assert raw_trip(store, plan.trip_id) == before


def test_image_validation_and_stale_updates_are_atomic(journal_setup):
    store, plan, journals = journal_setup
    args = note_args(plan)
    for uploads in ([photo()] * 7, [photo(), {"name": "fake.jpg", "data": b"not an image"}],
                    [{"name": "huge.png", "data": b"x" * (5 * 1024 * 1024 + 1)}],
                    [{"name": "vector.svg", "data": b'<svg xmlns="http://www.w3.org/2000/svg"/>'}]):
        with pytest.raises(ValueError):
            journals.save(**args, content="No partial saves", new_images=uploads)
    assert not journals.list_entries()
    with journals.connect() as db:
        assert db.execute("SELECT count(*) FROM journal_images").fetchone()[0] == 0
    entry = journals.save(**args, content="Original", new_images=[photo()])
    with pytest.raises(ValueError):
        journals.save(**args, content="Wrong image", expected_revision=entry.revision, keep_image_ids=["foreign-image"])
    journals.save(**args, content="Newer text", expected_revision=entry.revision)
    with pytest.raises(ValueError):
        journals.save(**args, content="Stale removal", expected_revision=entry.revision, keep_image_ids=[])
    assert journals.image_bytes(entry.id, entry.images[0].id)
    assert journals.list_entries()[0].content == "Newer text"


@pytest.mark.parametrize("kind,mime", [("JPEG","image/jpeg"),("PNG","image/png"),("WEBP","image/webp")])
def test_supported_raster_formats(journal_setup, kind, mime):
    _, plan, journals = journal_setup
    output = BytesIO()
    Image.new("RGB", (16, 12), "navy").save(output, format=kind)
    entry = journals.save(**note_args(plan), content="Photo", new_images=[{"name":"photo", "data":output.getvalue()}])
    assert entry.images[0].mime_type == mime
    with Image.open(BytesIO(journals.image_bytes(entry.id, entry.images[0].id, preview=True))) as preview:
        assert preview.format == "JPEG"


def test_card_image_event_and_legacy_text_notes(journal_setup):
    store, plan, journals = journal_setup
    upload = photo()
    entry = apply_journal_event(journals, plan.trip_id, {"kind":"journal", **note_args(plan), "content":"From a card",
        "new_images":[{"name": upload["name"], "data_url":"data:image/png;base64," + base64.b64encode(upload["data"]).decode()}], "keep_image_ids":[]})
    assert entry.images[0].name == "coast.png"
    with pytest.raises(ValueError):
        apply_journal_event(journals, plan.trip_id, {"kind":"journal", **note_args(plan), "content":"Broken",
            "new_images":[{"name":"broken.png", "data_url":"data:image/png;base64,not!base64"}]})
    text = journals.save(**note_args(plan, "hotel"), content="Old text-only note")
    payload = text.model_dump(mode="json")
    payload.pop("images")
    with journals.connect() as db:
        db.execute("UPDATE travel_journals SET payload=? WHERE id=?", (json.dumps(payload), text.id))
    loaded = next(note for note in JournalStore(store.path).list_entries() if note.id == text.id)
    assert loaded.images == [] and loaded.content == "Old text-only note"


def test_diary_edit_button_cancel_and_revision_snapshot(journal_setup):
    store, plan, journals = journal_setup
    entry = journals.save(**note_args(plan), content="Original text", new_images=[photo()])
    diary = AppTest.from_file(APP)
    diary.query_params["view"] = "journals"
    diary.run(timeout=20)
    assert not diary.exception and not diary.text_area and not diary.expander
    diary.button[0].click().run(timeout=20)
    assert diary.text_area[0].value == "Original text"
    diary.text_area[0].set_value("Cancelled changes")
    diary.checkbox[0].check()
    next(button for button in diary.button if button.label == "取消").click().run(timeout=20)
    assert not diary.exception and not diary.text_area
    assert journals.list_entries()[0].content == "Original text"
    assert len(journals.list_entries()[0].images) == 1
    diary.button[0].click().run(timeout=20)
    journals.save(**note_args(plan), content="Edited elsewhere", expected_revision=entry.revision)
    diary.text_area[0].set_value("Stale form")
    next(button for button in diary.button if button.label == "保存记录").click().run(timeout=20)
    assert not diary.exception and diary.error
    assert journals.list_entries()[0].content == "Edited elsewhere"
