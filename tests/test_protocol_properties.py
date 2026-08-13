"""Property-based coverage for the daemon control protocol — the socket wire language.

The daemon reads these requests straight off a Unix socket, one JSON line each, from the CLI
and the MCP server. A malformed request must always become a `ProtocolError` the client sees,
never an unhandled exception that kills the request handler — so this fuzzes `loads` and
`parse_steps` to prove they are total on the error path across the whole input space, not just
the shapes `test_daemon_*` hand-picks.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from carle.daemon import protocol

# JSON-shaped values: the recursive structures a real (or hostile) client could send.
json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(), children, max_size=5)
    ),
    max_leaves=15,
)

# The keys a step item is actually inspected for, so the fuzzer hits real parse branches.
step_keys = st.sampled_from(
    [
        "move",
        "pose",
        "waist",
        "travel",
        "gesture",
        "face",
        "pause",
        "say",
        "media",
        "hold",
        "mode",
        "sub",
        "index",
        "direction",
        "speed",
    ]
)


@given(obj=st.dictionaries(st.text(), json_values, max_size=8))
def test_dumps_then_loads_round_trips(obj: dict) -> None:
    assert protocol.loads(protocol.dumps(obj)) == obj


@given(data=st.binary(max_size=200))
def test_loads_is_total_on_arbitrary_bytes(data: bytes) -> None:
    try:
        result = protocol.loads(data)
    except protocol.ProtocolError:
        return
    assert isinstance(result, dict)  # loads only ever returns a dict or raises ProtocolError


@given(items=st.lists(json_values, max_size=8))
def test_parse_steps_is_total_on_arbitrary_item_lists(items: list) -> None:
    try:
        protocol.parse_steps(items)
    except protocol.ProtocolError:
        return
    # A successful parse means every element was a well-formed step dict; nothing else escapes.


@given(
    non_list=st.one_of(
        st.none(), st.integers(), st.text(), st.dictionaries(st.text(), st.integers())
    )
)
def test_parse_steps_rejects_a_non_list_cleanly(non_list: object) -> None:
    try:
        protocol.parse_steps(non_list)  # type: ignore[arg-type]
    except protocol.ProtocolError:
        return
    raise AssertionError(f"parse_steps accepted a non-list {non_list!r} without ProtocolError")


@given(item=st.dictionaries(step_keys, json_values, max_size=5))
def test_a_single_step_item_never_crashes_the_parser(item: dict) -> None:
    # Item dicts built from the real step keys with arbitrary values exercise every branch of
    # _parse_item (int()/float()/nested-dict access) — all must resolve to steps or ProtocolError.
    try:
        protocol.parse_steps([item])
    except protocol.ProtocolError:
        return
