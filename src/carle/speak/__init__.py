"""The cross-platform speak service — route rendered audio to the robot's speaker.

This package plays caller-supplied audio to the robot's Bluetooth speaker as an
explicitly targeted output device, without ever changing the host's default output
(R4, R5). It is designed cross-platform and validated on macOS (R8).

The audio backends (``sounddevice``/PortAudio and friends) live behind the optional
``carle[speak]`` extra and are imported lazily inside the factories, so this package
imports — and its tests collect — on a lean or headless runner without them (KTD9).
"""

from __future__ import annotations

from carle.speak.animate import RobotAnimation
from carle.speak.decode import (
    DecodeError,
    PcmBuffer,
    RawPcmFormat,
    decode_clip,
    stream_pcm_blocks,
)
from carle.speak.server import (
    AnimationHook,
    NoopAnimation,
    SpeakServer,
    SpeakService,
)
from carle.speak.sink import AudioSink, DeviceUnavailableError
from carle.speak.stream import Outcome, StreamPlayer

__all__ = [
    "AnimationHook",
    "AudioSink",
    "DecodeError",
    "DeviceUnavailableError",
    "NoopAnimation",
    "Outcome",
    "PcmBuffer",
    "RawPcmFormat",
    "RobotAnimation",
    "SpeakServer",
    "SpeakService",
    "StreamPlayer",
    "decode_clip",
    "stream_pcm_blocks",
]
