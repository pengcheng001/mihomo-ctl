"""User config stored at ~/.config/mihomo-ctl/config.toml."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG_DIR = Path(user_config_dir("mihomo-ctl"))
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULT_BIN_CANDIDATES = [
    "/usr/local/bin/mihomo",
    "/usr/bin/mihomo",
]
DEFAULT_DIR_CANDIDATES = [
    "$HOME/Data/pengcheng/mihomo",
    "/data/proxy/mihomo",
    "$HOME/.config/mihomo",
]


@dataclass
class Config:
    mihomo_dir: str = ""                # 含 config.yaml/bin/...
    mihomo_bin: str = ""                # mihomo 可执行文件
    config_file: str = "config.yaml"    # 相对 mihomo_dir
    log_file: str = "mihomo.log"        # 相对 mihomo_dir
    api_port: int = 9090
    api_secret: str = "mypassword123"
    api_bind: str = "0.0.0.0"           # external-controller 监听地址(127.0.0.1 = 仅本机)
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 17890
    proxy_user: str = ""                # 跑 mihomo 的 Linux 用户(留空=当前用户)

    def resolve_paths(self) -> None:
        self.mihomo_dir = os.path.expandvars(os.path.expanduser(self.mihomo_dir or ""))
        self.mihomo_bin = os.path.expandvars(os.path.expanduser(self.mihomo_bin or ""))

    @property
    def conf_dir(self) -> Path:
        """mihomo -d 指向的目录(放 config.yaml + geoip.* + geosite.dat + cache.db + ui/)。"""
        return Path(self.mihomo_dir) / "config"

    @property
    def config_path(self) -> Path:
        return self.conf_dir / self.config_file

    @property
    def log_path(self) -> Path:
        return Path(self.mihomo_dir) / self.log_file

    @property
    def tun_overlay_path(self) -> Path:
        return self.conf_dir / "tun-overlay.yaml"

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"


def _auto_detect_bin() -> str:
    which = shutil.which("mihomo")
    if which:
        return which
    for p in DEFAULT_BIN_CANDIDATES:
        if Path(p).exists():
            return p
    return ""


def _auto_detect_dir() -> str:
    for raw in DEFAULT_DIR_CANDIDATES:
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        if (p / "config.yaml").exists() or (p / "config" / "config.yaml").exists():
            return str(p)
    return ""


def load(create_if_missing: bool = True) -> Config:
    if not CONFIG_FILE.exists():
        if not create_if_missing:
            return Config()
        cfg = Config(
            mihomo_dir=_auto_detect_dir(),
            mihomo_bin=_auto_detect_bin(),
        )
        save(cfg)
        return cfg

    with CONFIG_FILE.open("rb") as f:
        data = tomllib.load(f)
    cfg = Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    cfg.resolve_paths()
    return cfg


def save(cfg: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("wb") as f:
        tomli_w.dump(asdict(cfg), f)
