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

## 4. Confirm against the robot

A frame is not documented until it has been sent to a real robot and the response observed.
This is the step that separates this reference from a plausible guess.

```bash
uv run carle scan
uv run carle info --address <address>
```

`info` prints the peripheral's services and characteristics verbatim; that output is what the
Transport section of the protocol reference is written from.

Note there is currently no command that writes to the robot. Adding one is the first task once
the frame format is known — until then there is nothing to send.

## 5. Record the finding

Edit [`protocol/commands.yaml`](../protocol/commands.yaml) — never the generated table in the
reference document — then regenerate:

```bash
uv run python scripts/generate_reference.py
uv run pytest
```

Which fields an entry carries depends on how far it has been taken:

| You have | Set status to | And record |
|---|---|---|
| A published capability, nothing else | `unmapped` | nothing further |
| Searched the app, found no frame | `unlocated` | nothing further |
| A frame from the app, untested | `decoded` | `encoding`, `derivation` |
| A frame you sent and watched work | `confirmed` | `encoding`, `derivation`, `observed_behavior`, `hardware_evidence` |

`hardware_evidence` names a date, the platform you ran from, and a `log` path under
[`evidence/`](../evidence/). That file has to exist — the test suite resolves the path, so a
placeholder string will not pass. Put whatever you actually captured in it: a snoop-log
excerpt, terminal output, a note describing what the robot did.

Also set `provenance`. A row that exists because Ruko published a capability is
`vendor-marketing` and can never carry an encoding; a row backed by a frame from the app is
`decompile`. If the decompile shows a seeded capability was really several commands, or several
were really one, keep the original id and add `superseded_by` naming the replacements. Deleting
the row would make the table look more complete than it is.

## Scope

This document describes how to talk to the robot. It is not a security assessment and makes no
recommendations about the device's security posture — see `CONTRIBUTING.md` for where that line
sits and why.
