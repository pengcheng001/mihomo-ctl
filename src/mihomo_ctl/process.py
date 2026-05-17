"""Start/stop mihomo process. Handles both mixed-port and TUN modes."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import config as cfg_mod
from . import sysproxy
from .utils import mihomo_pid, run, say, step, warn


def _spawn_detached(argv: list[str], log_path: Path) -> int:
    """Start a long-running process, fully detached from this Python process."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "wb")
    p = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    return p.pid


def _wait_for_pid(timeout: float = 3.0) -> Optional[int]:
    end = time.time() + timeout
    while time.time() < end:
        pid = mihomo_pid()
        if pid:
            return pid
        time.sleep(0.2)
    return None


def _tail_fatal(log_path: Path, n: int = 50) -> str:
    """从日志最后 n 行抓 fatal/error 信息,用于启动失败时直接告诉用户原因。"""
    try:
        lines = log_path.read_text(errors="ignore").splitlines()[-n:]
    except Exception:
        return ""
    bad = [ln for ln in lines if "level=fatal" in ln or "level=error" in ln]
    return "\n  ".join(bad[-3:]) if bad else ""


def _find_tun_device() -> str | None:
    """探测 mihomo 刚创建的 TUN 接口名(可能是 utun0 / Meta / mihomo 等)。"""
    r = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        # 格式: 123: utun0: <POINTOPOINT,...>  link/none ...
        parts = line.split(":", 2)
        if len(parts) < 2: continue
        name = parts[1].strip().split("@")[0]
        if name in ("utun0", "utun1", "Meta", "mihomo") or name.startswith("utun"):
            return name
    return None


def start_mixed(cfg: cfg_mod.Config) -> bool:
    """Start mihomo in mixed-port mode. Returns True on success."""
    existing = mihomo_pid()
    if existing:
        say(f"mihomo 已在运行 (PID: {existing})")
        return True

    bin_path = Path(cfg.mihomo_bin)
    if not bin_path.is_file() or not os.access(bin_path, os.X_OK):
        say(f"未找到可执行 mihomo: {bin_path}", ok=False); return False
    if not cfg.config_path.is_file():
        say(f"未找到配置: {cfg.config_path}  (先跑 mhctl sub '订阅URL')", ok=False); return False

    step("启动 mihomo (mixed-port)...")
    _spawn_detached([str(bin_path), "-d", str(cfg.conf_dir)], cfg.log_path)
    pid = _wait_for_pid()
    if not pid:
        say(f"启动失败,日志:\n  {_tail_fatal(cfg.log_path)}", ok=False); return False

    # 给 mihomo 1s 解析配置;如果它解析失败会很快退出
    time.sleep(1)
    if not mihomo_pid():
        say(f"mihomo 启动后立刻退出 ({_tail_fatal(cfg.log_path) or '原因未知,查 ' + str(cfg.log_path)})", ok=False)
        return False
    say(f"mihomo 已启动 (PID: {pid}, 日志: {cfg.log_path})")

    # mixed-port 模式: 自动设系统代理(让浏览器等 GUI 应用也走 mihomo)
    sysproxy.enable(cfg.proxy_host, cfg.proxy_port)
    return True


def start_tun(cfg: cfg_mod.Config) -> bool:
    """Start mihomo in TUN mode."""
    bin_path = Path(cfg.mihomo_bin)
    base_cfg = cfg.config_path
    overlay = cfg.tun_overlay_path
    merged = Path("/tmp/mihomo-tun.yaml")

    if not bin_path.is_file():
        say(f"未找到 mihomo: {bin_path}", ok=False); return False
    if not base_cfg.is_file():
        say(f"未找到配置: {base_cfg}  (先跑 mhctl sub '订阅URL')", ok=False); return False
    if not overlay.is_file():
        say(f"未找到 TUN overlay: {overlay}", ok=False); return False
    if not Path("/dev/net/tun").exists():
        say("/dev/net/tun 不存在,内核未启用 TUN 支持", ok=False); return False

    # 检查 capability
    caps = subprocess.run(["getcap", str(bin_path)], capture_output=True, text=True)
    if "cap_net_admin" not in caps.stdout:
        say("mihomo 缺少 CAP_NET_ADMIN,跑一下 mhctl install --force 自动 setcap", ok=False)
        return False

    # 已在跑则先停(避免端口冲突)
    pid = mihomo_pid()
    if pid:
        step(f"检测到 mihomo 在跑 (PID: {pid}),先停止")
        stop(cfg, quiet=True)

    # 拼接 overlay + 主配置
    merged.write_bytes(overlay.read_bytes() + b"\n" + base_cfg.read_bytes())

    step("启动 mihomo (TUN 模式)...")
    _spawn_detached(
        [str(bin_path), "-d", str(cfg.conf_dir), "-f", str(merged)],
        cfg.log_path,
    )
    new_pid = _wait_for_pid()
    if not new_pid:
        say(f"启动失败:\n  {_tail_fatal(cfg.log_path)}", ok=False); return False

    # 给 mihomo 几秒建 TUN 设备 + 解析配置
    time.sleep(1)
    if not mihomo_pid():
        say(f"mihomo 启动后立刻退出:\n  {_tail_fatal(cfg.log_path) or '查 ' + str(cfg.log_path)}", ok=False)
        return False
    say(f"mihomo 已启动 (PID: {new_pid})")

    # 等任意 TUN 类设备出现(名字可能是 utun0/Meta/mihomo)
    for _ in range(8):
        dev = _find_tun_device()
        if dev:
            say(f"TUN 设备已建立: {dev}")
            # TUN 模式不需要系统代理(路由层接管),但确保没有残留的 mixed-port 设置
            sysproxy.disable()
            return True
        time.sleep(1)
    warn(f"未在 8s 内检测到 TUN 设备,可能 TUN 没起来:\n  {_tail_fatal(cfg.log_path)}")
    return True


def stop(cfg: cfg_mod.Config, quiet: bool = False) -> None:
    pid = mihomo_pid()
    if not pid:
        if not quiet:
            say("mihomo 未运行")
        # 即使没在跑也清一下系统代理(可能上次没清干净)
        if not quiet:
            sysproxy.disable()
        return
    step(f"停止 mihomo (PID: {pid})")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(1)
    if mihomo_pid():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    say("mihomo 已停止")
    # 清掉 GNOME 系统代理
    if not quiet:
        sysproxy.disable()


def tun_status() -> dict:
    """Return current utun0 state: exists, addresses, default route via utun."""
    info = {"utun0_up": False, "addrs": [], "default_via_utun": False}
    r = subprocess.run(["ip", "-o", "addr", "show", "utun0"], capture_output=True, text=True)
    if r.returncode == 0:
        info["utun0_up"] = True
        for line in r.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                info["addrs"].append(parts[parts.index("inet") + 1])
            elif "inet6" in parts:
                info["addrs"].append(parts[parts.index("inet6") + 1])
    r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True)
    if "utun" in r.stdout:
        info["default_via_utun"] = True
    return info


def tun_clean() -> None:
    """Safe TUN cleanup: stop mihomo, remove utun0. Does NOT touch iptables/DNS/network."""
    pid = mihomo_pid()
    if pid:
        step(f"停止 mihomo (PID: {pid})")
        try:
            os.kill(pid, signal.SIGTERM); time.sleep(1)
        except ProcessLookupError:
            pass
        if mihomo_pid():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        say("mihomo 已停止")

    # 删除残留 utun 设备 (mihomo SIGTERM 正常退出会自动清,SIGKILL 后才需要手动)
    for dev in ("utun0", "utun1", "Meta"):
        r = subprocess.run(["ip", "link", "show", dev], capture_output=True)
        if r.returncode == 0:
            step(f"删除残留接口 {dev}")
            from .utils import sudo
            sudo(["ip", "link", "set", dev, "down"], check=False, capture=True)
            sudo(["ip", "link", "delete", dev], check=False, capture=True)
            say(f"{dev} 已删除")
    say("TUN 清理完毕 (未动 iptables/DNS/路由)")
