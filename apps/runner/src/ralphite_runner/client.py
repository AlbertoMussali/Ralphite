from __future__ import annotations

from typing import Any

import requests


class APIClient:
    def __init__(self, base_url: str, runner_token: str | None = None, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.runner_token = runner_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.runner_token:
            headers["X-Runner-Token"] = self.runner_token
        return headers

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}{path}",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
