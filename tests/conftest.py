"""Shared fixtures. Tests run from the repo root (the CSV data files live there)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import pytest  # noqa: E402

from scenario import build_systems, make_unit  # noqa: E402


@pytest.fixture
def systems():
    """Standard engine wiring with a fixed seed."""
    return build_systems(seed=12345)


@pytest.fixture
def unit_factory():
    return make_unit
