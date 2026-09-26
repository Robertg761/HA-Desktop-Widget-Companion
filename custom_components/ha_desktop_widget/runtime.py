"""Runtime coordinator for registered HA Desktop Widget clients."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import (
    CAPABILITY_APPLY_PROFILE,
    COMMAND_APPLY_PROFILE,
    COMMAND_EXPIRY_SECONDS,
    COMMAND_TIMEOUT_SECONDS,
    DATA_RUNTIMES,
    DOMAIN,
    MAX_PROFILES,
    MAX_SNAPSHOTS,
    MAX_SNAPSHOTS_PER_OWNER,
    PERSIST_DEBOUNCE_SECONDS,
    PROFILE_SCHEMA_VERSION,
    PROFILE_STORE_VERSION,
    PROTOCOL_VERSION,
    STORE_KEY_PREFIX,
    STORE_VERSION,
)
from .models import (
    DesktopRecord,
    ProfileDocumentError,
    ProfileRecord,
    same_document,
    utcnow_iso,
    validate_profile_document,
)

_LOGGER = logging.getLogger(__name__)


class DesktopUnavailableError(HomeAssistantError):
    """Raised when a desktop has no active command subscription."""


class DesktopOwnershipError(HomeAssistantError):
    """Raised when a user tries to operate another user's desktop."""


class DesktopCommandError(HomeAssistantError):
    """Raised when a desktop rejects or fails a command."""


class ProfileError(HomeAssistantError):
    """Raised when a profile operation is invalid."""


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
        # Profiles and layout snapshots are larger and change rarely, so they live in their own
        # store instead of being rewritten with every debounced heartbeat save.
        self.profile_store: Store[dict[str, Any]] = Store(
            hass, PROFILE_STORE_VERSION, f"{STORE_KEY_PREFIX}.{entry_id}.profiles"
        )
        self.desktops: dict[str, DesktopRecord] = {}
        self.sessions: dict[str, DesktopSession] = {}
        self.profiles: dict[str, ProfileRecord] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self._connection_desktops: dict[int, str] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._cancel_save: CALLBACK_TYPE | None = None
        # Profile pushes to one desktop are serialized so they cannot complete out of order.
        self._sync_locks: dict[str, asyncio.Lock] = {}
        self._sync_scheduled: set[str] = set()
        self._sync_failures: dict[str, tuple[str, int]] = {}
        # Profiles being deleted: they can no longer be assigned.
        self._deleting_profiles: set[str] = set()
        self._sync_tasks: set[asyncio.Task[None]] = set()

    async def async_load(self) -> None:
        """Load registered desktops and start them in an offline state."""
        stored = await self.store.async_load() or {}
        raw_desktops = stored.get("desktops", {})
        if isinstance(raw_desktops, dict):
            for desktop_id, raw_record in raw_desktops.items():
                if not isinstance(desktop_id, str) or not isinstance(raw_record, dict):
                    continue
                record = DesktopRecord.from_storage(
                    {**raw_record, "desktop_id": raw_record.get("desktop_id", desktop_id)}
                )
                if record.desktop_id != "invalid":
                    self.desktops[record.desktop_id] = record
        await self._async_load_profiles()

    async def _async_load_profiles(self) -> None:
        stored = await self.profile_store.async_load() or {}
        raw_profiles = stored.get("profiles", {})
        if isinstance(raw_profiles, dict):
            for raw_profile in raw_profiles.values():
                if not isinstance(raw_profile, dict):
                    continue
                profile = ProfileRecord.from_storage(raw_profile)
                if profile is not None:
                    self.profiles[profile.profile_id] = profile
        raw_snapshots = stored.get("snapshots", {})
        if isinstance(raw_snapshots, dict):
            for desktop_id, raw_snapshot in raw_snapshots.items():
                if desktop_id not in self.desktops or not isinstance(raw_snapshot, dict):
                    continue
                try:
                    document = validate_profile_document(raw_snapshot.get("document"))
                except ProfileDocumentError:
                    continue
                updated_at = raw_snapshot.get("updated_at")
                self.snapshots[desktop_id] = {
                    "document": document,
                    "updated_at": updated_at if isinstance(updated_at, str) else utcnow_iso(),
                }
        for record in self.desktops.values():
            if record.assigned_profile_id not in self.profiles:
                record.assigned_profile_id = None

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

    async def async_save_profiles(self) -> None:
        """Persist profiles and desktop layout snapshots."""
        await self.profile_store.async_save(
            {
                "profiles": {
                    profile_id: profile.as_storage_dict()
                    for profile_id, profile in self.profiles.items()
                },
                "snapshots": self.snapshots,
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
        self._async_update_device(record)
        await self.async_save()
        self._notify()
        return record

    @callback
    def _async_update_device(self, record: DesktopRecord) -> None:
        """Keep the HA device in step with metadata refreshed on re-registration."""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, record.desktop_id)})
        if device is None:
            return
        registry.async_update_device(
            device.id,
            name=record.name,
            model=record.platform.title(),
            sw_version=record.app_version,
        )

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
        # A new session may be a restarted or upgraded desktop, so retry a failed profile sync.
        self._sync_failures.pop(desktop_id, None)
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
        self.async_request_profile_sync(desktop_id)
        return record

    async def async_put_config_snapshot(
        self, desktop_id: str, *, connection: Any, document: Any
    ) -> None:
        """Store the desktop's current shareable layout reported by its active session."""
        self._assert_session(desktop_id, connection)
        validated = validate_profile_document(document)
        current = self.snapshots.get(desktop_id)
        if current is not None and same_document(current["document"], validated):
            return
        if current is None:
            self._make_room_for_snapshot(self.desktops[desktop_id].owner_user_id)
        self.snapshots[desktop_id] = {"document": validated, "updated_at": utcnow_iso()}
        await self.async_save_profiles()
        self._notify()

    def _make_room_for_snapshot(self, owner_user_id: str) -> None:
        """Keep snapshot storage bounded however many desktop IDs are registered.

        A user's own oldest snapshot makes way for their newest one, so no user can evict
        another's; past the global limit new snapshots are refused instead.
        """
        owned = sorted(
            (
                (snapshot["updated_at"], desktop_id)
                for desktop_id, snapshot in self.snapshots.items()
                if (record := self.desktops.get(desktop_id)) is not None
                and record.owner_user_id == owner_user_id
            ),
        )
        while len(owned) >= MAX_SNAPSHOTS_PER_OWNER:
            _, oldest = owned.pop(0)
            self.snapshots.pop(oldest, None)
        if len(self.snapshots) >= MAX_SNAPSHOTS:
            raise ProfileError(
                f"Home Assistant already stores the maximum of {MAX_SNAPSHOTS} desktop layouts"
            )

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
            self.async_request_profile_sync(desktop_id)
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
        self._sync_failures.pop(desktop_id, None)
        self._sync_locks.pop(desktop_id, None)
        await self.async_save()
        if self.snapshots.pop(desktop_id, None) is not None:
            await self.async_save_profiles()
        self._notify()
        return True

    # Profiles --------------------------------------------------------------------------------

    def find_profile(self, reference: str) -> ProfileRecord | None:
        """Resolve a profile by ID or case-insensitive name."""
        return self.profiles.get(reference) or self._find_profile_by_name(reference)

    def _find_profile_by_name(self, name: str) -> ProfileRecord | None:
        folded = name.strip().casefold()
        return next(
            (profile for profile in self.profiles.values() if profile.name.casefold() == folded),
            None,
        )

    def require_profile(self, reference: str) -> ProfileRecord:
        """Resolve a profile or raise a user-facing error."""
        profile = self.find_profile(reference)
        if profile is None:
            raise ProfileError(f"Profile {reference} was not found")
        return profile

    def get_snapshot(self, desktop_id: str) -> dict[str, Any] | None:
        """Return the desktop's last reported layout snapshot."""
        return self.snapshots.get(desktop_id)

    async def async_save_profile(
        self,
        *,
        name: str,
        document: Any,
        profile_id: str | None = None,
    ) -> ProfileRecord:
        """Create a profile, or update one and bump its revision when its document changes."""
        clean_name = name.strip()[:64]
        if not clean_name:
            raise ProfileError("Profile name must not be empty")
        try:
            validated = validate_profile_document(document)
        except ProfileDocumentError as err:
            raise ProfileError(str(err)) from err

        existing = self.profiles.get(profile_id) if profile_id else None
        if profile_id and existing is None:
            raise ProfileError(f"Profile {profile_id} was not found")
        clash = self._find_profile_by_name(clean_name)
        if clash is not None and clash is not existing:
            raise ProfileError(f"A profile named {clash.name} already exists")
        # Actions accept a profile's name or ID, so a name must never be another profile's ID.
        if any(
            profile_id.casefold() == clean_name.casefold()
            for profile_id, profile in self.profiles.items()
            if profile is not existing
        ):
            raise ProfileError(f"{clean_name} is reserved as another profile's ID")

        if existing is None:
            if len(self.profiles) >= MAX_PROFILES:
                raise ProfileError(f"At most {MAX_PROFILES} profiles can be stored")
            profile = ProfileRecord(
                profile_id=self._new_profile_id(), name=clean_name, document=validated
            )
            self.profiles[profile.profile_id] = profile
        else:
            profile = existing
            if not same_document(profile.document, validated):
                profile.document = validated
                profile.revision += 1
            profile.name = clean_name
            profile.updated_at = utcnow_iso()

        await self.async_save_profiles()
        self._notify()
        for desktop_id, record in self.desktops.items():
            if record.assigned_profile_id == profile.profile_id:
                self.async_request_profile_sync(desktop_id)
        return profile

    def _new_profile_id(self) -> str:
        while True:
            profile_id = uuid4().hex
            if profile_id not in self.profiles and self._find_profile_by_name(profile_id) is None:
                return profile_id

    async def async_capture_profile(self, desktop_id: str, *, name: str) -> ProfileRecord:
        """Save a desktop's reported layout as a profile, updating a same-named profile."""
        snapshot = self.snapshots.get(desktop_id)
        if snapshot is None:
            raise ProfileError(
                "This desktop has not reported its layout yet; connect it and try again"
            )
        existing = self._find_profile_by_name(name)
        return await self.async_save_profile(
            name=name,
            document=snapshot["document"],
            profile_id=existing.profile_id if existing else None,
        )

    async def async_delete_profile(self, profile_id: str) -> bool:
        """Delete a profile and clear assignments to it; desktops keep their applied layout.

        Waits for pushes already on their way to the assigned desktops, so none of them changes
        after the deletion returns.
        """
        if profile_id not in self.profiles or profile_id in self._deleting_profiles:
            return False
        # Refuse new assignments first, then hold every assigned desktop's sync lock. Assignments
        # already past their check can still land while this waits, so keep re-checking.
        self._deleting_profiles.add(profile_id)
        try:
            async with contextlib.AsyncExitStack() as stack:
                locked: set[str] = set()
                while pending := sorted(
                    desktop_id
                    for desktop_id, record in self.desktops.items()
                    if record.assigned_profile_id == profile_id and desktop_id not in locked
                ):
                    for desktop_id in pending:
                        await stack.enter_async_context(self._sync_lock(desktop_id))
                        locked.add(desktop_id)
                if self.profiles.pop(profile_id, None) is None:
                    return False
                cleared = False
                for desktop_id, record in self.desktops.items():
                    if record.assigned_profile_id == profile_id:
                        record.assigned_profile_id = None
                        self._sync_failures.pop(desktop_id, None)
                        cleared = True
                await self.async_save_profiles()
                if cleared:
                    await self.async_save()
        finally:
            self._deleting_profiles.discard(profile_id)
        self._notify()
        return True

    async def async_assign_profile(self, desktop_id: str, profile_id: str | None) -> None:
        """Set the profile a desktop should converge to, or clear its assignment.

        Waits for a push already on its way to the desktop, so the desktop does not change
        after an unassignment returns.
        """
        async with self._sync_lock(desktop_id):
            record = self.desktops.get(desktop_id)
            if record is None:
                raise DesktopUnavailableError("Desktop is not registered")
            if profile_id is not None:
                if profile_id not in self.profiles or profile_id in self._deleting_profiles:
                    raise ProfileError(f"Profile {profile_id} was not found")
                if CAPABILITY_APPLY_PROFILE not in record.capabilities:
                    raise ProfileError(f"{record.name} does not support profiles")
            if record.assigned_profile_id == profile_id:
                return
            record.assigned_profile_id = profile_id
            self._sync_failures.pop(desktop_id, None)
            await self.async_save()
        self._notify()

    def assigned_profile(self, desktop_id: str) -> ProfileRecord | None:
        """Return the profile assigned to a desktop, if any."""
        record = self.desktops.get(desktop_id)
        if record is None or record.assigned_profile_id is None:
            return None
        return self.profiles.get(record.assigned_profile_id)

    def profile_out_of_date(self, desktop_id: str) -> bool:
        """Return whether a desktop has not applied the current revision of its profile."""
        record = self.desktops.get(desktop_id)
        profile = self.assigned_profile(desktop_id)
        if record is None or profile is None:
            return False
        return (
            record.active_profile_id != profile.profile_id
            or record.profile_revision != profile.revision
        )

    async def async_apply_profile(
        self, desktop_id: str, profile: ProfileRecord
    ) -> dict[str, Any]:
        """Push a profile revision to an online desktop and wait for its acknowledgement."""
        if not self.supports(desktop_id, CAPABILITY_APPLY_PROFILE):
            raise ProfileError("Desktop does not support profiles")
        return await self.async_dispatch_command(
            desktop_id,
            COMMAND_APPLY_PROFILE,
            {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "profile_id": profile.profile_id,
                "revision": profile.revision,
                "profile": profile.document,
            },
        )

    def _sync_target(self, desktop_id: str) -> tuple[str, int] | None:
        profile = self.assigned_profile(desktop_id)
        return (profile.profile_id, profile.revision) if profile else None

    def _sync_lock(self, desktop_id: str) -> asyncio.Lock:
        return self._sync_locks.setdefault(desktop_id, asyncio.Lock())

    async def _async_push_assigned(self, desktop_id: str, *, force: bool) -> bool:
        """Push the assigned profile under the desktop's sync lock.

        Drift is re-checked once the lock is held, because an earlier push may have finished
        the job. A revision that fails, or that the desktop acknowledges without reporting it
        applied, is remembered so it is not pushed again on every heartbeat.
        """
        async with self._sync_lock(desktop_id):
            profile = self.assigned_profile(desktop_id)
            if profile is None or not (force or self.profile_out_of_date(desktop_id)):
                return False
            target = (profile.profile_id, profile.revision)
            session = self.sessions.get(desktop_id)
            try:
                await self.async_apply_profile(desktop_id, profile)
            except DesktopUnavailableError:
                raise
            except HomeAssistantError:
                # A replacement session gets its own retry, so only remember failures from
                # the session that is still live.
                if self.sessions.get(desktop_id) is session:
                    self._sync_failures[desktop_id] = target
                raise
            if self.sessions.get(desktop_id) is not session:
                return True
            if self._sync_target(desktop_id) == target and self.profile_out_of_date(desktop_id):
                self._sync_failures[desktop_id] = target
                _LOGGER.warning(
                    "Desktop %s acknowledged profile %s revision %s but did not report applying it",
                    desktop_id,
                    profile.name,
                    profile.revision,
                )
            else:
                self._sync_failures.pop(desktop_id, None)
            return True

    async def async_sync_profile(self, desktop_id: str, *, force: bool = False) -> bool:
        """Push the assigned profile when the desktop is behind it, or always when forced.

        Waits for any push already in flight. Returns whether a push was acknowledged; errors
        propagate to the caller.
        """
        try:
            return await self._async_push_assigned(desktop_id, force=force)
        finally:
            # The assignment or revision may have changed while this push was in flight.
            self.async_request_profile_sync(desktop_id)

    @callback
    def async_request_profile_sync(self, desktop_id: str) -> None:
        """Converge an online desktop to its assigned profile revision in the background."""
        target = self._sync_target(desktop_id)
        if (
            target is None
            or desktop_id in self._sync_scheduled
            or self._sync_lock(desktop_id).locked()
            or not self.is_online(desktop_id)
            or not self.supports(desktop_id, CAPABILITY_APPLY_PROFILE)
            or not self.profile_out_of_date(desktop_id)
            or self._sync_failures.get(desktop_id) == target
        ):
            return
        self._sync_scheduled.add(desktop_id)
        task = self.hass.async_create_background_task(
            self._async_background_sync(desktop_id),
            f"{DOMAIN} profile sync {desktop_id}",
        )
        self._sync_tasks.add(task)
        task.add_done_callback(self._sync_tasks.discard)

    async def _async_background_sync(self, desktop_id: str) -> None:
        try:
            await self._async_push_assigned(desktop_id, force=False)
        except DesktopUnavailableError:
            pass
        except HomeAssistantError as err:
            _LOGGER.warning("Could not apply a profile to desktop %s: %s", desktop_id, err)
        finally:
            self._sync_scheduled.discard(desktop_id)
        # Follow a revision or assignment that changed while the push was in flight.
        self.async_request_profile_sync(desktop_id)

    async def async_shutdown(self) -> None:
        """Flush storage and fail pending commands during config-entry unload."""
        for desktop_id, session in tuple(self.sessions.items()):
            self._remove_session(desktop_id, session, "Integration was unloaded")
        for task in tuple(self._sync_tasks):
            task.cancel()
        if self._sync_tasks:
            await asyncio.gather(*self._sync_tasks, return_exceptions=True)
        if self._cancel_save is not None:
            await self.async_save()
        self._listeners.clear()

    def desktop_summary(self, record: DesktopRecord) -> dict[str, Any]:
        """Return a desktop's public state together with its profile status."""
        data = record.as_public_dict(online=self.is_online(record.desktop_id))
        data["profile_out_of_date"] = self.profile_out_of_date(record.desktop_id)
        data["has_snapshot"] = record.desktop_id in self.snapshots
        return data

    def diagnostics(self) -> dict[str, Any]:
        """Return redacted integration diagnostics."""
        return {
            "protocol_version": PROTOCOL_VERSION,
            "registered_desktops": len(self.desktops),
            "online_desktops": len(self.sessions),
            "desktops": [self.desktop_summary(record) for record in self.desktops.values()],
            # Profile and snapshot contents can name every entity on a dashboard; report shape only.
            "profiles": [profile.as_summary_dict() for profile in self.profiles.values()],
        }


def get_loaded_runtime(hass: HomeAssistant) -> HADesktopWidgetRuntime | None:
    """Return the singleton loaded runtime, if configured."""
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    runtimes: dict[str, HADesktopWidgetRuntime] = domain_data.get(DATA_RUNTIMES, {})
    return next(iter(runtimes.values()), None)
