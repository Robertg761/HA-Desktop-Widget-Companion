"""Persistent and runtime models for HA Desktop Widget desktops."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .const import (
    MAX_PROFILE_DOCUMENT_BYTES,
    MAX_PROFILE_DOCUMENT_DEPTH,
    PROFILE_SECTION_KEYS,
)


def utcnow_iso() -> str:
    """Return a stable UTC timestamp for storage and protocol messages."""
    return datetime.now(UTC).isoformat()


def _clean_optional_string(value: Any, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:maximum]


def _clean_string(value: Any, *, fallback: str, maximum: int) -> str:
    return _clean_optional_string(value, maximum=maximum) or fallback


def _clean_capabilities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple | set):
        return ()
    normalized = {
        capability.strip()[:64]
        for capability in value
        if isinstance(capability, str) and capability.strip()
    }
    return tuple(sorted(normalized))[:32]


def _clean_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _clean_protocol_version(value: Any) -> int:
    return _clean_int(value, minimum=1, maximum=1000) or 1


class ProfileDocumentError(ValueError):
    """Raised when a profile document exceeds the structural bounds Home Assistant enforces."""


def _document_depth(value: Any) -> int:
    depth = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, level = stack.pop()
        if isinstance(current, dict):
            depth = max(depth, level)
            stack.extend((child, level + 1) for child in current.values())
        elif isinstance(current, list):
            depth = max(depth, level)
            stack.extend((child, level + 1) for child in current)
    return depth


def validate_profile_document(document: Any) -> dict[str, Any]:
    """Return a detached copy of a structurally valid profile document.

    Home Assistant checks only the top-level sections, JSON-compatibility, size, and depth.
    The desktop owns the semantic normalization of each section's contents.
    """
    if not isinstance(document, dict):
        raise ProfileDocumentError("Profile document must be an object")
    unknown = sorted(str(key) for key in document if key not in PROFILE_SECTION_KEYS)
    if unknown:
        raise ProfileDocumentError(f"Unsupported profile sections: {', '.join(unknown[:5])}")
    try:
        serialized = json.dumps(document, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as err:
        raise ProfileDocumentError("Profile document must be plain JSON data") from err
    if len(serialized.encode()) > MAX_PROFILE_DOCUMENT_BYTES:
        raise ProfileDocumentError(
            f"Profile document exceeds {MAX_PROFILE_DOCUMENT_BYTES // 1024} KiB"
        )
    if _document_depth(document) > MAX_PROFILE_DOCUMENT_DEPTH:
        raise ProfileDocumentError(
            f"Profile document is nested deeper than {MAX_PROFILE_DOCUMENT_DEPTH} levels"
        )
    return json.loads(serialized)


@dataclass(slots=True)
class DesktopRecord:
    """A registered desktop and its last known state."""

    desktop_id: str
    name: str
    owner_user_id: str
    platform: str = "unknown"
    architecture: str = "unknown"
    app_version: str = "unknown"
    protocol_version: int = 1
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    visible: bool | None = None
    current_page: str | None = None
    window_width: int | None = None
    window_height: int | None = None
    active_profile_id: str | None = None
    profile_revision: int | None = None
    assigned_profile_id: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)
    last_seen_at: str | None = None

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> DesktopRecord:
        """Restore a record from integration storage."""
        desktop_id = _clean_string(data.get("desktop_id"), fallback="invalid", maximum=128)
        return cls(
            desktop_id=desktop_id,
            name=_clean_string(data.get("name"), fallback="Desktop Widget", maximum=64),
            owner_user_id=_clean_string(
                data.get("owner_user_id"), fallback="unknown", maximum=128
            ),
            platform=_clean_string(data.get("platform"), fallback="unknown", maximum=32),
            architecture=_clean_string(
                data.get("architecture"), fallback="unknown", maximum=32
            ),
            app_version=_clean_string(
                data.get("app_version"), fallback="unknown", maximum=32
            ),
            protocol_version=_clean_protocol_version(data.get("protocol_version")),
            capabilities=_clean_capabilities(data.get("capabilities")),
            visible=data.get("visible") if isinstance(data.get("visible"), bool) else None,
            current_page=_clean_optional_string(data.get("current_page"), maximum=128),
            window_width=_clean_int(data.get("window_width"), minimum=100, maximum=10000),
            window_height=_clean_int(data.get("window_height"), minimum=100, maximum=10000),
            active_profile_id=_clean_optional_string(data.get("active_profile_id"), maximum=64),
            profile_revision=_clean_int(
                data.get("profile_revision"), minimum=0, maximum=2**31 - 1
            ),
            assigned_profile_id=_clean_optional_string(
                data.get("assigned_profile_id"), maximum=64
            ),
            created_at=_clean_string(
                data.get("created_at"), fallback=utcnow_iso(), maximum=64
            ),
            updated_at=_clean_string(
                data.get("updated_at"), fallback=utcnow_iso(), maximum=64
            ),
            last_seen_at=_clean_optional_string(data.get("last_seen_at"), maximum=64),
        )

    @classmethod
    def from_registration(
        cls,
        registration: dict[str, Any],
        *,
        owner_user_id: str,
        existing: DesktopRecord | None = None,
    ) -> DesktopRecord:
        """Create or update a record from a validated registration message."""
        now = utcnow_iso()
        desktop_id = _clean_string(
            registration.get("desktop_id"), fallback="invalid", maximum=128
        )
        return cls(
            desktop_id=desktop_id,
            name=_clean_string(
                registration.get("name"),
                fallback=existing.name if existing else "Desktop Widget",
                maximum=64,
            ),
            owner_user_id=existing.owner_user_id if existing else owner_user_id,
            platform=_clean_string(
                registration.get("platform"),
                fallback=existing.platform if existing else "unknown",
                maximum=32,
            ),
            architecture=_clean_string(
                registration.get("architecture"),
                fallback=existing.architecture if existing else "unknown",
                maximum=32,
            ),
            app_version=_clean_string(
                registration.get("app_version"),
                fallback=existing.app_version if existing else "unknown",
                maximum=32,
            ),
            protocol_version=_clean_protocol_version(registration.get("protocol_version")),
            capabilities=_clean_capabilities(registration.get("capabilities")),
            visible=existing.visible if existing else None,
            current_page=existing.current_page if existing else None,
            window_width=existing.window_width if existing else None,
            window_height=existing.window_height if existing else None,
            active_profile_id=existing.active_profile_id if existing else None,
            profile_revision=existing.profile_revision if existing else None,
            assigned_profile_id=existing.assigned_profile_id if existing else None,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            last_seen_at=existing.last_seen_at if existing else None,
        )

    def apply_state(self, state: dict[str, Any]) -> None:
        """Apply a validated state patch received from the desktop."""
        if "visible" in state and isinstance(state["visible"], bool):
            self.visible = state["visible"]
        if "current_page" in state:
            self.current_page = _clean_optional_string(state["current_page"], maximum=128)
        for key in ("window_width", "window_height"):
            if key in state:
                size = _clean_int(state[key], minimum=100, maximum=10000)
                if size is not None:
                    setattr(self, key, size)
        # The desktop reports its applied profile identity only once one has been applied,
        # so an absent key keeps the last known identity rather than clearing it.
        if "active_profile_id" in state:
            profile_id = _clean_optional_string(state["active_profile_id"], maximum=64)
            # A revision belongs to one profile; never carry it over to a different one.
            if profile_id != self.active_profile_id and "profile_revision" not in state:
                self.profile_revision = None
            self.active_profile_id = profile_id
        if "profile_revision" in state:
            self.profile_revision = _clean_int(
                state["profile_revision"], minimum=0, maximum=2**31 - 1
            )
        self.last_seen_at = utcnow_iso()
        self.updated_at = self.last_seen_at

    def as_storage_dict(self) -> dict[str, Any]:
        """Serialize persistent, non-secret fields."""
        return {
            "desktop_id": self.desktop_id,
            "name": self.name,
            "owner_user_id": self.owner_user_id,
            "platform": self.platform,
            "architecture": self.architecture,
            "app_version": self.app_version,
            "protocol_version": self.protocol_version,
            "capabilities": list(self.capabilities),
            "visible": self.visible,
            "current_page": self.current_page,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "active_profile_id": self.active_profile_id,
            "profile_revision": self.profile_revision,
            "assigned_profile_id": self.assigned_profile_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
        }

    def as_public_dict(self, *, online: bool) -> dict[str, Any]:
        """Serialize fields safe to return through the WebSocket API."""
        data = self.as_storage_dict()
        data["online"] = online
        return data


@dataclass(slots=True)
class ProfileRecord:
    """A named, revision-controlled desktop profile authored in Home Assistant."""

    profile_id: str
    name: str
    document: dict[str, Any]
    revision: int = 1
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> ProfileRecord | None:
        """Restore a stored profile, discarding records that no longer validate."""
        profile_id = _clean_optional_string(data.get("profile_id"), maximum=64)
        name = _clean_optional_string(data.get("name"), maximum=64)
        revision = _clean_int(data.get("revision"), minimum=1, maximum=2**31 - 1)
        if profile_id is None or name is None or revision is None:
            return None
        try:
            document = validate_profile_document(data.get("document"))
        except ProfileDocumentError:
            return None
        return cls(
            profile_id=profile_id,
            name=name,
            document=document,
            revision=revision,
            created_at=_clean_string(data.get("created_at"), fallback=utcnow_iso(), maximum=64),
            updated_at=_clean_string(data.get("updated_at"), fallback=utcnow_iso(), maximum=64),
        )

    def as_storage_dict(self) -> dict[str, Any]:
        """Serialize the profile, including its document."""
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "revision": self.revision,
            "document": self.document,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def as_summary_dict(self) -> dict[str, Any]:
        """Serialize the profile identity and section names without its contents."""
        data = self.as_storage_dict()
        data["sections"] = sorted(data.pop("document"))
        return data
