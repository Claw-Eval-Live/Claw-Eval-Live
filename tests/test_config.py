import pytest

from liveclaw_500.config import load_config


def test_load_config_missing_explicit_path_raises():
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config("model_configs/does_not_exist.yaml")
