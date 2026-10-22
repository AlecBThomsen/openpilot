from types import SimpleNamespace

import pytest

from cereal import custom
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.tesla.carstate import CarState
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.teslacan import TeslaCAN
from opendbc.car.tesla.teslacan_legacy import TeslaCANRaven
from opendbc.car.tesla.values import CANBUS, CAR, TeslaFlags, TeslaSafetyFlags


def modern_params(candidate=CAR.TESLA_MODEL_Y, markers=((2, 0x489),), alpha_long=False, docs=False, **toggles):
  fingerprint = gen_empty_fingerprint()
  for bus, address in markers:
    fingerprint[bus][address] = 8
  return CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, docs, SimpleNamespace(**toggles))


@pytest.mark.parametrize("candidate", (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y))
@pytest.mark.parametrize(("markers", "dashcam"), (
  ((), True),
  (((2, 0x489),), False),
  (((0, 0x054),), False),
  (((2, 0x489), (0, 0x054)), False),
  (((0, 0x489), (2, 0x054)), True),
))
def test_steering_protocol_requires_live_markers_on_correct_bus(candidate, markers, dashcam):
  # Selecting a known model alone must not bypass the firmware-protocol guard.
  assert modern_params(candidate, markers).dashcamOnly == dashcam


def test_protocol_guard_preserves_legacy_and_documentation_paths():
  for candidate in (CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_S_PREAP):
    assert not modern_params(candidate, markers=()).dashcamOnly
  assert not modern_params(markers=(), docs=True).dashcamOnly
  assert modern_params(CAR.TESLA_MODEL_X).dashcamOnly


@pytest.mark.parametrize("candidate", (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y, CAR.TESLA_MODEL_X))
@pytest.mark.parametrize("markers", ((), ((2, 0x489),)))
@pytest.mark.parametrize("alpha_long", (False, True))
def test_unsupported_protocol_cannot_expose_a_controller_or_active_safety(candidate, markers, alpha_long):
  cp = modern_params(candidate, markers=markers, alpha_long=alpha_long)
  interface = CarInterface(cp, custom.StarPilotCarParams.new_message())
  if not markers or candidate == CAR.TESLA_MODEL_X:
    assert interface.CC is None
    assert cp.passive
    assert len(cp.safetyConfigs) == 1
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
    assert cp.safetyConfigs[0].safetyParam == 0
  else:
    assert interface.CC is not None
    assert not cp.passive
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.tesla


@pytest.mark.parametrize("candidate", (CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_S_PREAP))
def test_legacy_interfaces_keep_their_controller_and_safety(candidate):
  cp = modern_params(candidate, markers=())
  original_safety = cp.safetyConfigs[0].to_dict()
  interface = CarInterface(cp, custom.StarPilotCarParams.new_message())
  assert interface.CC is not None
  assert not cp.passive
  assert cp.safetyConfigs[0].to_dict() == original_safety


@pytest.mark.parametrize("alpha_long", (False, True))
def test_protocol_detection_preserves_screen_and_cooperative_flags(alpha_long):
  cp = modern_params(CAR.TESLA_MODEL_3, markers=((2, 0x489), (1, 0x3DF)), alpha_long=alpha_long,
                     tesla_aol_screen_tap_requested=True, tesla_aol_screen_brake_disengage_requested=True,
                     tesla_cooperative_steering=True)
  assert not cp.dashcamOnly
  assert cp.flags & TeslaFlags.HAS_VEHICLE_BUS
  assert cp.flags & TeslaFlags.AOL_SCREEN_BUTTON
  expected = TeslaSafetyFlags.COOP_STEERING | TeslaSafetyFlags.AOL_SCREEN_BUTTON | TeslaSafetyFlags.AOL_SCREEN_DISENGAGE_ON_BRAKE
  if alpha_long:
    expected |= TeslaSafetyFlags.LONG_CONTROL
  assert cp.safetyConfigs[0].safetyParam == expected
  assert cp.openpilotLongitudinalControl == alpha_long


@pytest.mark.parametrize("enabled", (False, True))
def test_modern_steering_uses_native_three_bit_wire_encoding(enabled):
  address, data, bus = TeslaCAN(CANPacker("tesla_model3_party")).create_steering_control(0, enabled)
  assert address == 0x488 and bus == CANBUS.party
  assert data[2] & 0xE0 == (0x20 if enabled else 0)
  assert data[3] == ((0x88 + 0x04 + sum(data[:3])) & 0xFF)

  # The HW1 path uses a different DBC and must retain its original two-bit wire format.
  legacy = TeslaCANRaven({CANBUS.party: CANPacker("tesla_can")})
  _, legacy_data, _ = legacy.create_steering_control(0, 0, enabled)
  assert legacy_data[2] & 0xE0 == (0x40 if enabled else 0)


class JuniperCAN:
  """Synthetic complete modern Tesla input with no IBST_status message."""

  PARTY_MESSAGES = ("DI_speed", "DI_systemStatus", "ESP_status", "EPAS3S_sysStatus", "DI_state", "ESP_B", "UI_warning")
  AP_MESSAGES = ("SCCM_steeringAngleSensor", "DAS_status", "DAS_control", "DAS_steeringControl", "DAS_settings")

  def __init__(self):
    self.cp = modern_params()
    self.state = CarState(self.cp, custom.StarPilotCarParams.new_message())
    self.parsers = CarState.get_can_parsers(self.cp)
    self.packer = CANPacker("tesla_model3_party")
    self.frame = 0
    self.state.update(self.parsers, None)  # register the messages consumed by CarState

  def update(self, brake=False, fault=None, stock_steering_type=0, autosteer=False):
    self.frame += 1
    values = {
      "ESP_status": {"ESP_driverBrakeApply": 2 if brake else 1},
      "DAS_steeringControl": {"DAS_steeringControlType": stock_steering_type},
      "DAS_settings": {"DAS_autosteerEnabled": int(autosteer)},
    }
    frames = []
    for bus, names in ((CANBUS.party, self.PARTY_MESSAGES), (CANBUS.autopilot_party, self.AP_MESSAGES)):
      for name in names:
        message = self.packer.make_can_msg(name, bus, values.get(name, {}))
        if name == "ESP_status":
          if fault == "missing":
            continue
          if fault == "checksum":
            address, data, bus = message
            message = (address, bytes([data[0] ^ 1]) + data[1:], bus)
        frames.append(message)
    # Old brake messages must not substitute for a missing/invalid current brake signal.
    if fault:
      frames.append(self.packer.make_can_msg("IBST_status", 0, {"IBST_driverBrakeApply": 1}))
    for parser in self.parsers.values():
      parser.update([(self.frame * 20_000_000, frames)])
    result, _ = self.state.update(self.parsers, None)
    return result, all(parser.can_valid for parser in self.parsers.values())


def test_juniper_can_valid_without_ibst_and_brake_edges_are_detected():
  car = JuniperCAN()
  for _ in range(60):
    state, valid = car.update()
  assert valid and not state.brakePressed
  assert 0x39D not in car.parsers[Bus.party].addresses
  assert 0x145 in car.parsers[Bus.party].addresses
  for pressed in (True, False, True, False):
    state, valid = car.update(brake=pressed)
    assert valid
    assert state.brakePressed == pressed


@pytest.mark.parametrize("fault", ("missing", "checksum"))
def test_required_brake_data_failure_still_invalidates_can(fault):
  car = JuniperCAN()
  for _ in range(60):
    _, valid = car.update()
  assert valid
  for _ in range(30):
    _, valid = car.update(fault=fault)
  assert not valid


@pytest.mark.parametrize("steering_type", (0, 1, 2, 3, 4))
def test_stock_lkas_decodes_three_bit_enum_without_aliasing_fsd(steering_type):
  car = JuniperCAN()
  state, _ = car.update(stock_steering_type=steering_type)
  assert state.stockLkas == (steering_type == 2)


def test_stock_autosteer_gate_is_preserved():
  car = JuniperCAN()
  state, _ = car.update(autosteer=True)
  assert state.invalidLkasSetting
  state, _ = car.update(autosteer=False)
  assert not state.invalidLkasSetting


@pytest.mark.parametrize("version", (
  b'TeM3_E014p10_0.0.0 (24),Y002.21.2',
  b'TeM3_E014p10_0.0.0 (24),YP002.21.2',
  b'TeMYG4_Legacy3Y_0.0.0 (6),Y4003.04.0',
  b'TeMYG4_Main_0.0.0 (77),Y4003.05.4',
  b'TeMYG4_Main_0.0.0 (78),Y4003.06.0',
  b'TeMYG4_Main_0.0.0 (78),Y4OC003.08.1',
  b'TeMYG4_Main_0.0.0 (87),Y4003.09.3',
))
def test_upstream_model_y_eps_versions_match_only_model_y(version):
  fw = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=0x730, brand="tesla", fwVersion=version)
  exact, candidates = match_fw_to_car([fw], "", log=False)
  assert exact
  assert candidates == {CAR.TESLA_MODEL_Y}
