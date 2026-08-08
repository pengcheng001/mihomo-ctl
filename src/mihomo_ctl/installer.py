"""Extract bundled mihomo binary + geo data + UI from the wheel to a working dir."""
from __future__ import annotations

import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import config as cfg_mod
from .utils import say, step, sudo, warn

BUNDLED_PLATFORM = ("Linux", "x86_64")

HOOK_LINE = 'eval "$(mhctl init-shell {shell})"  # mihomo-ctl: tab 补全 + on/off 自动改 env'

CAPS = "cap_net_admin,cap_net_bind_service,cap_net_raw=+ep"

NM_DROPIN_PATH = "/etc/NetworkManager/conf.d/99-mihomo-unmanage-utun.conf"
NM_DROPIN_CONTENT = """# Written by mihomo-ctl: 让 NetworkManager 不要管 mihomo 创建的 TUN 设备
# (否则启动 TUN 时 NM 会弹 polkit 框问你是否允许它管理新接口)
[keyfile]
unmanaged-devices=interface-name:utun*;interface-name:Meta;interface-name:mihomo*
"""

# polkit ≥ 0.106 (Ubuntu 24.04+/Fedora/Arch) 用 JS rules
POLKIT_RULES_PATH = "/etc/polkit-1/rules.d/49-mihomo-resolve.rules"
POLKIT_RULES_TEMPLATE = """// Written by mihomo-ctl: 让用户 '{user}' 免密调 resolvectl 改 utun 的 DNS
// (否则 mihomo TUN 启动时每次都弹 polkit 框问 resolve1.set-* 几遍)
polkit.addRule(function(action, subject) {{
    if (action.id.indexOf("org.freedesktop.resolve1.") === 0 &&
        subject.user == "{user}") {{
        return polkit.Result.YES;
    }}
}});
"""

# polkit < 0.106 (Ubuntu 22.04 及更早) 用 .pkla
POLKIT_PKLA_PATH = "/etc/polkit-1/localauthority/50-local.d/49-mihomo-resolve.pkla"
POLKIT_PKLA_TEMPLATE = """[Allow {user} to manage systemd-resolved per-link DNS for mihomo TUN]
Identity=unix-user:{user}
Action=org.freedesktop.resolve1.set-dns-servers;org.freedesktop.resolve1.set-domains;org.freedesktop.resolve1.set-default-route;org.freedesktop.resolve1.set-llmnr;org.freedesktop.resolve1.set-mdns;org.freedesktop.resolve1.set-dnssec;org.freedesktop.resolve1.revert
ResultAny=yes
ResultInactive=yes
ResultActive=yes
"""


def _polkit_version_tuple() -> tuple[int, int] | None:
    """返回 polkit 主+次版本(例:0.105 -> (0,105), 124-2 -> (124,0))。"""
    for tool in ("pkaction", "pkcheck"):
        try:
            out = subprocess.run([tool, "--version"], capture_output=True, text=True)
            ver = out.stdout.strip().split()[-1]
            parts = ver.replace("-", ".").split(".")
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (FileNotFoundError, ValueError, IndexError):
            continue
    return None


def _check_platform() -> None:
    sys_name, machine = platform.system(), platform.machine()
    if (sys_name, machine) != BUNDLED_PLATFORM:
        say(
            f"内置的 mihomo 是 Linux x86_64 ELF,当前平台 {sys_name}/{machine} 不兼容。\n"
            f"  从 https://github.com/MetaCubeX/mihomo/releases 下载你平台的 mihomo,\n"
            f"  解压后用 mhctl init --mihomo-bin /path/to/mihomo 指过去即可。",
            ok=False,
        )
        sys.exit(1)


def assets_root() -> Path:
    """Return the directory where wheel-bundled assets are placed."""
    import mihomo_ctl
    return Path(mihomo_ctl.__file__).parent / "_assets"


def _copy_file(src: Path, dst: Path, force: bool, executable: bool = False) -> bool:
    if dst.exists() and not force:
        warn(f"已存在,跳过(用 --force 覆盖): {dst}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if executable:
        st = dst.stat().st_mode
        dst.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    say(f"装好: {dst}")
    return True


def _copy_tree(src: Path, dst: Path, force: bool) -> bool:
    if dst.exists() and not force:
        warn(f"已存在,跳过(用 --force 覆盖): {dst}")
        return False
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    say(f"装好 (递归): {dst}")
    return True


def _detect_shell() -> str:
    """从 $SHELL 推断 bash/zsh,识别不出来回退到 bash。"""
    shell = os.environ.get("SHELL", "")
    if shell.endswith("zsh"):
        return "zsh"
    return "bash"


def _rc_file(shell: str) -> Path:
    return Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")


def configure_polkit_resolve() -> bool:
    """写 polkit 规则,让当前用户调 resolvectl set-dns/-domains/-route 免密。
    自动检测 polkit 版本: ≥0.106 用 JS rules, <0.106 用 pkla (Ubuntu 22.04 是后者)。
    不写就是每次 mhctl tun on 都弹 4 次密码框。需要 sudo。"""
    import getpass
    user = getpass.getuser()

    ver = _polkit_version_tuple()
    if ver is None:
        warn("找不到 polkit (pkaction/pkcheck), 跳过 polkit 配置")
        return False

    # 决定格式: (主版本 > 0) 或 (主版本=0 且 次版本 >= 106) 走 JS rules
    use_rules = ver[0] > 0 or ver[1] >= 106
    if use_rules:
        path = POLKIT_RULES_PATH
        content = POLKIT_RULES_TEMPLATE.format(user=user)
        fmt = "JS rules"
    else:
        path = POLKIT_PKLA_PATH
        content = POLKIT_PKLA_TEMPLATE.format(user=user)
        fmt = "pkla"

    # 已经写过且 user 匹配就跳
    try:
        existing = subprocess.run(["sudo", "cat", path],
                                   capture_output=True, text=True).stdout
        if f"unix-user:{user}" in existing or f'subject.user == "{user}"' in existing:
            say(f"polkit 规则已存在,跳过: {path}")
            return True
    except Exception:
        pass

    step(f"写 polkit {fmt}: {path} (polkit={ver[0]}.{ver[1]}, 放行 user={user}, 可能要输 sudo 密码)")
    try:
        # 确保目录存在 (Ubuntu 22.04 上 /etc/polkit-1/localauthority/50-local.d 可能不存在)
        parent = str(Path(path).parent)
        sudo(["mkdir", "-p", parent], capture=True)
        # 写文件 + 修权限(polkit 要求 root:root 644,否则静默忽略)
        subprocess.run(["sudo", "tee", path],
                       input=content, text=True,
                       capture_output=True, check=True)
        sudo(["chmod", "644", path], capture=True)
        sudo(["chown", "root:root", path], capture=True)
        say(f"已写入 {fmt} 规则 (启 TUN 不会再因 resolvectl 弹密码框)")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"写 polkit 规则失败(sudo 取消?): {e.stderr.strip() if e.stderr else e}")
        return False


def configure_networkmanager_unmanage() -> bool:
    """写 NM dropin,让 NetworkManager 忽略 utun*/Meta/mihomo*,避免启 TUN 时弹密码框。
    需要 sudo。如果系统没装 NetworkManager 直接跳过。"""
    if not subprocess.run(["pgrep", "-x", "NetworkManager"],
                          capture_output=True).returncode == 0:
        say("NetworkManager 未运行,跳过 NM 配置")
        return True

    # 已经写过就跳
    if Path(NM_DROPIN_PATH).exists():
        try:
            existing = Path(NM_DROPIN_PATH).read_text()
            if "interface-name:utun" in existing:
                say(f"NM dropin 已存在,跳过: {NM_DROPIN_PATH}")
                return True
        except PermissionError:
            pass  # 文件不可读但存在,假设已写过

    step(f"写 NM dropin: {NM_DROPIN_PATH} (可能要输 sudo 密码)")
    try:
        # 用 sudo tee 写,避免 shell 重定向权限问题
        p = subprocess.run(["sudo", "tee", NM_DROPIN_PATH],
                           input=NM_DROPIN_CONTENT, text=True,
                           capture_output=True, check=True)
        sudo(["systemctl", "reload", "NetworkManager"], check=False, capture=True)
        say(f"已写入并 reload NetworkManager (启 TUN 不会再弹密码框)")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"写 NM dropin 失败(sudo 取消?): {e.stderr.strip() if e.stderr else e}")
        warn(f"以后启 TUN 看到弹框点 Cancel 即可,功能不受影响")
        return False


def set_capabilities(bin_path: Path) -> bool:
    """给 mihomo 加 CAP_NET_ADMIN 等(TUN 模式需要)。已经有就跳过。需要 sudo。"""
    # 已经有就不动
    r = subprocess.run(["getcap", str(bin_path)], capture_output=True, text=True)
    if "cap_net_admin" in r.stdout:
        say(f"capabilities 已具备,跳过 setcap")
        return True

    step(f"sudo setcap {CAPS} {bin_path} (TUN 模式需要,可能要输密码)")
    try:
        sudo(["setcap", CAPS, str(bin_path)])
        say("capabilities 已设置 (mhctl tun on 现在能用了)")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"setcap 失败(可能是 sudo 取消或没装 libcap),TUN 模式将不可用: {e}")
        warn(f"以后想用 TUN 手动跑: sudo setcap '{CAPS}' {bin_path}")
        return False
    except FileNotFoundError:
        warn("找不到 setcap 命令,装一下: apt install libcap2-bin")
        return False


def install_shell_hook(shell: str | None = None) -> Path | None:
    """把 hook 一行幂等地追加到 rc 文件。返回 rc 路径(None 表示已存在,没改)。"""
    shell = shell or _detect_shell()
    rc = _rc_file(shell)
    line = HOOK_LINE.format(shell=shell)
    existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    if any("mihomo-ctl" in ln and "init-shell" in ln for ln in existing.splitlines()):
        say(f"{rc} 已有 hook,跳过")
        return None
    with rc.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"\n# >>> mihomo-ctl >>>\n{line}\n# <<< mihomo-ctl <<<\n")
    say(f"已把 hook 加到 {rc}")
    return rc


def deploy_stub_config(dest: Path) -> None:
    """部署 config.yaml.example 到工作目录;若没 config.yaml 则同时建一份让 mhctl on 能跑。
    *绝不* 覆盖已存在的 config.yaml(避免吞掉你的真订阅,即使 --force 也不动)。"""
    root = assets_root()
    src = root / "config" / "config.yaml.example"
    if not src.exists():
        return
    dst_example = dest / "config" / "config.yaml.example"
    _copy_file(src, dst_example, force=True)  # 模板每次都覆盖到最新

    real_config = dest / "config" / "config.yaml"
    if not real_config.exists():
        shutil.copyfile(src, real_config)
        say(f"装好 stub config (mhctl on 能跑了,但流量都走 DIRECT): {real_config}")
    else:
        say(f"已有 config.yaml,保持不动(stub 不覆盖真订阅): {real_config}")


def uninstall_systemd_unit() -> None:
    """停掉 + 取消自启 + 删 unit 文件 + reload。幂等。"""
    unit_path = Path.home() / ".config" / "systemd" / "user" / "mihomo-ctl.service"

    # 1. stop + disable(就算 unit 已不存在也不报错)
    subprocess.run(["systemctl", "--user", "disable", "--now", "mihomo-ctl"],
                   check=False, capture_output=True)
    say("已停止 + 取消开机自启")

    # 2. 删 unit 文件
    if unit_path.exists():
        unit_path.unlink()
        say(f"已删除: {unit_path}")
    else:
        say(f"unit 文件本来就不存在: {unit_path}")

    # 3. reload 让 systemd 忘掉
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   check=False, capture_output=True)
    subprocess.run(["systemctl", "--user", "reset-failed"],
                   check=False, capture_output=True)
    say("systemd 已 daemon-reload")


def install_systemd_unit(cfg: cfg_mod.Config) -> Path:
    """生成 systemd user unit 到 ~/.config/systemd/user/mihomo-ctl.service。"""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "mihomo-ctl.service"

    content = f"""[Unit]
Description=mihomo (Clash.Meta) proxy (managed by mihomo-ctl)
Documentation=https://github.com/MetaCubeX/mihomo
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={cfg.mihomo_bin} -d {cfg.conf_dir}
ExecStop=/bin/kill -SIGTERM $MAINPID
Restart=on-failure
RestartSec=5s
# 注意: systemd 起的 mihomo 不会给你的 shell 设 http_proxy 环境变量,
# 想自动 export 还需要在 .bashrc 里手动 eval "$(mhctl env on)"
# 或者纯走 TUN 模式(路由层接管,不需要环境变量)

[Install]
WantedBy=default.target
"""
    unit_path.write_text(content, encoding="utf-8")
    say(f"systemd unit 已写入: {unit_path}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    return unit_path


DECODO_UNIT_NAME = "mihomo-ctl-decodo-subscription.service"


def install_decodo_systemd_unit(cfg: cfg_mod.Config) -> Path:
    """Generate a separate user unit for the token-protected Decodo endpoint."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / DECODO_UNIT_NAME
    python = shlex.quote(sys.executable)
    host = shlex.quote(str(cfg.decodo_listen_host))

    content = f"""[Unit]
Description=mihomo-ctl Decodo subscription endpoint
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m mihomo_ctl decodo serve --host {host} --port {int(cfg.decodo_listen_port)}
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
    unit_path.write_text(content, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    say(f"Decodo 订阅 systemd unit 已写入: {unit_path}")
    return unit_path


def uninstall_decodo_systemd_unit() -> None:
    unit_path = Path.home() / ".config" / "systemd" / "user" / DECODO_UNIT_NAME
    subprocess.run(["systemctl", "--user", "disable", "--now", DECODO_UNIT_NAME],
                   check=False, capture_output=True)
    if unit_path.exists():
        unit_path.unlink()
        say(f"已删除: {unit_path}")
    else:
        say(f"unit 文件本来就不存在: {unit_path}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)


def install(dest: Path, force: bool = False, skip_shell_hook: bool = False,
            shell: str | None = None, skip_setcap: bool = False,
            skip_stub_config: bool = False, with_systemd: bool = False,
            skip_nm_unmanage: bool = False, skip_polkit_resolve: bool = False) -> None:
    """Extract bundled assets to dest, update user config, install shell hook, set caps."""
    _check_platform()
    root = assets_root()
    if not (root / "bin" / "mihomo").exists():
        say(
            "wheel 中找不到 _assets/bin/mihomo。\n"
            "  若你是 `pip install -e .` 的开发模式,资源未被注入,请先 `python -m build` 出 wheel 再装。",
            ok=False,
        )
        sys.exit(1)

    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    step(f"展开到 {dest}")

    # mihomo 在跑会导致 'Text file busy' 没法覆盖二进制,先停
    from .utils import mihomo_pid
    import signal, os as _os, time as _time
    pid = mihomo_pid()
    if pid:
        warn(f"mihomo 在运行 (PID: {pid}),先停止才能覆盖二进制")
        try:
            _os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                if not mihomo_pid(): break
                _time.sleep(0.2)
            if mihomo_pid():
                _os.kill(pid, signal.SIGKILL); _time.sleep(0.3)
        except ProcessLookupError:
            pass
        say("已停止,安装后可 mhctl on 重启")

    _copy_file(root / "bin" / "mihomo", dest / "bin" / "mihomo",
               force=force, executable=True)
    for fname in ("geoip.dat", "geoip.metadb", "geosite.dat", "tun-overlay.yaml"):
        _copy_file(root / "config" / fname, dest / "config" / fname, force=force)
    _copy_tree(root / "config" / "ui", dest / "config" / "ui", force=force)

    # 写本机配置
    cfg = cfg_mod.load()
    cfg.mihomo_dir = str(dest)
    cfg.mihomo_bin = str(dest / "bin" / "mihomo")
    cfg_mod.save(cfg)
    say(f"已更新本机配置: {cfg_mod.CONFIG_FILE}")

    # 装 shell hook (tab 补全 + on/off 自动改 env)
    if not skip_shell_hook:
        step("装 shell hook (tab 补全 + on/off 自动改 env)...")
        install_shell_hook(shell)

    # 给二进制设 capability (TUN 模式需要)
    if not skip_setcap:
        set_capabilities(dest / "bin" / "mihomo")

    # 让 NetworkManager 忽略 utun*/Meta(避免启 TUN 弹密码框)
    if not skip_nm_unmanage:
        configure_networkmanager_unmanage()

    # polkit 规则:让 systemd-resolved 的 DNS 修改免密(否则 TUN 启动弹 4 次)
    if not skip_polkit_resolve:
        configure_polkit_resolve()

    # 部署 stub config(没拉订阅时也能 mhctl on 把 UI 起来)
    if not skip_stub_config:
        deploy_stub_config(dest)

    # 装 systemd user unit (开机自启)
    if with_systemd:
        cfg = cfg_mod.load()
        install_systemd_unit(cfg)
