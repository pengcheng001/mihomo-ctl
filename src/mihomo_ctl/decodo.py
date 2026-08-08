"""Decodo ISP account/node management, rendering, tokens, and health checks."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import yaml

from . import config as cfg_mod

DEFAULT_SERVER = "isp.decodo.com"
SUPPORTED_PROTOCOLS = ("http", "socks5")
GROUP_NAME = "Decodo ISP"
HEALTH_URL = "https://ipwho.is/"


@dataclass(frozen=True)
class DecodoNode:
    """A resolved node. Credentials are excluded from repr/log-style output."""

    account_id: str
    node_id: str
    name: str
    server: str
    port: int
    protocol: str
    username: str = field(repr=False)
    password: str = field(repr=False)

    def to_proxy(self) -> dict[str, Any]:
        # Keep this as a native Mihomo HTTP/SOCKS5 node. Do not translate it to
        # Shadowsocks, VLESS, VMess, Trojan, or any other protocol.
        proxy: dict[str, Any] = {
            "name": self.name,
            "type": self.protocol,
            "server": self.server,
            "port": self.port,
            "tls": False,
        }
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


def _text(value: Any, label: str, *, required: bool = True) -> str:
    if value is None:
        value = ""
    value = str(value).strip()
    if required and not value:
        raise ValueError(f"{label} 不能为空")
    return value


def _protocol(value: Any, label: str = "protocol") -> str:
    value = _text(value, label).lower()
    if value not in SUPPORTED_PROTOCOLS:
        raise ValueError(f"{label} 只支持: {', '.join(SUPPORTED_PROTOCOLS)}")
    return value


def _port(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("port 必须是 1-65535 的整数")
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("port 必须是 1-65535 的整数") from None
    if not 1 <= port <= 65535:
        raise ValueError("port 必须是 1-65535 的整数")
    return port


def _account_values(raw: dict[str, Any], index: int) -> tuple[str, str, str, str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"decodo_accounts[{index}] 不是对象")
    account_id = _text(raw.get("id"), f"账号[{index}] id")
    provider = _text(raw.get("provider", "decodo"), f"账号[{account_id}] provider")
    if provider != "decodo":
        raise ValueError(f"账号[{account_id}] provider 必须是 decodo")
    server = _text(raw.get("server", DEFAULT_SERVER), f"账号[{account_id}] server")
    protocol = _protocol(raw.get("protocol", "http"), f"账号[{account_id}] protocol")
    username = _text(raw.get("username"), f"账号[{account_id}] username")
    password = _text(raw.get("password"), f"账号[{account_id}] password")
    return account_id, server, protocol, username, password


def _node_from_record(
    account: dict[str, Any], account_index: int, node: dict[str, Any], node_index: int,
) -> DecodoNode:
    account_id, account_server, account_protocol, username, password = _account_values(
        account, account_index
    )
    if not isinstance(node, dict):
        raise ValueError(f"账号[{account_id}] nodes[{node_index}] 不是对象")
    port = _port(node.get("port"))
    node_id = _text(node.get("id", f"{account_id}:{port}"), f"节点[{account_id}:{port}] id")
    name = _text(node.get("name", node_id), f"节点[{node_id}] name")
    server = _text(node.get("server", account_server), f"节点[{node_id}] server")
    protocol = _protocol(node.get("protocol", account_protocol), f"节点[{node_id}] protocol")
    return DecodoNode(
        account_id=account_id,
        node_id=node_id,
        name=name,
        server=server,
        port=port,
        protocol=protocol,
        username=username,
        password=password,
    )


def account_summaries(cfg: cfg_mod.Config) -> list[dict[str, Any]]:
    """Return account metadata without passwords."""
    summaries = []
    records = cfg.decodo_accounts or []
    if not isinstance(records, list):
        raise ValueError("decodo_accounts 必须是列表")
    for index, raw in enumerate(records):
        account_id, server, protocol, username, _ = _account_values(raw, index)
        nodes = raw.get("nodes") or []
        if not isinstance(nodes, list):
            raise ValueError(f"账号[{account_id}] nodes 必须是列表")
        summaries.append({
            "id": account_id,
            "server": server,
            "protocol": protocol,
            "username": username,
            "node_count": len(nodes),
        })
    return summaries


def list_nodes(cfg: cfg_mod.Config) -> list[DecodoNode]:
    records = cfg.decodo_accounts or []
    if not isinstance(records, list):
        raise ValueError("decodo_accounts 必须是列表")

    result: list[DecodoNode] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for account_index, account in enumerate(records):
        _account_values(account, account_index)
        nodes = account.get("nodes") or []
        if not isinstance(nodes, list):
            raise ValueError(f"账号[{account.get('id', account_index)}] nodes 必须是列表")
        for node_index, raw_node in enumerate(nodes):
            if not isinstance(raw_node, dict):
                raise ValueError(f"账号[{account.get('id', account_index)}] nodes[{node_index}] 不是对象")
            if raw_node.get("enabled", True) is False:
                continue
            node = _node_from_record(account, account_index, raw_node, node_index)
            if node.node_id in seen_ids:
                raise ValueError(f"Decodo 节点 id 重复: {node.node_id}")
            if node.name in seen_names:
                raise ValueError(f"Decodo 节点 name 重复: {node.name}")
            seen_ids.add(node.node_id)
            seen_names.add(node.name)
            result.append(node)
    return result


def _account_record(cfg: cfg_mod.Config, account_id: str) -> dict[str, Any] | None:
    for raw in cfg.decodo_accounts or []:
        if isinstance(raw, dict) and str(raw.get("id", "")) == account_id:
            return raw
    return None


def _new_account_id(cfg: cfg_mod.Config) -> str:
    existing = {str(r.get("id")) for r in (cfg.decodo_accounts or []) if isinstance(r, dict)}
    number = 1
    while f"decodo-{number}" in existing:
        number += 1
    return f"decodo-{number}"


def add_node(
    cfg: cfg_mod.Config,
    *,
    server: str,
    port: int,
    protocol: str,
    username: str,
    password: str,
    name: str,
    account_id: str | None = None,
) -> tuple[str, DecodoNode]:
    """Append one node, reusing an account with the same endpoint/user when possible."""
    server = _text(server, "server")
    port = _port(port)
    protocol = _protocol(protocol)
    username = _text(username, "username")
    password = _text(password, "password")
    name = _text(name, "name")

    if not isinstance(cfg.decodo_accounts, list):
        cfg.decodo_accounts = []

    account: dict[str, Any] | None = None
    if account_id:
        account = _account_record(cfg, account_id)
        if account is None:
            account = {
                "id": account_id,
                "provider": "decodo",
                "server": server,
                "protocol": protocol,
                "username": username,
                "password": password,
                "nodes": [],
            }
            cfg.decodo_accounts.append(account)
    else:
        for candidate in cfg.decodo_accounts:
            if not isinstance(candidate, dict):
                continue
            if (
                str(candidate.get("server", "")) == server
                and str(candidate.get("protocol", "http")).lower() == protocol
                and str(candidate.get("username", "")) == username
            ):
                account = candidate
                break
        if account is None:
            account = {
                "id": _new_account_id(cfg),
                "provider": "decodo",
                "server": server,
                "protocol": protocol,
                "username": username,
                "password": password,
                "nodes": [],
            }
            cfg.decodo_accounts.append(account)

    account["server"] = server
    account["protocol"] = protocol
    account["username"] = username
    account["password"] = password
    nodes = account.setdefault("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError(f"账号[{account.get('id')}] nodes 必须是列表")
    if any(_port(n.get("port")) == port for n in nodes if isinstance(n, dict) and n.get("port") is not None):
        raise ValueError(f"账号[{account.get('id')}] 已存在端口 {port}")

    node_id = f"{account['id']}:{port}"
    all_existing = list_nodes(cfg)
    if any(n.name == name for n in all_existing):
        raise ValueError(f"Decodo 节点 name 已存在: {name}")
    nodes.append({"id": node_id, "name": name, "port": port})
    added = next(n for n in list_nodes(cfg) if n.node_id == node_id)
    return str(account["id"]), added


def remove_node(cfg: cfg_mod.Config, node_ref: str) -> DecodoNode:
    """Remove by node id, display name, or port when it is unambiguous."""
    target: tuple[dict[str, Any], dict[str, Any], DecodoNode] | None = None
    for account_index, account in enumerate(cfg.decodo_accounts or []):
        if not isinstance(account, dict):
            continue
        for node_index, raw_node in enumerate(account.get("nodes") or []):
            if not isinstance(raw_node, dict):
                continue
            try:
                node = _node_from_record(account, account_index, raw_node, node_index)
            except ValueError:
                continue
            if node_ref in (node.node_id, node.name, str(node.port)):
                if target is not None:
                    raise ValueError(f"节点引用不唯一: {node_ref}")
                target = (account, raw_node, node)
    if target is None:
        raise ValueError(f"找不到 Decodo 节点: {node_ref}")
    account, raw_node, node = target
    account["nodes"].remove(raw_node)
    return node


def set_password(cfg: cfg_mod.Config, account_or_node: str, password: str) -> str:
    password = _text(password, "password")
    account = _account_record(cfg, account_or_node)
    if account is None:
        for node in list_nodes(cfg):
            if account_or_node in (node.node_id, node.name):
                account = _account_record(cfg, node.account_id)
                break
    if account is None:
        raise ValueError(f"找不到 Decodo 账号或节点: {account_or_node}")
    account["password"] = password
    return str(account["id"])


def render_document(cfg: cfg_mod.Config, *, include_rules: bool = True) -> dict[str, Any]:
    nodes = list_nodes(cfg)
    if not nodes:
        raise ValueError("还没有 Decodo 节点,先执行 mhctl decodo add")
    names = [node.name for node in nodes]
    document: dict[str, Any] = {
        "proxies": [node.to_proxy() for node in nodes],
        "proxy-groups": [{
            "name": GROUP_NAME,
            "type": "select",
            "proxies": names + ["DIRECT"],
        }],
    }
    if include_rules:
        # A standalone subscription must actually use the generated group. More
        # specific client rules can still be placed before this terminal MATCH.
        document["mode"] = "rule"
        document["rules"] = [f"MATCH,{GROUP_NAME}"]
    return document


def render_yaml(cfg: cfg_mod.Config, *, include_rules: bool = True) -> bytes:
    return yaml.safe_dump(
        render_document(cfg, include_rules=include_rules),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_subscription_token(cfg: cfg_mod.Config, label: str = "default") -> str:
    if not isinstance(cfg.decodo_tokens, list):
        cfg.decodo_tokens = []
    label = _text(label, "token label")
    existing_labels = {str(r.get("id")) for r in cfg.decodo_tokens if isinstance(r, dict)}
    base_label = label
    suffix = 2
    while label in existing_labels:
        label = f"{base_label}-{suffix}"
        suffix += 1
    token = secrets.token_urlsafe(32)
    cfg.decodo_tokens.append({
        "id": label,
        "sha256": _token_digest(token),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return token


def token_summaries(cfg: cfg_mod.Config) -> list[dict[str, str]]:
    return [
        {
            "id": str(raw.get("id", "-")),
            "created_at": str(raw.get("created_at", "-")),
        }
        for raw in (cfg.decodo_tokens or [])
        if isinstance(raw, dict)
    ]


def revoke_token(cfg: cfg_mod.Config, label: str) -> None:
    before = len(cfg.decodo_tokens or [])
    cfg.decodo_tokens = [
        raw for raw in (cfg.decodo_tokens or [])
        if not isinstance(raw, dict) or str(raw.get("id")) != label
    ]
    if len(cfg.decodo_tokens) == before:
        raise ValueError(f"找不到订阅 token: {label}")


def token_is_valid(cfg: cfg_mod.Config, token: str) -> bool:
    digest = _token_digest(token)
    for raw in cfg.decodo_tokens or []:
        if not isinstance(raw, dict):
            continue
        stored = str(raw.get("sha256", raw.get("hash", "")))
        if stored and hmac.compare_digest(stored, digest):
            return True
    return False


def subscription_url(cfg: cfg_mod.Config, token: str) -> str:
    base = (cfg.decodo_public_base_url or "").strip().rstrip("/")
    if not base:
        host = cfg.decodo_listen_host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        base = f"http://{host}:{cfg.decodo_listen_port}"
    return f"{base}/sub/{quote(token, safe='')}"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _offline_result(exc: BaseException) -> dict[str, Any]:
    # Never use str(exc): httpx may include a proxy URL with credentials.
    return {
        "status": "offline",
        "latency": None,
        "exit_ip": "",
        "country": "",
        "region": "",
        "city": "",
        "isp": "",
        "asn": "",
        "last_check_time": _now(),
        "error": type(exc).__name__,
    }


def check_node(node: DecodoNode, url: str = HEALTH_URL, timeout: float = 15.0) -> dict[str, Any]:
    started = time.perf_counter()
    auth_user = quote(node.username, safe="")
    auth_password = quote(node.password, safe="")
    proxy_url = f"{node.protocol}://{auth_user}:{auth_password}@{node.server}:{node.port}"
    try:
        with httpx.Client(
            proxy=proxy_url,
            timeout=timeout,
            trust_env=False,
            headers={"User-Agent": "mihomo-ctl/decodo-health"},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or data.get("success") is False:
            raise ValueError("IP metadata lookup failed")
        exit_ip = _text(data.get("ip"), "exit_ip")
        raw_connection = data.get("connection")
        connection: dict[str, Any] = raw_connection if isinstance(raw_connection, dict) else {}
        asn = connection.get("asn", data.get("asn", ""))
        if isinstance(asn, int):
            asn = f"AS{asn}"
        return {
            "status": "online",
            "latency": max(0, int(round((time.perf_counter() - started) * 1000))),
            "exit_ip": exit_ip,
            "country": str(data.get("country", "") or ""),
            "region": str(data.get("region", "") or ""),
            "city": str(data.get("city", "") or ""),
            "isp": str(connection.get("isp", data.get("isp", "")) or ""),
            "asn": str(asn or ""),
            "last_check_time": _now(),
        }
    except Exception as exc:
        return _offline_result(exc)


def load_health(cfg: cfg_mod.Config) -> dict[str, dict[str, Any]]:
    path = cfg.decodo_health_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_health(cfg: cfg_mod.Config, health: dict[str, dict[str, Any]]) -> None:
    path = cfg.decodo_health_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def check_nodes(cfg: cfg_mod.Config, node_ref: str | None = None) -> dict[str, dict[str, Any]]:
    nodes = list_nodes(cfg)
    if node_ref:
        nodes = [n for n in nodes if n.node_id == node_ref or n.name == node_ref]
        if not nodes:
            raise ValueError(f"找不到 Decodo 节点: {node_ref}")
    if not nodes:
        raise ValueError("还没有 Decodo 节点,先执行 mhctl decodo add")

    results: dict[str, dict[str, Any]] = {}
    workers = min(8, max(1, len(nodes)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(check_node, node): node for node in nodes}
        for future in as_completed(futures):
            node = futures[future]
            try:
                results[node.node_id] = future.result()
            except Exception as exc:  # defensive: one node must never stop the batch
                results[node.node_id] = _offline_result(exc)

    health = load_health(cfg)
    health.update(results)
    save_health(cfg, health)
    return results
