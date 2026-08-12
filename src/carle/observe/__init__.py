"""The autonomous movement-observation loop (docs/plans — observe loop).

A camera-in-the-loop harness that derives what each robot movement command does:
it drives one code through the daemon, records a short clip from a webcam, and reads
the motion from the frames. The vision judgment and the reference-prose edit are
injected seams the orchestrating agent fulfils at run time; every committed test uses
fakes, so nothing here touches a camera, a robot, or BLE in CI.
"""
