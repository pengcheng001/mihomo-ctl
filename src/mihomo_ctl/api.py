"""Client for mihomo external-controller REST API."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from . import config as cfg_mod


class API:
    def __init__(self, cfg: cfg_mod.Config):
        self.base = cfg.api_url
        self.headers = {"Authorization": f"Bearer {cfg.api_secret}"} if cfg.api_secret else {}
        # trust_env=False 让 httpx 忽略 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量
        # 否则 mhctl on 自己设的 ALL_PROXY=socks5://... 会让 httpx 试图走 SOCKS
        # (本机 API 调用根本不需要任何代理)
        self.client = httpx.Client(headers=self.headers, timeout=5.0, trust_env=False)

    def alive(self) -> bool:
        try:
            r = self.client.get(f"{self.base}/")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def proxies(self) -> dict[str, Any]:
        return self.client.get(f"{self.base}/proxies").json().get("proxies", {})

    def group(self, name: str) -> dict[str, Any]:
        return self.client.get(f"{self.base}/proxies/{name}").json()

    def switch(self, group: str, node: str) -> None:
        r = self.client.put(f"{self.base}/proxies/{group}", json={"name": node})
        r.raise_for_status()

    def test_group(self, group: str, url: str = "http://cp.cloudflare.com/generate_204",
                   timeout_ms: int = 5000) -> dict[str, int]:
        r = self.client.get(
            f"{self.base}/group/{group}/delay",
            params={"url": url, "timeout": timeout_ms},
            timeout=30.0,
        )
        return r.json() if r.status_code == 200 else {}

    def reload_config(self, path: str) -> None:
        r = self.client.put(f"{self.base}/configs", json={"path": path})
        r.raise_for_status()

    def selectors(self) -> list[tuple[str, str]]:
        """Return [(group_name, current_choice), ...] for all Selector groups."""
        return [
            (k, v.get("now", "-"))
            for k, v in self.proxies().items()
            if v.get("type") == "Selector"
        ]
