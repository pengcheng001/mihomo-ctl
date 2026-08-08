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
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(out_path)
    # A downloaded or generated Clash config can contain proxy credentials.
    try:
        out_path.chmod(0o600)
    except OSError:
        pass
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


def render_decodo(cfg: cfg_mod.Config) -> bytes:
    """Render the configured Decodo nodes as a Mihomo/Clash YAML subscription."""
    from . import decodo as decodo_mod

    return decodo_mod.render_yaml(cfg)


def merge_decodo(cfg: cfg_mod.Config, *, global_mode: bool = False) -> Path:
    """Replace the managed Decodo group/nodes inside the current Mihomo config.

    Existing third-party proxies and groups are retained. Decodo nodes are also
    appended to every existing group that has a ``proxies`` list, so they can be
    selected from the original groups as well as from ``Decodo ISP``. The previous
    Decodo members are removed first to avoid duplicate or stale entries.
    """
    from . import decodo as decodo_mod

    path = cfg.config_path
    if not path.exists():
        raise ValueError(f"当前 Mihomo 配置不存在: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"当前 Mihomo 配置 YAML 解析失败: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("当前 Mihomo 配置顶层不是 mapping")

    generated = decodo_mod.render_document(cfg, include_rules=False)
    new_proxies = generated["proxies"]
    new_names = {str(proxy["name"]) for proxy in new_proxies}
    managed_names = set(new_names)

    old_groups = document.get("proxy-groups") or []
    if not isinstance(old_groups, list):
        raise ValueError("当前 Mihomo 配置 proxy-groups 不是列表")

    node_names = [str(proxy["name"]) for proxy in new_proxies]
    # The managed group is the source of truth for names from an earlier merge.
    # Collect these before rewriting any other group, so renamed/removed nodes do
    # not remain stranded in the original subscription's proxy groups.
    for group in old_groups:
        if isinstance(group, dict) and group.get("name") == decodo_mod.GROUP_NAME:
            members = group.get("proxies") or []
            if isinstance(members, list):
                managed_names.update(str(name) for name in members if name != "DIRECT")

    kept_groups = []
    for group in old_groups:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        if group.get("name") == decodo_mod.GROUP_NAME:
            continue

        members = group.get("proxies")
        if isinstance(members, list):
            # Preserve the original order, then put each Decodo node at the end.
            members = [member for member in members if str(member) not in managed_names]
            members.extend(name for name in node_names if name not in members)
            group["proxies"] = members
        kept_groups.append(group)

    old_proxies = document.get("proxies") or []
    if not isinstance(old_proxies, list):
        raise ValueError("当前 Mihomo 配置 proxies 不是列表")
    document["proxies"] = [
        proxy for proxy in old_proxies
        if not isinstance(proxy, dict) or str(proxy.get("name", "")) not in managed_names
    ] + new_proxies
    document["proxy-groups"] = kept_groups + generated["proxy-groups"]

    if global_mode:
        rules = document.get("rules") or []
        if not isinstance(rules, list):
            raise ValueError("当前 Mihomo 配置 rules 不是列表")
        replaced = False
        for index in range(len(rules) - 1, -1, -1):
            rule = rules[index]
            if isinstance(rule, str) and rule.split(",", 1)[0].strip().upper() == "MATCH":
                rules[index] = f"MATCH,{decodo_mod.GROUP_NAME}"
                replaced = True
                break
        if not replaced:
            rules.append(f"MATCH,{decodo_mod.GROUP_NAME}")
        document["rules"] = rules
        document["mode"] = "rule"

    content = yaml.safe_dump(
        document,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")
    install(content, path)
    inject_local_settings(cfg)
    return path


def remove_decodo(cfg: cfg_mod.Config) -> Path:
    """Remove the managed Decodo group/nodes from the current config."""
    from . import decodo as decodo_mod

    path = cfg.config_path
    if not path.exists():
        raise ValueError(f"当前 Mihomo 配置不存在: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"当前 Mihomo 配置 YAML 解析失败: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("当前 Mihomo 配置顶层不是 mapping")

    managed_names = {node.name for node in decodo_mod.list_nodes(cfg)}
    groups = document.get("proxy-groups") or []
    if isinstance(groups, list):
        kept_groups = []
        for group in groups:
            if isinstance(group, dict) and group.get("name") == decodo_mod.GROUP_NAME:
                members = group.get("proxies") or []
                if isinstance(members, list):
                    managed_names.update(str(name) for name in members if name != "DIRECT")
                continue
            if isinstance(group, dict) and isinstance(group.get("proxies"), list):
                group["proxies"] = [
                    member for member in group["proxies"]
                    if str(member) not in managed_names
                ]
            kept_groups.append(group)
        document["proxy-groups"] = kept_groups

    proxies = document.get("proxies") or []
    if isinstance(proxies, list):
        document["proxies"] = [
            proxy for proxy in proxies
            if not isinstance(proxy, dict) or str(proxy.get("name", "")) not in managed_names
        ]

    rules = document.get("rules")
    if isinstance(rules, list):
        document["rules"] = [
            rule for rule in rules
            if rule != f"MATCH,{decodo_mod.GROUP_NAME}"
        ]

    content = yaml.safe_dump(
        document,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")
    install(content, path)
    inject_local_settings(cfg)
    return path
