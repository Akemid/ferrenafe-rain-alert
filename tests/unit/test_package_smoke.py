"""Smoke test: the rain_alert package and its five layer sub-packages import
cleanly under their expected dotted names. Proves the phase-1 skeleton
(pyproject.toml + src/ layout) is wired correctly before any domain code
exists (tasks.md 1.2)."""

import importlib

LAYER_SUBPACKAGES = ["domain", "ports", "application", "adapters", "entrypoints"]


def test_root_package_imports_with_expected_name() -> None:
    module = importlib.import_module("rain_alert")
    assert module.__name__ == "rain_alert"


def test_every_layer_subpackage_imports_as_a_package() -> None:
    for name in LAYER_SUBPACKAGES:
        module = importlib.import_module(f"rain_alert.{name}")
        assert module.__name__ == f"rain_alert.{name}"
        # A regular package backed by __init__.py sets __file__; an implicit
        # namespace package (missing __init__.py) leaves it None. Asserting
        # __file__ proves the skeleton file exists, not just that the
        # dotted path resolves.
        assert module.__file__ is not None
        assert module.__file__.endswith("__init__.py")
