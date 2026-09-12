"""Every scenarios/**/*.yaml is a test case."""
import glob
import os

import pytest

from scenario import run_scenario_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS = sorted(glob.glob(os.path.join(ROOT, 'scenarios', '**', '*.yaml'), recursive=True))


@pytest.mark.parametrize('path', SCENARIOS, ids=[os.path.relpath(p, ROOT) for p in SCENARIOS])
def test_scenario(path):
    scenario, steps, failures = run_scenario_file(path)
    detail = "\n".join(f"  #{s.index} {s.result}" for s in steps if s.result is not None)
    assert not failures, f"{scenario.name} ({scenario.source})\n{detail}\n" + "\n".join(failures)
