"""Bandwidth limiting via tc on the loopback interface (mixed-port mode only)."""
from __future__ import annotations

import json
from pathlib import Path

from .utils import mihomo_pid, say, sudo

STATE_FILE = Path("/tmp/mihomo-ctl-limit.json")


def _purge() -> None:
    sudo(["tc", "qdisc", "del", "dev", "lo", "root"], check=False, capture=True)


def turn_on(rate: str, port: int) -> bool:
    if not mihomo_pid():
        say("mihomo 未运行,先 mhctl on", ok=False); return False

    _purge()

    cmds = [
        ["tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "htb", "default", "30"],
        ["tc", "class", "add", "dev", "lo", "parent", "1:", "classid", "1:10",
         "htb", "rate", rate, "ceil", rate, "quantum", "1500"],
        ["tc", "class", "add", "dev", "lo", "parent", "1:", "classid", "1:30",
         "htb", "rate", "1000mbit"],
        ["tc", "filter", "add", "dev", "lo", "parent", "1:", "protocol", "ip",
         "prio", "1", "u32", "match", "ip", "sport", str(port), "0xffff", "flowid", "1:10"],
    ]
    for c in cmds:
        try:
            sudo(c)
        except Exception as e:
            say(f"命令失败: {' '.join(c)} — {e}", ok=False); return False

    # IPv6 filter (失败容忍,某些内核不支持)
    sudo(
        ["tc", "filter", "add", "dev", "lo", "parent", "1:", "protocol", "ipv6",
         "prio", "2", "u32", "match", "ip6", "sport", str(port), "0xffff", "flowid", "1:10"],
        check=False,
    )

    STATE_FILE.write_text(json.dumps({"rate": rate, "port": port}))
    say(f"下行限速已启用: {rate} (src port {port} → 应用,上行不限)")
    return True


def turn_off() -> None:
    _purge()
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    say("限速已清除")


def status() -> None:
    if not STATE_FILE.exists():
        say("未启用限速", ok=False); return
    s = json.loads(STATE_FILE.read_text())
    print(f"当前: 下行 {s['rate']}  (src port {s['port']} → 应用)\n")
    print("=== lo qdisc ===")
    sudo(["tc", "-s", "qdisc", "show", "dev", "lo"], check=False)
    print("\n=== 限速类 1:10 ===")
    sudo(["tc", "-s", "class", "show", "dev", "lo", "classid", "1:10"], check=False)
