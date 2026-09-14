# pysilverline

Async client for **Poolex Silverline / Tuya v3.3** pool heat pumps. Speaks the
local Tuya protocol (TCP/6668, AES-128-ECB) directly — no cloud, no Smart Life
account at runtime.

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
series, Phalén Calidi XP. DPs 1, 2, 3, 4, 13 are confirmed across the family;
DPs 101–111 are firmware-dependent.

## License

MIT

The `fi_120_v35` model/layout key selects the FI 150 Full Inverter map for
Silverline FI 120 Tuya v3.5 units (issue #22). It is distinct from
`pc_inv_120v2`; no automatic firmware detection is applied.
