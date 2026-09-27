# Configuration

## Configuration parameters

Connection settings — host, port, local key, device model — are set during
setup and changed with the **Reconfigure** action: click the three-dot menu on
the device in **Settings → Devices** and choose **Reconfigure**. The device ID
must stay the same; a different unit needs a new entry. Reconfigure
re-validates the connection details and then shows the same model dropdown,
pre-selected with the current choice.

One runtime setting lives in **Configure** (the button on the integration
entry, not the device):

| Option | Default | Range | Meaning |
|---|---|---|---|
| Fallback polling interval | 30 s | 8–300 s | How often to issue a full-state query (`DP_QUERY`, or `DP_QUERY_NEW` on v3.5); it runs on this schedule even while pushes are arriving |

The lower bound is a hardware limit rather than a preference: WiFi modules of
the WBR3 family reboot when polled faster than ~8 s, so shorter intervals are
rejected. Pushes deliver changes instantly, but some firmware pushes only one
DP at a time, so the poll is what keeps the full DP set fresh — raise the
interval only if you can live with slower recovery of DPs the device never
pushes.

Changing the option reloads the entry, which briefly reconnects the socket.

## Data update model

- **Polling**: every 30 seconds by default (configurable, see above) the
  integration issues a Tuya full-state query (`DP_QUERY`, or `DP_QUERY_NEW`
  on v3.5) to refresh the full state. Polling
  faster than ~8 s causes the WBR3 WiFi module to reboot — the option will
  not let you go below that.
- **Push**: the device pushes spontaneous state changes within ~200 ms of
  any DP changing. The integration listens for those on the persistent
  socket and applies them immediately, so most updates feel instant. This
  applies to v3.3 and v3.5; v3.4 firmware closes the connection after every
  reply, so those units update on the polling interval.

## Per-mode setpoints

The device keeps a separate stored setpoint per mode-family (Heat, Cool,
Auto) and restores that mode's last value when you switch into it. For a
pool heat pump this is usually what you want — Heat is for warming the
pool (typically 26–30 °C), Cool is for chilling it during a heat wave
(typically 18–24 °C), and Auto holds a band in the middle. The setpoint
slider also adapts its min/max to the active mode (Heat 15–40 °C, Cool
8–28 °C, Auto 8–40 °C). If you change mode and target in one HA service
call (`climate.set_temperature` with both `hvac_mode` and `temperature`),
the mode change is applied first so your target lands under the new mode.
Home Assistant still checks the target against the *current* mode's range
before the call reaches the integration, so pick a target that is valid in
both modes.
