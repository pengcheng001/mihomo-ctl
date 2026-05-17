"""Download and validate Clash YAML subscriptions."""
from __future__ import annotations

import base64
import datetime as dt
import re
import shutil
from pathlib import Path

import httpx
import yaml

from . import config as cfg_mod
from .utils import say, step, warn

CLASH_UA = "clash.meta/1.18.0"

_CLASH_FIRST_KEYS = re.compile(
    r"^(#|proxies|proxy-providers|port|mixed-port|mode|"
    r"external-controller|external-ui|allow-lan|tun|dns|secret):"
)


def download(url: str, ua: str = CLASH_UA) -> bytes:
    step(f"拉取订阅 (UA={ua})")
    # 显式 trust_env=False:不要让正在跑的 mihomo 代理自己(可能死循环)
    with httpx.Client(follow_redirects=True, timeout=30.0, trust_env=False) as c:
        r = c.get(url, headers={"User-Agent": ua})
    r.raise_for_status()
    say(f"下载完成 (HTTP {r.status_code}, {len(r.content)} 字节)")
    return r.content


def identify_and_validate(content: bytes) -> None:
    """Raise ValueError if not a usable Clash YAML."""
    if len(content) < 100:
        raise ValueError(f"响应过短 ({len(content)} 字节),可能 URL 错误或被拒")

    first = content.split(b"\n", 1)[0].strip()
    first_str = first.decode("utf-8", errors="ignore")

    if _CLASH_FIRST_KEYS.match(first_str):
        # 进一步用 yaml.safe_load 校验
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 解析失败: {e}")
        if not isinstance(doc, dict):
            raise ValueError("YAML 顶层不是 mapping")
        if "proxies" not in doc and "proxy-providers" not in doc:
            raise ValueError("YAML 缺少 proxies/proxy-providers 节,不是有效订阅")
        return

    # Base64 V2Ray/SS 链接列表
    if re.fullmatch(r"[A-Za-z0-9+/=\s]+", first_str):
        try:
            base64.b64decode(first_str[:200], validate=False)
            raise ValueError(
                "这是 Base64 编码的 V2Ray/SS 链接订阅,不是 Clash YAML。\n"
                "  请向机场客服索要 'Clash for Windows 订阅地址',\n"
                "  或用 subconverter 转换: https://github.com/tindy2013/subconverter"
            )
        except Exception:
            pass

    raise ValueError(f"无法识别的格式,首行: {first_str[:80]!r}")


def install(content: bytes, out_path: Path) -> Path:
    """Write content to out_path, backup old. Returns backup path or None."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bak = None
    if out_path.exists():
        bak = out_path.with_name(
            f"{out_path.name}.{dt.datetime.now():%Y%m%d_%H%M%S}.bak"
        )
        shutil.copy2(out_path, bak)
        step(f"原配置备份: {bak}")
    tmp = out_path.with_suffix(out_path.suffix + ".new")
    tmp.write_bytes(content)
    tmp.replace(out_path)
    return bak


def inject_local_settings(cfg: cfg_mod.Config) -> None:
    """Insert/overwrite mixed-port/external-controller/external-ui/secret in the YAML.
    Uses pyyaml round-trip; preserves the rest of the file's content (re-serialized).
    For minimal disruption we instead do a line-prepend approach: ensure those keys
    appear as top-level lines, replacing any existing ones.
    """
    path = cfg.config_path
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    updates = {
        "mixed-port": str(cfg.proxy_port),
        "external-controller": f"{cfg.api_bind}:{cfg.api_port}",
        "external-ui": "ui",
        "secret": f'"{cfg.api_secret}"',
    }
    lines = text.splitlines()
    keys_seen = set()
    new_lines = []
    for ln in lines:
        m = re.match(r"^(mixed-port|external-controller|external-ui|secret):", ln)
        if m:
            k = m.group(1)
            new_lines.append(f"{k}: {updates[k]}")
            keys_seen.add(k)
        else:
            new_lines.append(ln)
    prepend = [f"{k}: {v}" for k, v in updates.items() if k not in keys_seen]
    out = "\n".join(prepend + new_lines) + "\n"
    path.write_text(out, encoding="utf-8")


def node_count(path: Path) -> int:
    """Rough count of `- name:` entries (matches both block and flow YAML)."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r"^\s*-\s*\{?\s*name:", text, flags=re.MULTILINE))
