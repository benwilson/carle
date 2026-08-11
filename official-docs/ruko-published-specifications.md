# Ruko 1088 — published specifications

Values transcribed from Ruko's own specification pages on 2026-08-11. This is an
**extracted** capture: the numbers below are the vendor's, the formatting is not. The pages
themselves were not archived byte-for-byte.

Sources:

- <https://www.rukotoy.com/params/toys/ruko-1088-blue>
- <https://www.rukotoy.com/params/toys/ruko-1088-gold>
- <https://www.ruko.net/products/ruko-1088-large-smart-robots-for-kids>
- <https://rukotoy.com/download-center/apps/carle>

## Hardware

| Field | Value |
|---|---|
| Product size | 10.6 × 5.1 × 15.7 in (L×W×H) |
| Package size | 12.2 × 5.6 × 17.1 in |
| Product weight | 1.28 kg |
| Package weight | 1.82 kg |
| Battery | 3.7 V 600 mAh, internal |
| Play time | 100 minutes |
| Charge time | 3 hours at 5 V / 2 A |
| Motors | 9 motor drives |
| Suitable age | 4–9 years |

The Gold specification page carries identical values. All four colours — Blue, Green, Gold,
Pink — publish the same specifications; colour is the only differing field.

Note a documentation conflict in the vendor's own material: the user manual states roughly
2 hours charge and 2 hours play, while the specification pages state 3 hours charge and 100
minutes play. Ruko has not reconciled these.

## Control channels

| Field | Value |
|---|---|
| Remote signal | 2.4 GHz |
| Remote range | 65 ft |
| Remote batteries | 2 × 1.5 V AA (not included) |
| App | "Carle", Android 4.3+ / iOS 8.0+ |
| App transport | Bluetooth; device advertises as `JT_XXXX` |
| Programmable commands | 50 by remote, 200 by app |

## Capability counts

These are the counts `protocol/commands.yaml` is seeded from.

| Capability | Count |
|---|---|
| Songs | 10 |
| Dance tracks | 8 |
| Gymnastic routines | 2 |
| Stories | 4 |
| Voice commands | 14 |
| Volume levels | 5 |
| Touch-sensing sound effects | 5 |

Movement is described in prose — "forward/backward/turning", "walking and sliding in multiple
directions", plus arm, shoulder, and elbow articulation — and is not enumerated as a count.

## Package contents

Robot, remote control, USB cable, user manual.

## Related

The Carle app's Android package is `com.ihunuo.jtlrobot`. The same publisher ships
`com.ihunuo.ykr_hn_2005a_tlw66`, a ThermoPro thermometer app, which is why iHunuo is believed
to be a whitelabel app house rather than a Ruko in-house team.
