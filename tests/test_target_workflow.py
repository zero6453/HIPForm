"""Finished-target workflow, tested with independent analytic CAD examples."""

import pytest

from hipform.config import SimulationConfig


def test_supplied_materials_and_three_hour_process_round_trip():
    config = SimulationConfig()
    assert config.capsule.yield_strength_mpa == 250
    assert config.capsule.tensile_strength_mpa == 400
    assert config.powder.yield_strength_mpa == 800
    assert config.powder.tensile_strength_mpa == 950
    assert config.powder.expected_final_relative_density == .97
    assert config.powder.typical_volume_shrinkage_fraction == .30
    hold = sum(b.time_s - a.time_s for a, b in zip(config.cycle, config.cycle[1:])
               if a.temperature_c == b.temperature_c == 900
               and a.pressure_mpa == b.pressure_mpa == 120)
    assert hold == 10800
    assert SimulationConfig.model_validate_json(config.model_dump_json()) == config
