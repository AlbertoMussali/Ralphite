from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_simulated_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPHITE_DEV_SIMULATED_EXECUTION", "1")
