# Public standard bus XML examples

AI_SOC and AIZ_SOC are original synthetic translated-map fixtures. Their IEEE
1685-2022 XML describes all bus endpoints, interconnections, local memory windows
and static decoder apertures. native.yaml supplies explicit model/CPU/boot/IRQ
bindings and address-space aliases; it contains no duplicate placement map.
These examples qualify a supported subset, not the complete 46/37-instance chips.

```sh
usp soc validate specs/bus/AI_SOC/native.yaml --input-format ipxact
usp soc generate specs/bus/AI_SOC/native.yaml --input-format ipxact --engine both --out build/xml-generated
usp flow plan specs/bus/AI_SOC-flow.yaml --out build/xml-plan
```

Build using usp flow build and the pinned source/tool options documented in
docs/native-flow.md, then usp flow run/verify. Both -flow.yaml files request
bare-metal, FreeRTOS, Zephyr, µC/OS-II and µC/OS-III on both engines with the same
IR/ELF contracts. No downloaded company reference data is included. See
docs/native-bus-xml.md for bus capability limits and exporter round trips.
