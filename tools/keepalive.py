#!/usr/bin/env python3
"""DEPRECATED — superseded by the control-plane daemon (`carle daemon start`).

The daemon (`src/carle/daemon/`) now holds the link and heartbeats the no-op, and does
far more besides. This script is kept, not deleted, as the single-purpose experiment for
the first charged-robot session that validates the daemon's heartbeat against the idle
timer; once that live pass succeeds, this file goes.

Keep the Ruko 1088 awake — hold the BLE link and stream a NOOP so it never idles.

Left alone, the robot resumes its own idle routine (music, movement, speech) within a
second or two of silence. This holds one connection open and streams a no-op movement
frame just often enough to deny the idle routine its window, so the robot stays still
and under the control plane's authority until something is actually asked of it.

The NOOP is a `0xB6` movement frame with every motion byte zeroed —
`B6 06 00 00 00 00 00 00 00 AA` — so it commands no motion at all: no servo turns, no
strain, it only keeps the channel warm. The robot drops its link on its own from time
to time, so this reconnects and resumes rather than exiting.

    python3 tools/keepalive.py <address> [--interval 1.0]

Stop it with ctrl-c, or kill the process. NOTE: not yet run against hardware — the
robot was off when this was written. Verify the interval actually beats the idle timer
on a live robot and adjust if needed.
"""

import argparse
import asyncio
import sys

sys.path.insert(0, "src")
from carle import frame  # noqa: E402
from carle.transport import WRITE_CHARACTERISTIC  # noqa: E402

# All motion bytes zero: mode, speed, direction, waist, limb, p5.
NOOP = frame.build(0xB6, [0, 0, 0, 0, 0, 0])

# The idle routine resumes within ~1–2 s of silence, so a beat around 1 s beats it with
# margin. The frame moves nothing, so a faster beat costs nothing but radio traffic.
DEFAULT_INTERVAL = 1.0

_beats = 0


async def session(address, interval):
    from bleak import BleakClient

    global _beats
    async with BleakClient(address, timeout=20.0) as client:
        print(f"connected to {address} — holding awake (NOOP every {interval:g}s).", flush=True)
        while True:
            await client.write_gatt_char(WRITE_CHARACTERISTIC, NOOP, response=False)
            _beats += 1
            if _beats % 30 == 0:
                print(f"  …{_beats} keep-alive beats", flush=True)
            await asyncio.sleep(interval)


async def main(address, interval):
    while True:
        try:
            await session(address, interval)
        except Exception as exc:  # noqa: BLE001 - any drop: reconnect and resume
            print(f"  link dropped ({type(exc).__name__}); reconnecting…", flush=True)
            await asyncio.sleep(1.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Keep the robot awake with a NOOP heartbeat.")
    parser.add_argument("address", help="peripheral address from `carle scan`")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between NOOP frames (default {DEFAULT_INTERVAL:g})",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.address, args.interval))
    except KeyboardInterrupt:
        print(f"stopped after {_beats} beats.")
