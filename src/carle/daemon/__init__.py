"""The always-on control plane: a daemon that owns the robot's BLE link.

One process holds the single BLE connection, runs a timed multi-channel command
queue, and heartbeats a no-op frame so the robot's idle routine never gets its window
while the link is held. Two thin clients drive it over a Unix-socket JSON protocol — a
CLI (in `carle.cli`) and an MCP server (`carle.daemon.mcp_server`).

Nothing here has been run against a robot; the engine is exercised entirely against
fakes. See docs/plans/2026-08-12-004-feat-robot-control-plane-plan.md and
docs/movement-vocabulary.md.
"""
