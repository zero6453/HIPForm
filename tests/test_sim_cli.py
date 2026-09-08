"""CLI configuration and failure semantics."""

import json
import pytest

pytest.importorskip("gmsh")


def test_default_config_can_be_created_and_loaded(tmp_path):
    from hipform.cli import main
    from hipform.config import load_config
    path = tmp_path / "ideal.yaml"
    assert main(["init", "--output", str(path)]) == 0
    config = load_config(path)
    assert config.tolerances.flat_mm == 10
    assert config.tolerances.angular_mm == 20
    assert max(p.temperature_c for p in config.cycle) == 920
    assert main(["init", "--output", str(path)]) == 2


def test_failed_mesh_records_failure_without_false_acceptance(tmp_path):
    from hipform.cli import main
    output = tmp_path / "run"
    code = main(["run", "--cavity", str(tmp_path / "missing.step"),
                 "--capsule", str(tmp_path / "capsule.step"), "--output", str(output)])
    assert code == 2
    result = json.loads((output / "result.json").read_text())
    assert result["execution_status"] == "failed"
    assert result["engineering_acceptance"] == "not_assessed"


def test_malformed_yaml_produces_readable_error(tmp_path, capsys):
    from hipform.cli import main
    path = tmp_path / "bad.yaml"
    path.write_text("cycle: [\n")
    assert main(["run", "--cavity", "absent.step", "--capsule", "absent.step",
                 "--config", str(path), "--output", str(tmp_path / "out")]) == 2
    assert "YAML" in capsys.readouterr().err
