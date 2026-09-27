# Troubleshooting

**"cannot_connect" during setup**
- Verify the IP is reachable: `nc -vz <host> 6668`.
- Ensure the device is not already connected to the Smart Life app on the
  same network — the WBR3 only accepts one local TCP client at a time. The
  cleanest fix is to firewall the device from outbound 443/8886 so it stays
  LAN-only. Trade-off on WBR3-module devices: blocking 8886 also freezes
  measurement DPs (temps, frequency, etc.) at their last cloud-synced value
  — see [Known limitations](known-limitations.md). Control still works fine.

**"invalid_auth" during setup**
- The local key is regenerated whenever the device is re-paired in Smart
  Life. Re-fetch it from the Tuya IoT Platform after any Smart Life touch.

**A diagnostic sensor I expected is missing**
- Your firmware variant likely doesn't expose that DP, in which case
  the integration omits the entity on purpose (the alternative — a
  permanently `unavailable` sensor — confuses dashboards and template
  sensors). Compare with the supported DPs in the device's diagnostics
  download to confirm.
- Many diagnostic sensors and the less common fault binary sensors are
  created *disabled by default*: check the device page's hidden/disabled
  entities before assuming the DP is missing.

**Current temperature (or mode, or the fault sensor) stopped updating**
- If the device is firewalled from the internet, try lifting the block and
  see whether the missing values return — on some firmware the DPs stop
  being reported entirely without a Tuya cloud session (issue #18) or
  freeze at their last cloud-synced value (issue #15). See
  [Known limitations](known-limitations.md).
- If the reading is present but implausible, check whether the filter pump
  has been off: the sensor sits in the heat exchanger, not the bulk pool.

**The `fault_code` sensor shows `ok` or `unavailable`**
- `ok` means the device explicitly reported a zero fault bitmap: no active
  fault.
- `unavailable` means Home Assistant cannot currently read the fault bitmap,
  either because the device hasn't reported that DP since the integration
  (re)loaded, or because the connection is unavailable. A healthy reading is never reported as
  unavailable.

**Capturing a live DP dump for a bug report**
- The repository ships [`scripts/probe.py`](../scripts/probe.py), which reads
  the device id and local_key from an `access.yaml` at the repo root (pass your device's
  address with `--host <ip>`; the default host is the maintainer's own),
  and dumps every DP the device exposes. It also writes DP 2 (the setpoint)
  back with its current value to make the device echo its full state. `access.yaml` holds your Tuya local_key, so after creating
  it run `chmod 600 access.yaml` to keep it readable only by your user.

**Boost or Silent is rejected when in Auto**
- Setting Boost or Silent while in Auto raises an error: the standard
  firmware has no Boost/Silent Auto mode, so the integration offers none on
  any model. Switch to Heat or Cool first, then set the preset again
  (switching to Auto resets it).

## Reporting a problem

`pysilverline` ships a diagnostic tool that gathers everything a maintainer
needs — the protocol probe ladder, the full DP map, and (optionally) the
control-write result — into one paste-ready report, with your device id, local
key and host/IP redacted. It runs without Home Assistant, so it also helps when
the integration won't connect at all.

Run it with **no arguments** and it walks you through everything — scanning your
network, letting you pick the heat pump, and asking for the local key:

```bash
pip install pysilverline
pysilverline diagnose
```

```text
Scanning for devices (6s)…

pysilverline diagnostics — let's gather a report for a GitHub issue.

Devices found on your network:
  [1] 192.168.1.42  (productKey 3bhylhz5zhogklel, v3.3, id bf90…)
Pick a number, or press Enter to type the details manually: 1
  → using 192.168.1.42

Local key (16 characters): 0123456789abcdef
Protocol version (auto / 3.3 / 3.4 / 3.5) [auto]:
Also test the control path? … [y/N]: n
```

It then prints the report and offers to save it. Paste the output into a
[bug report](https://github.com/christianreiss/ha-silverline/issues/new/choose).

- Your **local key** comes from `python -m tinytuya wizard` or the Tuya IoT
  Platform (Cloud → Devices) — the same place you found it during setup. Home
  Assistant's diagnostics download redacts it deliberately, so don't look there.
- Say **yes** to the control-path test only if you want it: it writes the
  setpoint (DP 2) back to its *current* value, so it changes nothing, but it
  reveals whether local writes are accepted (the signal behind issue #7).
- Already running Home Assistant? *Download diagnostics* on the device page
  carries the version and DP-map information, but not the probe ladder or the
  control-path test.

<details>
<summary>Scripted / non-interactive use</summary>

Pass all three connection details (plus any options) as flags to skip the
prompts — handy for scripts, or when you already know them:

```bash
pysilverline diagnose --host <device-ip> --device-id <id> --local-key <key> \
    [--probe-write] [--version 3.5] [--output report.md] [--no-redact]
```
</details>
