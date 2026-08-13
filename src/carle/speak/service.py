"""Compose and run the speak server from its parts (U6).

`carle speak-server` needs one place that wires the transport-free core
(`SpeakService`) to the loopback HTTP server (`SpeakServer`) and, when wanted, the
animation coordinator (`RobotAnimation`). `build_speak_server` is that place, kept out
of `cli.py` so the wiring is unit-testable without argparse: the service, server, and
animation factories are all injected, so a test passes fakes and asserts the device
name, daemon socket, and port thread through to the right component.

No audio backend is imported at module load. Importing `carle.speak` builds nothing heavy
— the `sounddevice`/PortAudio backends load lazily inside the sink and stream factories
only when a real playback runs (KTD9) — so importing this module never requires the
``carle[speak]`` extra. Because of that laziness, building and starting the server would
otherwise *succeed* without the extra and only fault on the first request; `carle
speak-server` calls `require_speak_backends()` at startup to turn that into a clean CLI
error instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from carle.speak.animate import RobotAnimation
from carle.speak.server import SpeakServer, SpeakService

if TYPE_CHECKING:
    from carle.speak.server import AnimationHook


def require_speak_backends() -> None:
    """Import the audio backends eagerly so a missing ``carle[speak]`` extra fails fast.

    The backends are otherwise lazy-imported deep inside playback (KTD9), so without this
    probe a lean install would print "speak server listening ..." and only fault as an
    opaque HTTP 500 on the first real request. Calling this once at `speak-server` startup
    raises `ImportError` up front, which `cli.py` turns into the one-line install hint.
    """
    import miniaudio  # noqa: F401, PLC0415 - eager probe; lazy everywhere else (KTD9)
    import sounddevice  # noqa: F401, PLC0415
    import soundfile  # noqa: F401, PLC0415
    import soxr  # noqa: F401, PLC0415


def build_speak_server(
    *,
    device_name: str,
    port: int,
    socket_path: Path | str | None,
    animate: bool = True,
    service_factory: Callable[..., SpeakService] = SpeakService,
    server_factory: Callable[..., SpeakServer] = SpeakServer,
    animation_factory: Callable[..., AnimationHook] = RobotAnimation,
) -> SpeakServer:
    """Compose sink + decode + stream + API + animation into a bound `SpeakServer`.

    When ``animate`` is true, a `RobotAnimation` is built with ``socket_path`` so it
    targets the running daemon, and wired into the service; when false, the service runs
    with no animation (its default no-op hook). The factories are injected so tests
    substitute fakes and assert what each received — no real audio device or socket.
    """
    animation = animation_factory(socket_path=socket_path) if animate else None
    service = service_factory(device_name=device_name, animation=animation)
    return server_factory(service, port=port)


def run_speak_server(server: SpeakServer) -> None:
    """Serve until interrupted, then shut down and release the socket cleanly.

    Ctrl-C (KeyboardInterrupt) breaks out of `serve_forever`; the ``finally`` then runs
    the stdlib shutdown/close pair so the loopback port is not left bound.
    """
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
