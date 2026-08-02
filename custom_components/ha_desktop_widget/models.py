"""Persistent and runtime models for HA Desktop Widget desktops."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


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
            protocol_version=max(1, int(data.get("protocol_version", 1))),
            capabilities=_clean_capabilities(data.get("capabilities")),
            visible=data.get("visible") if isinstance(data.get("visible"), bool) else None,
            current_page=_clean_optional_string(data.get("current_page"), maximum=128),
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
            protocol_version=max(1, int(registration.get("protocol_version", 1))),
            capabilities=_clean_capabilities(registration.get("capabilities")),
            visible=existing.visible if existing else None,
            current_page=existing.current_page if existing else None,
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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
        }

    def as_public_dict(self, *, online: bool) -> dict[str, Any]:
        """Serialize fields safe to return through the WebSocket API."""
        data = self.as_storage_dict()
        data["online"] = online
        return data
