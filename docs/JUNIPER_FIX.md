# Model Y Juniper compatibility branch

Base: Firestar StarPilot `Dom`, commit `823a5d5674ec6635b51dd989884dfbd661f90dc4`.
Branch: `juniper-fix`.

## Problem and changes

The Juniper does not send `IBST_status` (0x39D). Dom required that message in both its Python vehicle state and Panda safety firmware, causing invalid CAN data and the displayed **Unknown Vehicle Variant** alert.

This branch adapts the complete brake change from [commaai/opendbc #2628](https://github.com/commaai/opendbc/pull/2628): use `ESP_status` (0x145, eight bytes, 50 Hz), including brake state, checksum, counter, quality checks, and safety receive requirements.

It also adapts the current three-bit `DAS_steeringControlType` handling and live protocol detection described in [#3802](https://github.com/commaai/opendbc/pull/3802). Model 3/Y requires 0x489 on Autopilot party bus 2 or 0x054 on party bus 0. Otherwise the interface has no controller, is passive, and selects `noOutput` safety. This enforcement is necessary because Dom's runtime originally ignored `dashcamOnly` when a controller existed. Older two-bit Model 3/Y firmware is therefore unsupported by this branch. HW1 and pre-AP retain their separate existing encodings and controllers; Model X retains its declared dashcam-only status.

Seven Model Y EPS firmware identifiers observed upstream are added for recognition. Recognition alone does not bypass the live protocol guard. The identifier set was cross-checked against commaai/opendbc `c3955753ae7258412814e2cdca3ee8af979e4679` and sunnypilot dev `6fbe0eb7316682a2007c1690f9e93ef091c7010a`.

Existing steering/acceleration bounds, brake/driver overrides, stock Autosteer checks, screen controls, cooperative steering, and checksum/counter validation remain enabled. No communication-error suppression or forced vehicle selection was added.

## Validation

Local tests run on macOS with Python 3.12:

- Tesla vehicle, controller, Juniper, HW1, and pre-AP tests: **129 passed**, one existing messaging serialization test excluded because the bundled `msgq/ipc_pyx.so` is a Linux binary and cannot load on macOS.
- Tesla, Tesla screen-button, HW1, and pre-AP Panda safety tests: **122 passed, 47 skipped** by the existing test suite.
- Ruff passes changed interface, fingerprints, and test files. The existing unused `cp_party` warning in the unchanged legacy portion of `carstate.py` remains.
- `git diff --check` passes.

The synthetic Juniper test supplies the required modern messages without IBST, verifies brake transitions and valid CAN, then removes or corrupts ESP brake data to verify CAN becomes invalid. Tests also check three-bit wire encoding, rejection of unsupported steering modes, preservation of legacy encoding, and passive/noOutput behavior without protocol markers.

There has been **no replay of this user's logs, on-device validation, or road test**. A separate process crash cannot be diagnosed from the generic communication alert alone. The branch fixes the identified compatibility defects; it does not establish that every reported communication error has the same cause.

## Reproducing the tests

From the repository root, using an environment containing the repository's Python dependencies:

```sh
PYTHONPATH=. scons -C opendbc_repo -j4 opendbc/safety/tests/libsafety/libsafety.so
PYTHONPATH=. python -m pytest --confcutdir=opendbc_repo -o addopts='' \
  opendbc_repo/opendbc/safety/tests/test_tesla.py \
  opendbc_repo/opendbc/safety/tests/test_tesla_screen_button.py \
  opendbc_repo/opendbc/safety/tests/test_tesla_legacy.py \
  opendbc_repo/opendbc/safety/tests/test_tesla_preap.py -q
PYTHONPATH=. python -m pytest --confcutdir=opendbc_repo -o addopts='' \
  opendbc_repo/opendbc/car/tesla/tests opendbc_repo/opendbc/car/tesla/preap/tests \
  -k 'not test_steering_limit_info_round_trips_through_custom_message' -q
```

On a Linux environment with a compatible built `msgq` module, omit the final `-k` exclusion to run the messaging test too. The `--confcutdir` option isolates these component tests from the full application-manager fixture.

## Firmware packaging

Dom includes a tracked `prebuilt` marker and precompiled Panda firmware. A source-only safety patch is insufficient: the installed Panda would continue using its previous safety code.

Build all Panda variants, including Tesla wake and ignition variants, from the source commit that will ship:

```sh
cd panda
PYTHONPATH=../opendbc_repo scons --minimal -j4
```

This requires `arm-none-eabi-gcc`, SCons, and PyCryptodome. The default debug signing configuration matches Dom's existing release artifacts; do not change signing/build mode casually. Commit the regenerated tracked `panda/board/obj` artifacts together. `pandad` compares the selected firmware signature, flashes mismatches, and checks the result.

All 18 firmware variants were rebuilt locally with Arm GNU Toolchain 14.2.Rel1 from source commit `35a37d068b909110ced5ad3c1d16e7a29ddd3092`. Their embedded version is `DEV-35a37d06-DEBUG`. Lengths, version metadata, payloads, RSA signatures, and ARM ELF architecture were verified; every runtime signature differs from Dom's original firmware. [JUNIPER_FIRMWARE.json](JUNIPER_FIRMWARE.json) records build provenance and SHA-256 hashes for the 90 associated signed images, binaries, bootstubs, and ELF files. The firmware artifacts are committed separately from the source changes so that the embedded source revision remains traceable.

The [`juniper-fix` branch](https://github.com/AlecBThomsen/StarPilot/tree/juniper-fix) contains the complete source and firmware. It has not been installed on a comma device. A local copy and binary Git patch are retained on the development Mac; `StarPilot-Juniper.patch` contains the complete change and applies to the exact Dom base listed above. Do not copy only the Python files onto a device: the DBC and matching Panda firmware are part of the same change.

## Before using the branch in the vehicle

Keep a route back to the known-working sunnypilot version. Complete a parked startup check: confirm the correct car, expected brake state, successful Panda firmware update, and absence of persistent CAN/process errors. Do not bypass an alert or force another model to enable controls.

If an error remains, save the full alert and Galaxy's Tmux Log. A missing old brake message previously appeared as `CANParser: 0x39d IBST_status not valid (timeout or missing)`. Other missing-message names or a traceback require separate diagnosis. Log sharing is not automatic.
