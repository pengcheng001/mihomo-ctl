"""设/清系统代理(GNOME via gsettings)。让浏览器等 GUI 应用也认 mixed-port 代理。

GNOME 桌面绝大多数 GUI 应用(Chrome/Firefox profile sync/VSCode/Slack…)读
org.gnome.system.proxy.* 决定走不走代理。CLI 工具读 http_proxy 环境变量(由
mhctl on 时自动 export)。两者结合 → mixed-port 模式所有应用都覆盖。
"""
from __future__ import annotations

import shutil
import subprocess

from .utils import say, warn


def has_gsettings() -> bool:
    return shutil.which("gsettings") is not None


def gnome_set(host: str, port: int) -> bool:
    """打开 GNOME 系统代理(manual 模式),指向 host:port。"""
    if not has_gsettings():
        return False
    cmds = [
        ["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"],
        ["gsettings", "set", "org.gnome.system.proxy.http",  "host", host],
        ["gsettings", "set", "org.gnome.system.proxy.http",  "port", str(port)],
        ["gsettings", "set", "org.gnome.system.proxy.https", "host", host],
        ["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)],
        ["gsettings", "set", "org.gnome.system.proxy.socks", "host", host],
        ["gsettings", "set", "org.gnome.system.proxy.socks", "port", str(port)],
        ["gsettings", "set", "org.gnome.system.proxy",
         "ignore-hosts", "['localhost', '127.0.0.0/8', '::1', '192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12']"],
    ]
    try:
        for c in cmds:
            subprocess.run(c, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        warn(f"设置 GNOME 系统代理失败: {e.stderr.strip() or e}")
        return False


def gnome_unset() -> bool:
    """关闭 GNOME 系统代理(mode = none)。"""
    if not has_gsettings():
        return False
    try:
        subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"],
                       check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        warn(f"关闭 GNOME 系统代理失败: {e.stderr.strip() or e}")
        return False


def gnome_status() -> dict:
    """返回当前 GNOME 系统代理状态: mode/host/port。"""
    if not has_gsettings():
        return {"available": False}
    out = {"available": True}
    try:
        out["mode"] = subprocess.run(
            ["gsettings", "get", "org.gnome.system.proxy", "mode"],
            capture_output=True, text=True
        ).stdout.strip().strip("'")
        out["http_host"] = subprocess.run(
            ["gsettings", "get", "org.gnome.system.proxy.http", "host"],
            capture_output=True, text=True
        ).stdout.strip().strip("'")
        out["http_port"] = subprocess.run(
            ["gsettings", "get", "org.gnome.system.proxy.http", "port"],
            capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        pass
    return out


def enable(host: str, port: int) -> None:
    if not has_gsettings():
        return
    if gnome_set(host, port):
        say(f"GNOME 系统代理 → {host}:{port} (Chrome/VSCode/桌面应用现在自动走代理)")


def disable() -> None:
    if not has_gsettings():
        return
    if gnome_unset():
        say("GNOME 系统代理已关闭")
