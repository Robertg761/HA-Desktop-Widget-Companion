"""Runtime coordinator for registered HA Desktop Widget clients."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import (
    COMMAND_EXPIRY_SECONDS,
    COMMAND_TIMEOUT_SECONDS,
    DATA_RUNTIMES,
    DOMAIN,
    PERSIST_DEBOUNCE_SECONDS,
    PROTOCOL_VERSION,
    STORE_KEY_PREFIX,
    STORE_VERSION,
)
from .models import DesktopRecord, utcnow_iso


class DesktopUnavailableError(HomeAssistantError):
    """Raised when a desktop has no active command subscription."""


class DesktopOwnershipError(HomeAssistantError):
    """Raised when a user tries to operate another user's desktop."""


class DesktopCommandError(HomeAssistantError):
    """Raised when a desktop rejects or fails a command."""


@dataclass(slots=True)
class DesktopSession:
    """A live authenticated desktop command subscription."""

    connection: Any
    subscription_id: int
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class HADesktopWidgetRuntime:
    """Coordinate storage, sessions, entities, and remote commands."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{STORE_KEY_PREFIX}.{entry_id}"
        )
        self.desktops: dict[str, DesktopRecord] = {}
        self.sessions: dict[str, DesktopSession] = {}
        self._connection_desktops: dict[int, str] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._cancel_save: CALLBACK_TYPE | None = None

    async def async_load(self) -> None:
        """Load registered desktops and start them in an offline state."""
        stored = await self.store.async_load() or {}
        raw_desktops = stored.get("desktops", {})
        if not isinstance(raw_desktops, dict):
            return
        for desktop_id, raw_record in raw_desktops.items():
            if not isinstance(desktop_id, str) or not isinstance(raw_record, dict):
                continue
            record = DesktopRecord.from_storage(
                {**raw_record, "desktop_id": raw_record.get("desktop_id", desktop_id)}
            )
            if record.desktop_id != "invalid":
                self.desktops[record.desktop_id] = record

    async def async_save(self) -> None:
        """Persist all registered desktop records."""
        if self._cancel_save is not None:
            self._cancel_save()
            self._cancel_save = None
        await self.store.async_save(
            {
                "desktops": {
                    desktop_id: record.as_storage_dict()
                    for desktop_id, record in self.desktops.items()
                }
            }
        )

    @callback
    def async_schedule_save(self) -> None:
        """Debounce state persistence to avoid heartbeat write churn."""
        if self._cancel_save is not None:
            self._cancel_save()

        @callback
        def save(_now: datetime) -> None:
            self._cancel_save = None
            self.hass.async_create_task(self.async_save())

        self._cancel_save = async_call_later(
            self.hass,
            PERSIST_DEBOUNCE_SECONDS,
            save,
        )

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe an entity platform or entity to runtime changes."""
        self._listeners.add(listener)

        @callback
        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def get_desktop(self, desktop_id: str) -> DesktopRecord | None:
        """Return a desktop by its stable installation ID."""
        return self.desktops.get(desktop_id)

    def is_online(self, desktop_id: str) -> bool:
        """Return whether a desktop has an active command subscription."""
        return desktop_id in self.sessions

    def supports(self, desktop_id: str, capability: str) -> bool:
        """Return whether a desktop advertised a protocol capability."""
        record = self.desktops.get(desktop_id)
        return record is not None and capability in record.capabilities

    def assert_owner(self, desktop_id: str, *, user_id: str, is_admin: bool) -> DesktopRecord:
        """Return a desktop after enforcing its owning HA user boundary."""
        record = self.desktops.get(desktop_id)
        if record is None:
            raise DesktopUnavailableError("Desktop is not registered")
        if not is_admin and record.owner_user_id != user_id:
            raise DesktopOwnershipError("Desktop belongs to another Home Assistant user")
        return record

    async def async_register_desktop(
        self,
        registration: dict[str, Any],
        *,
        user_id: str,
        is_admin: bool,
    ) -> DesktopRecord:
        """Register a desktop or update its non-secret metadata."""
        desktop_id = registration["desktop_id"]
        existing = self.desktops.get(desktop_id)
        if existing is not None and not is_admin and existing.owner_user_id != user_id:
            raise DesktopOwnershipError("Desktop belongs to another Home Assistant user")
        record = DesktopRecord.from_registration(
            registration,
            owner_user_id=user_id,
            existing=existing,
        )
        self.desktops[record.desktop_id] = record
        await self.async_save()
        self._notify()
        return record

    @callback
    def async_subscribe_commands(
        self,
        desktop_id: str,
        *,
        connection: Any,
        subscription_id: int,
        user_id: str,
        is_admin: bool,
    ) -> Callable[[], None]:
        """Attach a WebSocket subscription as the desktop's live session."""
        record = self.assert_owner(desktop_id, user_id=user_id, is_admin=is_admin)

        previous_desktop_id = self._connection_desktops.get(id(connection))
        if previous_desktop_id and previous_desktop_id != desktop_id:
            previous = self.sessions.get(previous_desktop_id)
            if previous and previous.connection is connection:
                self._remove_session(
                    previous_desktop_id,
                    previous,
                    "Session moved to another desktop",
                )

        previous = self.sessions.get(desktop_id)
        if previous is not None:
            self._remove_session(desktop_id, previous, "Session replaced by a newer connection")

        session = DesktopSession(connection=connection, subscription_id=subscription_id)
        self.sessions[desktop_id] = session
        self._connection_desktops[id(connection)] = desktop_id
        record.last_seen_at = utcnow_iso()
        record.updated_at = record.last_seen_at
        self.async_schedule_save()
        self._notify()

        @callback
        def unsubscribe() -> None:
            if self.sessions.get(desktop_id) is session:
                self._remove_session(desktop_id, session, "Desktop disconnected")

        return unsubscribe

    @callback
    def _remove_session(
        self, desktop_id: str, session: DesktopSession, reason: str
    ) -> None:
        if self.sessions.get(desktop_id) is session:
            self.sessions.pop(desktop_id, None)
        if self._connection_desktops.get(id(session.connection)) == desktop_id:
            self._connection_desktops.pop(id(session.connection), None)
        for future in tuple(session.pending.values()):
            if not future.done():
                future.set_exception(DesktopUnavailableError(reason))
        session.pending.clear()
        record = self.desktops.get(desktop_id)
        if record is not None:
            record.last_seen_at = utcnow_iso()
            record.updated_at = record.last_seen_at
            self.async_schedule_save()
        self._notify()

    def _assert_session(self, desktop_id: str, connection: Any) -> DesktopSession:
        session = self.sessions.get(desktop_id)
        if session is None or session.connection is not connection:
            raise DesktopUnavailableError("Desktop has no active session on this connection")
        return session

    @callback
    def async_report_state(
        self, desktop_id: str, *, connection: Any, state: dict[str, Any]
    ) -> DesktopRecord:
        """Apply a state patch from the desktop's active session."""
        self._assert_session(desktop_id, connection)
        record = self.desktops[desktop_id]
        record.apply_state(state)
        self.async_schedule_save()
        self._notify()
        return record

    @callback
    def async_acknowledge_command(
        self,
        desktop_id: str,
        *,
        connection: Any,
        command_id: str,
        status: str,
        error: str | None,
        state: dict[str, Any] | None,
    ) -> None:
        """Resolve a pending command after validating its active session."""
        session = self._assert_session(desktop_id, connection)
        if state is not None:
            self.desktops[desktop_id].apply_state(state)
            self.async_schedule_save()
            self._notify()
        future = session.pending.get(command_id)
        if future is None or future.done():
            return
        future.set_result(
            {
                "command_id": command_id,
                "status": status,
                "error": (error or "")[:512],
                "state": state or {},
            }
        )

    async def async_dispatch_command(
        self,
        desktop_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a typed command and wait for its durable acknowledgement."""
        session = self.sessions.get(desktop_id)
        if session is None:
            raise DesktopUnavailableError("Desktop is offline")

        command_id = str(uuid4())
        now = datetime.now(UTC)
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command_id,
            "action": action,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=COMMAND_EXPIRY_SECONDS)).isoformat(),
            "payload": payload or {},
        }
        future: asyncio.Future[dict[str, Any]] = self.hass.loop.create_future()
        session.pending[command_id] = future
        try:
            session.connection.send_event(session.subscription_id, event)
            acknowledgement = await asyncio.wait_for(
                future, timeout=COMMAND_TIMEOUT_SECONDS
            )
        except TimeoutError as err:
            raise DesktopCommandError("Desktop did not acknowledge the command in time") from err
        finally:
            session.pending.pop(command_id, None)

        if acknowledgement["status"] != "completed":
            raise DesktopCommandError(
                acknowledgement.get("error") or "Desktop reported that the command failed"
            )
        return acknowledgement

    async def async_remove_desktop(self, desktop_id: str) -> bool:
        """Remove a registered desktop and invalidate its live session."""
        record = self.desktops.pop(desktop_id, None)
        if record is None:
            return False
        session = self.sessions.get(desktop_id)
        if session is not None:
            self._remove_session(desktop_id, session, "Desktop registration was removed")
        await self.async_save()
        self._notify()
        return True

    async def async_shutdown(self) -> None:
        """Flush storage and fail pending commands during config-entry unload."""
        for desktop_id, session in tuple(self.sessions.items()):
            self._remove_session(desktop_id, session, "Integration was unloaded")
        if self._cancel_save is not None:
            await self.async_save()
        self._listeners.clear()

    def diagnostics(self) -> dict[str, Any]:
        """Return redacted integration diagnostics."""
        return {
            "protocol_version": PROTOCOL_VERSION,
            "registered_desktops": len(self.desktops),
            "online_desktops": len(self.sessions),
            "desktops": [
                record.as_public_dict(online=self.is_online(record.desktop_id))
                for record in self.desktops.values()
            ],
        }


def get_loaded_runtime(hass: HomeAssistant) -> HADesktopWidgetRuntime | None:
    """Return the singleton loaded runtime, if configured."""
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    runtimes: dict[str, HADesktopWidgetRuntime] = domain_data.get(DATA_RUNTIMES, {})
    return next(iter(runtimes.values()), None)
