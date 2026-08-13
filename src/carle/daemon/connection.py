"""The held BLE connection — the daemon's single writer to the robot (U1).

`carle.transport` connects per call, which is right for one-shot `carle send` but wrong
for a daemon that must hold the link and heartbeat it. This wraps one client for the
daemon's lifetime: it sends frames, reads the battery characteristic, and reconnects on
a drop. The queue engine lives above it and survives a drop untouched (KTD2).

Two deliberate choices from the plan:

- **`send_frame` raises on a dropped link** while kicking off a background reconnect — it
  does not transparently retry (KTD2/KTD6). Surfacing the error is what lets the engine
  own step resume; a silent retry here would make the engine's reconnect handling and its
  AE6 test unreachable.
- **GATT operations are serialized** on the one client, so a status battery read and a
  tick write never overlap on the same connection.

The connection talks to a `BleClient` obtained from an injected factory, so the engine
and the tests never need a real Bluetooth stack — the same seam `carle.cli` uses for its
`Backend`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from carle.transport import WRITE_CHARACTERISTIC, chunked

#: Standard BLE Battery Service and Level characteristic — a readable robot state path
#: documented in docs/protocol-reference.md. The control service never reports state.
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC = "00002a19-0000-1000-8000-00805f9b34fb"

#: Seconds between reconnect attempts after a drop. The robot drops its link on its own
#: periodically, so a reconnect loop is the normal path, not an error path.
RECONNECT_BACKOFF = 1.5

#: Bound on a battery GATT read, so a hung read cannot stall a status request.
BATTERY_READ_TIMEOUT = 2.0

#: Bound on a single frame-chunk GATT write. Without it, a stalled write (a peripheral that
#: never acks) would hang the tick loop forever while holding the connection lock — freezing
#: the heartbeat, every queued command, and even a status read waiting on the same lock.
WRITE_TIMEOUT = 2.0


class DaemonConnectionError(Exception):
    """Raised by `send_frame`/`read_battery` when the link is down.

    Named to avoid shadowing the builtin `ConnectionError`; the engine catches it to
    trigger its per-step resume policy.
    """


@runtime_checkable
class BleClient(Protocol):
    """The slice of a BLE client the daemon needs. Bleak implements it; tests fake it."""

    @property
    def is_connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def write(self, characteristic: str, data: bytes) -> None: ...

    async def read(self, characteristic: str) -> bytes | None:
        """Read a characteristic, or return None when the peripheral does not expose it."""


#: A factory turns an address into a fresh client. The default builds a Bleak adapter;
#: tests inject one that returns a fake.
ClientFactory = Callable[[str], BleClient]


class BleakClientAdapter:
    """Adapts `bleak.BleakClient` to the `BleClient` protocol. Imports bleak lazily."""

    def __init__(self, address: str) -> None:
        self._address = address
        self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise DaemonConnectionError(f"bleak is not installed: {exc}") from exc
        self._client = BleakClient(self._address)
        await self._client.connect()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def write(self, characteristic: str, data: bytes) -> None:
        # response=False mirrors the app's write-without-response (see transport.py).
        await self._client.write_gatt_char(characteristic, data, response=False)

    async def read(self, characteristic: str) -> bytes | None:
        # The characteristic may be absent — return None rather than raising, so the
        # daemon degrades on a robot that does not expose the battery service.
        if self._client.services.get_characteristic(characteristic) is None:
            return None
        return bytes(await self._client.read_gatt_char(characteristic))


def _bleak_factory(address: str) -> BleClient:
    return BleakClientAdapter(address)


class DaemonConnection:
    """One held BLE connection with reconnect, frame writes, and a battery read."""

    def __init__(self, address: str, client_factory: ClientFactory = _bleak_factory) -> None:
        self._address = address
        self._factory = client_factory
        self._client: BleClient | None = None
        self._lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None
        self._closed = False
        self.last_write: float | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Establish the initial connection. Raises on failure."""
        client = self._factory(self._address)
        await client.connect()
        self._client = client

    async def send_frame(self, frame: bytes) -> None:
        """Write one frame, chunked. Raises `DaemonConnectionError` on a dropped link.

        On a drop this schedules a background reconnect and re-raises rather than
        retrying, so the caller (the engine) owns what happens to the interrupted step.
        """
        async with self._lock:
            if not self.is_connected:
                self._schedule_reconnect()
                raise DaemonConnectionError("link is down")
            try:
                for chunk in chunked(frame):
                    # Bound each chunk write (mirrors read_battery): a stalled ack must not
                    # wedge the tick loop with the connection lock held. A timeout is an
                    # Exception, so it flows into the same dropped-link handling below.
                    await asyncio.wait_for(
                        self._client.write(WRITE_CHARACTERISTIC, chunk), timeout=WRITE_TIMEOUT
                    )
            except Exception as exc:  # noqa: BLE001 - any BLE failure is a dropped link
                self._client = None
                self._schedule_reconnect()
                raise DaemonConnectionError(f"send failed: {type(exc).__name__}: {exc}") from exc
            self.last_write = time.monotonic()

    async def read_battery(self) -> int | None:
        """Return the battery percentage 0-100, or None when unavailable."""
        async with self._lock:
            if not self.is_connected:
                return None
            try:
                # Bound the GATT round-trip so a hung read cannot stall a status request.
                value = await asyncio.wait_for(
                    self._client.read(BATTERY_CHARACTERISTIC), timeout=BATTERY_READ_TIMEOUT
                )
            except Exception:  # noqa: BLE001 - a read failure is not fatal to the daemon
                return None
            return value[0] if value else None

    def _schedule_reconnect(self) -> None:
        """Start a background reconnect loop, unless one is already running or we're closed."""
        if self._closed:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        while not self._closed and not self.is_connected:
            try:
                client = self._factory(self._address)
                await client.connect()
                if self._closed:  # closed while connecting: don't resurrect the link
                    await client.disconnect()
                    return
                self._client = client
                return
            except Exception:  # noqa: BLE001 - keep retrying on the robot's own schedule
                await asyncio.sleep(RECONNECT_BACKOFF)

    async def close(self) -> None:
        """Disconnect and cancel any reconnect loop, awaiting it so it cannot reconnect."""
        self._closed = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
