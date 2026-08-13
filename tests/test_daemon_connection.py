"""U1 — the held BLE connection, exercised entirely against a fake client.

No real Bluetooth is touched. The daemon connection talks to a `BleClient`; here that
is `FakeClient`, which records writes, answers a battery read, can simulate a link drop,
and asserts that two GATT operations never overlap (proving the connection serializes
them).
"""

from __future__ import annotations

import asyncio

import pytest

from carle import frame
from carle.daemon.connection import (
    BATTERY_CHARACTERISTIC,
    DaemonConnection,
    DaemonConnectionError,
)
from carle.transport import WRITE_CHARACTERISTIC

NOOP = frame.build(0xB6, [0, 0, 0, 0, 0, 0])


class FakeClient:
    """A stand-in BLE client. One per (re)connect, produced by the factory below."""

    def __init__(self, battery: int | None = 80) -> None:
        self.connected = False
        self.writes: list[tuple[str, bytes]] = []
        self._battery = battery  # None models a robot with no battery characteristic
        self.drop_on_write = False
        self.hang_on_write = False  # models a peripheral that never acks a write
        self._busy = False  # trips if a second GATT op starts before the first finishes

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def _guarded(self):
        assert not self._busy, "GATT operations interleaved on one client"
        self._busy = True
        await asyncio.sleep(0)  # a real yield point where interleaving could happen
        self._busy = False

    async def write(self, characteristic: str, data: bytes) -> None:
        if not self.connected:
            raise RuntimeError("not connected")
        if self.drop_on_write:
            self.connected = False
            raise RuntimeError("link dropped")
        if self.hang_on_write:
            await asyncio.Event().wait()  # never returns: a stalled ack
        await self._guarded()
        self.writes.append((characteristic, bytes(data)))

    async def read(self, characteristic: str) -> bytes | None:
        if not self.connected:
            return None
        await self._guarded()
        if characteristic == BATTERY_CHARACTERISTIC:
            return None if self._battery is None else bytes([self._battery])
        return None


def make_connection(battery: int | None = 80) -> tuple[DaemonConnection, list[FakeClient]]:
    clients: list[FakeClient] = []

    def factory(_address: str) -> FakeClient:
        client = FakeClient(battery=battery)
        clients.append(client)
        return client

    return DaemonConnection("AA:BB", client_factory=factory), clients


def test_send_frame_writes_to_the_control_characteristic():
    async def scenario():
        conn, clients = make_connection()
        await conn.connect()
        await conn.send_frame(NOOP)
        assert clients[-1].writes == [(WRITE_CHARACTERISTIC, NOOP)]
        assert conn.last_write is not None

    asyncio.run(scenario())


def test_send_on_dropped_link_raises_then_reconnects_and_lands():
    async def scenario():
        conn, clients = make_connection()
        await conn.connect()
        clients[-1].drop_on_write = True
        with pytest.raises(DaemonConnectionError):
            await conn.send_frame(NOOP)
        assert not conn.is_connected
        await conn._reconnect_task  # let the background reconnect finish
        assert conn.is_connected
        await conn.send_frame(NOOP)
        # The second client (from the reconnect) carries the write, not the dropped one.
        assert clients[-1].writes == [(WRITE_CHARACTERISTIC, NOOP)]
        assert len(clients) == 2

    asyncio.run(scenario())


def test_a_hung_write_times_out_and_drops_the_link(monkeypatch):
    # A peripheral that never acks a write must not wedge the tick loop forever with the
    # connection lock held. The bounded write turns the stall into a normal dropped link.
    monkeypatch.setattr("carle.daemon.connection.WRITE_TIMEOUT", 0.05)

    async def scenario():
        conn, clients = make_connection()
        await conn.connect()
        clients[-1].hang_on_write = True
        with pytest.raises(DaemonConnectionError):
            await conn.send_frame(NOOP)
        assert not conn.is_connected  # marked down and a reconnect scheduled
        assert conn._reconnect_task is not None

    asyncio.run(scenario())


def test_read_battery_returns_the_byte_when_present():
    async def scenario():
        conn, _ = make_connection(battery=73)
        await conn.connect()
        assert await conn.read_battery() == 73

    asyncio.run(scenario())


def test_read_battery_returns_none_when_absent():
    async def scenario():
        conn, _ = make_connection(battery=None)
        await conn.connect()
        assert await conn.read_battery() is None

    asyncio.run(scenario())


def test_read_battery_returns_none_when_disconnected():
    async def scenario():
        conn, _ = make_connection()
        assert await conn.read_battery() is None

    asyncio.run(scenario())


def test_concurrent_battery_read_and_write_do_not_interleave():
    async def scenario():
        conn, clients = make_connection()
        await conn.connect()
        # Fire a write and a read at once; the connection's lock must serialize them, so
        # the FakeClient's interleave guard never trips.
        await asyncio.gather(conn.send_frame(NOOP), conn.read_battery())
        assert clients[-1].writes == [(WRITE_CHARACTERISTIC, NOOP)]

    asyncio.run(scenario())


def test_close_during_a_reconnect_does_not_resurrect_the_link():
    # A dropped link schedules a background reconnect. If the daemon shuts down while that
    # reconnect is mid-connect, close() must cancel it and the link must stay down — a
    # stray reconnect finishing after teardown would hold a robot no one is driving.
    async def scenario():
        gate = asyncio.Event()  # a reconnect's connect() blocks here until the test frees it

        class GatedClient(FakeClient):
            async def connect(self) -> None:
                if self.connected is False and gate_used["armed"]:
                    await gate.wait()  # the reconnect stalls here, mid-connect
                self.connected = True

        gate_used = {"armed": False}
        clients: list[GatedClient] = []

        def factory(_address: str) -> GatedClient:
            client = GatedClient()
            clients.append(client)
            return client

        conn = DaemonConnection("AA:BB", client_factory=factory)
        await conn.connect()  # first client connects immediately
        gate_used["armed"] = True
        clients[-1].drop_on_write = True
        with pytest.raises(DaemonConnectionError):
            await conn.send_frame(NOOP)  # drops, schedules the (now-gated) reconnect
        await asyncio.sleep(0)  # let the reconnect task reach the gate
        await conn.close()  # cancels the in-flight reconnect and marks the link closed
        gate.set()  # even if the cancelled connect were to wake, it must not resurrect
        await asyncio.sleep(0)
        assert not conn.is_connected

    asyncio.run(scenario())


def test_is_connected_tracks_connect_and_drop():
    async def scenario():
        conn, clients = make_connection()
        assert not conn.is_connected
        await conn.connect()
        assert conn.is_connected
        clients[-1].drop_on_write = True
        with pytest.raises(DaemonConnectionError):
            await conn.send_frame(NOOP)
        assert not conn.is_connected
        await conn._reconnect_task
        await conn.close()

    asyncio.run(scenario())
