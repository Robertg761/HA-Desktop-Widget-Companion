"""Tests for persisted desktop records."""

from custom_components.ha_desktop_widget.models import DesktopRecord


def test_registration_normalizes_non_secret_metadata() -> None:
    """Registration keeps stable identity while bounding user-controlled metadata."""
    record = DesktopRecord.from_registration(
        {
            "desktop_id": "desktop-12345678",
            "name": "  Office desktop  ",
            "platform": "linux",
            "architecture": "x64",
            "app_version": "3.9.0",
            "protocol_version": 1,
            "capabilities": ["switch_page", "visibility", "visibility"],
        },
        owner_user_id="user-1",
    )

    assert record.desktop_id == "desktop-12345678"
    assert record.name == "Office desktop"
    assert record.owner_user_id == "user-1"
    assert record.capabilities == ("switch_page", "visibility")
    assert "token" not in record.as_storage_dict()


def test_existing_registration_preserves_owner_and_runtime_state() -> None:
    """Metadata refreshes must not silently transfer desktop ownership."""
    original = DesktopRecord.from_registration(
        {
            "desktop_id": "desktop-12345678",
            "name": "Office",
            "capabilities": ["visibility"],
        },
        owner_user_id="user-1",
    )
    original.apply_state({"visible": True, "current_page": "office"})

    updated = DesktopRecord.from_registration(
        {
            "desktop_id": original.desktop_id,
            "name": "Office PC",
            "capabilities": ["visibility", "switch_page"],
        },
        owner_user_id="user-2",
        existing=original,
    )

    assert updated.owner_user_id == "user-1"
    assert updated.visible is True
    assert updated.current_page == "office"
    assert updated.name == "Office PC"


def test_storage_round_trip() -> None:
    """Stored records restore only bounded known fields."""
    record = DesktopRecord.from_storage(
        {
            "desktop_id": "desktop-12345678",
            "name": "Office",
            "owner_user_id": "user-1",
            "visible": "not-a-boolean",
            "current_page": " dashboard ",
            "capabilities": ["visibility", 123, "switch_page"],
            "unexpected_secret": "must-not-survive",
        }
    )

    serialized = record.as_storage_dict()
    assert serialized["visible"] is None
    assert serialized["current_page"] == "dashboard"
    assert serialized["capabilities"] == ["switch_page", "visibility"]
    assert "unexpected_secret" not in serialized
