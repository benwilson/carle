# Method

How findings in this repository are produced, so that someone with the same hardware can
reproduce them rather than take them on trust.

The order matters. The mobile app is read first because a decompiled command builder shows the
*structure* of a frame — field names, lengths, checksum routine — that a packet capture only
implies. The capture then confirms that the structure was read correctly. Working the other way
round means inferring structure from bytes, which is slower and produces guesses that look like
findings.

## 1. Obtain the Carle app

The app is `com.ihunuo.jtlrobot` on Google Play, published by iHunuo rather than by Ruko.

Ruko's own download center delegates to `https://d.ihunuo.com/app/psss` for both platforms.
That host refused connections from the United States when this was written (see
`official-docs/manifest.yaml`), so the practical route is to install from Play on an Android
device and pull the binary off it:

```bash
adb shell pm path com.ihunuo.jtlrobot
adb pull /data/app/<path-from-above>/base.apk carle.apk
```

Modern Play installs are usually **split APKs**, so `pm path` prints several lines — a
`base.apk` plus `split_config.*` entries for language and screen density. Pull all of them. The
protocol code lives in `base.apk`; the splits are resources and are worth keeping only so the
set can be reassembled.

Do not commit any of these. `.gitignore` excludes `*.apk` deliberately: the app is not ours to
redistribute, and a 20 MB binary in a protocol reference helps nobody.

## 2. Decompile

```bash
jadx --output-dir decompiled/ base.apk
```

Then look for the Bluetooth layer. Useful starting points:

```bash
grep -rn "BluetoothGattCharacteristic" decompiled/sources/
grep -rn "writeCharacteristic\|setCharacteristicNotification" decompiled/sources/
grep -rniE "0000[0-9a-f]{4}-0000-1000-8000-00805f9b34fb" decompiled/sources/
```

Two things are worth knowing before starting. First, iHunuo appears to be a whitelabel app
house — the same publisher ships `com.ihunuo.ykr_hn_2005a_tlw66`, a ThermoPro thermometer app —
so the Bluetooth layer is likely a shared library rather than robot-specific code. If it is,
the class names will look generic and the robot-specific part will be a thin command table on
top. Second, whether this reference should therefore document the *iHunuo framework* with the
1088 as its first profile, rather than the 1088 alone, is an open question that the decompile
settles. It is deliberately not decided in advance.

Record, for each frame you find, the class and method it came from. That goes in the entry's
`derivation` field and is what makes the finding reproducible.

## 3. Confirm against a packet capture

Static reading can be wrong. To check it:

1. On the Android device, enable **Developer options → Enable Bluetooth HCI snoop log**.
2. Toggle Bluetooth off and on so logging starts cleanly.
3. Drive the robot from the Carle app, exercising one capability at a time and noting the
   order.
4. Pull the log — its location varies by vendor and Android version:
   ```bash
   adb bugreport bugreport.zip     # snoop log is inside, most reliable across devices
   ```
5. Open it in Wireshark and filter on `btatt`.

Compare the bytes on the wire against the frame you derived. If they disagree, the static
reading was wrong, and the wire wins.

## 4. Send it to the robot

A frame is not documented until it has been sent to a real robot and the response
observed. This is the step that separates this reference from a plausible guess.

```bash
uv run carle scan
uv run carle send media_music --address <address-from-scan>
```

`send` builds the frame from the table, writes it to the control characteristic, listens
on the notify characteristic while it does, and records everything it sent to a log under
[`evidence/`](../evidence/). You do not write that log; the tool does.

Parameters come from repeated `--param`, range-checked against the table's declarations:

```bash
uv run carle send volume_set --param level=2 --address <address>
uv run carle send media_music --param index=3 --address <address>
```

That second one is the open question the decompile could not settle — whether the second
payload byte selects an individual track. The app always sends 0. If a non-zero value
changes which song plays, the twenty-four superseded media rows come back.

Two escape hatches, both deliberately outside the evidence chain:

```bash
uv run carle send media_music --dry-run          # print the frame, touch nothing
uv run carle send --raw "03 01" --family 0xB3 --address <address>
```

A dry run writes no log at all, and a raw send logs outside `evidence/`. Neither can
support a promotion.

## 5. Record what happened

```bash
uv run carle confirm media_music --behavior "Played a song and waved both arms"
uv run python scripts/generate_reference.py
uv run pytest
```

**Watch the clock, not just the robot.** It runs a pre-programmed idle routine on its own,
playing the same music and making the same movements a command does. Anything happening more
than a few seconds after your send is probably its own idea. Describe what you saw
immediately, and say so if the timing was loose — a wrong attribution here is worse than a
thin one, because it reads as protocol behaviour forever after.

`confirm` finds the real send log for that command, rebuilds the entry at the parameters that
log recorded, and refuses if they no longer produce the same frame — the observation described
a different command. On success it **appends an observation**: your behaviour description, the
parameter values that were actually sent, and the log they were sent from.

An entry carries a list, not a single observation. A parameterized command is one frame
spanning a whole space, so confirming it once describes a single point of that space — the
movement command has two dozen observations, one per joint and mode. Confirming again adds
another; it never overwrites what is already there. With more than one log to choose from,
`confirm` refuses to guess and makes you name the one you watched with `--log`, and it refuses
a log some observation already cites, because one send is one observation.

Commit the log alongside the table change. The invariant suite resolves that path on a
fresh checkout, so a promotion whose log is not committed fails for everyone else.

**`confirm` is convenience, not enforcement.** The invariant suite parses the cited log
itself and re-derives the same judgement from the committed files, so hand-editing an
entry to `confirmed` does not get past CI just because you skipped the CLI.

If you decode a frame but have no robot, stop at step 3 and record it as `decoded` with
its `derivation`. That is a real contribution and the honest label for it.

## Scope

This document describes how to talk to the robot. It is not a security assessment and makes no
recommendations about the device's security posture — see `CONTRIBUTING.md` for where that line
sits and why.
