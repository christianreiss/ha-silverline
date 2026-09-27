# pysilverline

Async client for **Poolex Silverline / Tuya v3.3, v3.4 and v3.5** pool heat
pumps (protocol version auto-detected). Speaks the local Tuya protocol
(TCP/6668; AES-128-ECB for v3.3/v3.4, AES-128-GCM for v3.5) directly — no
cloud, no Smart Life account at runtime. Requires Python 3.12+.

This package is the I/O layer underneath the
[`poolex_silverline`](https://github.com/christianreiss/ha-silverline) Home
Assistant integration but works standalone too.

## Install

```bash
pip install pysilverline
```

## Use

```python
import asyncio
from pysilverline import SilverlineClient

async def main():
    client = SilverlineClient(
        host="10.0.0.50",
        device_id="bf1234567890abcdefghij",
        local_key="0123456789abcdef",
    )
    await client.connect()
    state = await client.get_status()
    print(state)
    await client.set_dp(2, 28)        # set target temp to 28 °C
    await client.set_dp(4, "BoostHeat")
    await client.disconnect()

asyncio.run(main())
```

Listen for spontaneous push updates from the device:

```python
def on_update(state):
    print("push:", state.mode, state.temp_current)

unsub = client.add_listener(on_update)
# ... later
unsub()
```

## Compatible devices

The Tuya schema is shared across the Poolex Silverline FI family and several
OEM siblings: Poolex JetLine Selection FI, Steinbach Silent Mini, Brustec BR
series, Phalén Calidi XP. DPs 1–4 are shared across the family, but DP 4's
mode strings vary (the Steinbach Silent Mini uses `Heating`/`Cooling`, the
PC-INV-120V2 `h_powerful` and friends); the fault bitmap is usually DP 13, and
diagnostic DPs from 101 upward are firmware-dependent.

The `fi_120_v35` model/layout key selects the FI 150 Full Inverter map for
Silverline FI 120 Tuya v3.5 units (issue #22). It is distinct from
`pc_inv_120v2`; no automatic firmware detection is applied.

## License

MIT
