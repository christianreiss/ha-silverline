# Use cases and example automations

## Use cases

- **Seasonal pool warmup.** Set `hvac_mode: heat` with the `boost`
  preset and a 28 °C target in spring; the heat pump runs at maximum
  inverter speed until the pool reaches setpoint, then naturally
  modulates down.
- **Overnight quiet operation.** Use the `eco` preset (mapped to the
  Silent DP variant) during sleeping hours — the compressor caps its
  frequency to a quieter rpm at the cost of slower heating.
- **PV-surplus heating.** Trigger `climate.set_temperature` from a
  template sensor watching your solar surplus: setpoint moves up by a
  few degrees when there's free electricity, back down when there
  isn't.
- **Off-season holding.** Park the unit in Heat at its 15 °C minimum
  with the `eco` preset; it then only runs when the water falls below
  that.

## Examples

### Prevent dry-running by sequencing the filter pump first

The unit hard-faults on water flow (E03 on the classic firmware, E25 on Full
Inverter, E6 on the Nano 5kW) within ~30 s of running dry. Start the filter
pump before the heat pump, and turn the heat pump on through this script
rather than directly:

```yaml
script:
  pool_heat_on:
    alias: "Pool: pump, then heat"
    sequence:
      - action: switch.turn_on
        target:
          entity_id: switch.pool_filter_pump
      - delay: "00:00:15"
      - action: climate.set_hvac_mode
        target:
          entity_id: climate.pool_heatpump
        data:
          hvac_mode: heat
```

### Heat only during low-tariff hours

Drop the setpoint to a low floor at peak-rate times; raise
it during the cheap window so the inverter runs when electricity is
cheapest. Pair with a tariff sensor or a static time schedule.

```yaml
automation:
  - alias: "Pool: warm during off-peak"
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.pool_heatpump
        data:
          temperature: 28
      - action: climate.set_preset_mode
        target:
          entity_id: climate.pool_heatpump
        data:
          preset_mode: boost

  - alias: "Pool: idle during peak"
    triggers:
      - trigger: time
        at: "06:00:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.pool_heatpump
        data:
          temperature: 18
      - action: climate.set_preset_mode
        target:
          entity_id: climate.pool_heatpump
        data:
          preset_mode: eco
```

### Notify when a fault appears (with self-clearing)

The integration also surfaces fault bits as Home Assistant **Repair
issues** (Settings → Repairs) that auto-clear when the device clears
the fault. For an active push notification on top of that, watch the
fault binary sensors directly (the `for:` mirrors the Repair card's 60 s
debounce, so a brief self-clearing trip at start-up stays quiet):

```yaml
automation:
  - alias: "Pool: notify on water-flow fault"
    triggers:
      - trigger: state
        entity_id: binary_sensor.pool_heatpump_water_flow_fault
        from: "off"
        to: "on"
        for: "00:01:00"
    actions:
      - action: notify.mobile_app_<your_phone>
        data:
          title: "Pool heat pump: water-flow fault"
          message: >
            The heat pump can't detect water flow. Check the filter
            pump. The unit stops heating until flow is restored.
```
