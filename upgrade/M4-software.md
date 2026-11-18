# M4: software

Target version: 0.3.0. Status: **partial** at baseline.
Dependencies: M3.

## Observed baseline

Native bare-metal/Linux accepted; fixed-profile RTOS/Zephyr evidence cannot qualify arbitrary native designs.

## Required exits

- [ ] IR-derived startup/linker/DTS/BSP and device drivers
- [ ] Real bare-metal, FreeRTOS, Zephyr, both uCOS kernels and boot sequence tests
- [ ] OpenSBI/Linux and an actual Yocto image with relocation/IRQ/driver acceptance

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
