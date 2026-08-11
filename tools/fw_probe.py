#!/usr/bin/env python3
"""Fetch and dissect Ruko 1088 / iHunuo OTA firmware.

Run this from a network that can reach d.ihunuo.com (the vendor server is in China and
times out from many places). It does two jobs:

  1. Query the manifest endpoint the app uses and, if it returns a download link, pull
     the .bin.
  2. Parse whatever .bin you point it at, using the image header we reverse-engineered
     from OTAClient.otaLoadFMImageFromFile(), then run cheap "what is this" heuristics
     (entropy, strings, chip fingerprints).

Nothing here writes into the git repo — firmware is vendor-copyrighted, keep it local.

Usage:
    python3 fw_probe.py fetch  <appID>            # try the manifest, download if present
    python3 fw_probe.py probe  <appID>            # just print the manifest JSON, no download
    python3 fw_probe.py header <path-to.bin>      # parse the 12-byte image header
    python3 fw_probe.py analyze <path-to.bin>     # header + entropy + strings + chip guess
"""

import json
import struct
import sys
import urllib.request

PACKAGE = "com.ihunuo.jtlrobot"
# The app builds: http://d.ihunuo.com/api/v2/ota/{appID}?android-package-name={package}
MANIFEST = "http://d.ihunuo.com/api/v2/ota/{app_id}?android-package-name=" + PACKAGE
# A Dalvik UA in case the server filters on it.
UA = "Dalvik/2.1.0 (Linux; U; Android 11; Carle Build/RQ3A.211001)"


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def probe(app_id):
    url = MANIFEST.format(app_id=app_id)
    print(f"GET {url}")
    body = _get(url)
    print(f"  {len(body)} bytes")
    try:
        doc = json.loads(body)
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return doc
    except json.JSONDecodeError:
        print(body[:1000].decode("utf-8", "replace"))
        return None


def fetch(app_id, out="ota.bin"):
    doc = probe(app_id)
    if not doc:
        print("no JSON manifest; nothing to download")
        return
    # The Gson model in OTAUpdate names this otaDownloadLink; be lenient about casing.
    link = None
    for key in ("otaDownloadLink", "ota_download_link", "downloadLink", "url"):
        link = _dig(doc, key)
        if link:
            break
    if not link:
        print("manifest has no download link field; keys seen:", _keys(doc))
        return
    print(f"downloading {link}")
    data = _get(link, timeout=120)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out} ({len(data)} bytes)")
    header(out)


def _dig(doc, key):
    if isinstance(doc, dict):
        if key in doc:
            return doc[key]
        for v in doc.values():
            found = _dig(v, key)
            if found:
                return found
    return None


def _keys(doc, prefix=""):
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.append(prefix + k)
            out += _keys(v, prefix + k + ".")
    return out


def header(path):
    """The header OTAClient reads: 12 bytes, little-endian, then data at offset 28.

        offset(u16) signature(u16) version(u16) checksum(u16) length(u16) otaFlag(u8) reserved(u8)

    length is in 4-byte words, so image data size = length * 4. otaStartDFU copies the 16
    bytes at file offset 12..27 into the START_DFU command.
    """
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 28:
        print(f"{path}: only {len(blob)} bytes, too short to hold a header")
        return
    offset, signature, version, checksum, length, ota_flag, reserved = struct.unpack_from(
        "<HHHHHBB", blob, 0
    )
    print(f"file size        {len(blob)} bytes")
    print(f"header.offset    0x{offset:04X}")
    print(f"header.signature 0x{signature:04X}")
    print(f"header.version   {version}  (0x{version:04X})")
    print(f"header.checksum  0x{checksum:04X}")
    print(f"header.length    {length} words = {length * 4} bytes of image data")
    print(f"header.otaFlag   0x{ota_flag:02X}")
    print(f"header.reserved  0x{reserved:02X}")
    print(f"bytes 12..27 (START_DFU payload): {blob[12:28].hex(' ')}")
    body = blob[28:]
    print(f"data region      {len(body)} bytes (header says {length * 4})")
    return blob


def analyze(path):
    blob = header(path)
    if not blob:
        return
    body = blob[28:]
    # Shannon entropy over the data region — ~8.0 means encrypted/compressed, ~4-6 raw code.
    from collections import Counter
    from math import log2

    counts = Counter(body)
    n = len(body) or 1
    entropy = -sum((c / n) * log2(c / n) for c in counts.values())
    print(f"\ndata entropy     {entropy:.2f} bits/byte", end="  ")
    if entropy > 7.5:
        print("→ looks encrypted or compressed")
    elif entropy > 6.5:
        print("→ mixed; maybe packed")
    else:
        print("→ looks like plain code/data (reversible)")

    # Printable ASCII runs of length >= 5.
    runs, cur = [], bytearray()
    for byte in blob:
        if 0x20 <= byte < 0x7F:
            cur.append(byte)
        else:
            if len(cur) >= 5:
                runs.append(cur.decode("ascii"))
            cur = bytearray()
    print(f"\nprintable strings (>=5 chars): {len(runs)}")
    for s in runs[:60]:
        print("  ", s)

    # Chip fingerprints — Realtek BLE (the DFU UUIDs pointed here), Nordic, etc.
    lower = b"".join(r.encode() for r in runs).lower()
    for needle, who in [
        (b"realtek", "Realtek"),
        (b"rtl87", "Realtek RTL87xx BLE SoC"),
        (b"bee", "Realtek BEE (RTL8762) SDK"),
        (b"nordic", "Nordic"),
        (b"nrf5", "Nordic nRF5"),
        (b"telink", "Telink"),
        (b"cortex", "ARM Cortex"),
    ]:
        if needle in lower:
            print(f"\nchip hint: found {needle!r} → {who}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, arg = sys.argv[1], sys.argv[2]
    {"fetch": fetch, "probe": probe, "header": header, "analyze": analyze}[cmd](arg)
