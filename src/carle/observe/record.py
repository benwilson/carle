"""Turn a CodeResult into a reference edit through an injected writer (U4).

A confirmed result is applied to the canonical protocol-reference prose; an uncertain result
records a prose "uncertain" note (R8). The writer is a seam: the orchestrating agent edits
`docs/protocol-reference.md` at run time, tests pass a fake. A confirmed reading that disagrees
with a supplied prior entry requests an overwrite of that entry, not an append (R6). The frames
are always discarded afterward, even if the writer raises (R7).
"""

from __future__ import annotations

from collections.abc import Callable

from carle.observe.loop import CodeResult

#: writer(result, prior=<Observation|None>) applies a confirmed finding or an uncertain note to
#: the reference, overwriting `prior` when the confirmed reading differs from it.
Writer = Callable[..., None]

#: cleanup() discards the run's captured frames/scratch dir (typically CaptureResult.cleanup).
Cleanup = Callable[[], None]


def record_result(
    result: CodeResult,
    *,
    writer: Writer,
    prior: object | None = None,
    cleanup: Cleanup | None = None,
) -> None:
    """Apply `result` to the reference via `writer`, then always run `cleanup` (R7)."""
    try:
        writer(result, prior=prior)
    finally:
        if cleanup is not None:
            cleanup()
