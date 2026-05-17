from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from . import __version__
from . import api as api_mod
from . import config as cfg_mod
from . import installer as installer_mod
from . import limit as limit_mod
from . import process as proc_mod
from . import shell as shell_mod
from . import subscription as sub_mod
from . import sysproxy as sysproxy_mod
from .utils import console, mihomo_pid, say, step, warn


def _build_ui_url(cfg: cfg_mod.Config) -> str:
    """构造 Web UI 链接,带 query 参数让面板自动填表。"""
    base = f"{cfg.api_url}/ui/?hostname=127.0.0.1&port={cfg.api_port}"
    if cfg.api_secret:
        base += f"&secret={cfg.api_secret}"
    return base


def _reload_or_restart(cfg: cfg_mod.Config) -> None:
    """优先调 API 热加载,失败就重启 mihomo。"""
    if mihomo_pid():
        try:
            api_mod.API(cfg).reload_config(str(cfg.config_path))
            say("已热加载配置")
            return
        except Exception as e:
            warn(f"热加载失败 ({e}),改为重启")
    proc_mod.stop(cfg, quiet=True)
    proc_mod.start_mixed(cfg)


def _cfg() -> cfg_mod.Config:
    c = cfg_mod.load()
    if not c.mihomo_dir or not c.mihomo_bin:
        say("还没配置 mihomo 路径,先跑: mhctl init --mihomo-dir <dir> --mihomo-bin <bin>", ok=False)
        sys.exit(1)
    return c


# ============================================================
# 动态补全回调 (Tab 时调用,API 不可达就静默返回 [])
# ============================================================

def _complete_groups(ctx, param, incomplete: str):
    try:
        cfg = cfg_mod.load(create_if_missing=False)
        if not cfg.mihomo_dir:
            return []
        a = api_mod.API(cfg)
        names = [
            n for n, info in a.proxies().items()
            if info.get("type") in ("Selector", "URLTest", "Fallback", "LoadBalance")
        ]
        return [click.shell_completion.CompletionItem(n) for n in names if n.startswith(incomplete)]
    except Exception:
        return []


def _complete_nodes(ctx, param, incomplete: str):
    try:
        cfg = cfg_mod.load(create_if_missing=False)
        a = api_mod.API(cfg)
        group = ctx.params.get("group") or "🚀节点选择"
        info = a.group(group)
        names = info.get("all", [])
        return [click.shell_completion.CompletionItem(n) for n in names if n.startswith(incomplete)]
    except Exception:
        return []


def _complete_rates(ctx, param, incomplete: str):
    """常见限速值。"""
    common = ["100kbps", "200kbps", "500kbps", "1mbps", "2mbps",
              "5mbit", "10mbit", "50mbit", "100mbit"]
    return [click.shell_completion.CompletionItem(r) for r in common if r.startswith(incomplete)]


# ============================================================
# 主命令
# ============================================================

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="mhctl")
def main() -> None:
    """mihomo (Clash.Meta) 命令行管控工具。

    \b
    ━━━━━ 第一次使用 (4 步上车) ━━━━━
      1. mhctl install                 解二进制+geo+UI 到 ~/.local/share, 改 .bashrc, sudo setcap
      2. source ~/.bashrc              让 Tab 补全 + on/off 自动 export 立刻在当前终端生效
      3. mhctl sub '<订阅URL>'         从机场链接拉 Clash YAML
      4. mhctl on                      启动(mixed-port) 或 mhctl tun on (TUN 透明)
         curl ip.sb                    验证出口 IP 是节点地区

    \b
    ━━━━━ Web 控制面板 (浏览器图形管理) ━━━━━
      mhctl ui no-auth                  免密登录(自动绑 127.0.0.1,安全)
      mhctl ui open                     系统浏览器打开 UI
      mhctl ui url                      打印带 secret 的 UI URL (可复制)
      mhctl ui set-auth <密码>          重新设密码

    \b
    ━━━━━ 日常使用 ━━━━━
      mhctl on / off                    启停 mixed-port (CLI 走 env, GUI 走 GNOME 系统代理,自动配)
      mhctl tun on / off                启停 TUN 模式(路由层透明接管,Docker/Java 都走)
      mhctl sysproxy on/off/status      手动开关 GNOME 系统代理(mhctl on/off 已自动调)
      mhctl status                      看进程 + API + UI URL + 各组当前节点
      mhctl node list                   列默认组节点 + 最近延迟
      mhctl node switch <TAB>           Tab 补节点名,切节点
      mhctl node auto                   切回 ♻️自动选择
      mhctl node test                   批量测自动组延迟
      mhctl limit on 200kbps            下行限到 200KB/s (上传不限)
      mhctl limit off
      mhctl sub '<URL>' --reload        更新订阅 + 热加载(不重启)
      mhctl tun clean                   utun 残留时强清
      mhctl systemd install/enable      开机自启(systemd user unit)

    \b
    ━━━━━ 提示 ━━━━━
      • 装好后 mhctl 是 shell 函数,'mhctl on/off' 会自动 export/unset 代理环境变量
      • 任何位置按 <TAB> 都可补全子命令/选项/组名/节点名
      • 出问题: mhctl status / tail ~/.local/share/mihomo-ctl/mihomo/mihomo.log
    """


# ---------- install (展开 wheel 自带的 mihomo+geo 数据) ----------

@main.command()
@click.option("--dest", type=click.Path(file_okay=False),
              default=str(Path.home() / ".local/share/mihomo-ctl/mihomo"),
              show_default=True,
              help="目标安装目录(放 mihomo 二进制+geo 数据+UI)")
@click.option("--force", is_flag=True, help="覆盖已存在文件")
@click.option("--no-shell-hook", is_flag=True,
              help="不自动改 ~/.bashrc 或 ~/.zshrc")
@click.option("--no-setcap", is_flag=True,
              help="不给 mihomo 加 CAP_NET_ADMIN (TUN 模式将不可用)")
@click.option("--no-stub-config", is_flag=True,
              help="不部署 stub config.yaml (没拉订阅时 mhctl on 会失败)")
@click.option("--no-nm-unmanage", is_flag=True,
              help="不写 NetworkManager dropin(启 TUN 时 NM 会弹密码框)")
@click.option("--no-polkit-resolve", is_flag=True,
              help="不写 polkit 规则(启 TUN 时 systemd-resolved 会弹 4 次密码框)")
@click.option("--systemd", is_flag=True,
              help="顺带装 systemd user unit (~/.config/systemd/user/mihomo-ctl.service)")
@click.option("--shell", type=click.Choice(["bash", "zsh"]),
              help="指定 shell(默认根据 $SHELL 推断)")
def install(dest: str, force: bool, no_shell_hook: bool, no_setcap: bool,
            no_stub_config: bool, no_nm_unmanage: bool, no_polkit_resolve: bool,
            systemd: bool, shell: str | None) -> None:
    """一键安装: 解二进制+geo+UI, 写配置/hook, setcap, NM dropin, 可选 systemd。

    \b
    会做(过程可能要输 sudo 密码,#4 #5 需要):
      1. 把内置的 mihomo 二进制 + geoip/geosite + UI 解到 --dest
      2. 写 ~/.config/mihomo-ctl/config.toml
      3. 在 ~/.bashrc 或 ~/.zshrc 末尾加 hook (Tab 补全 + on/off 自动 export)
      4. sudo setcap cap_net_admin,... mihomo  (TUN 模式必需)
      5. sudo 写 NetworkManager dropin 让它忽略 utun*/Meta
      6. sudo 写 polkit 规则让你免密调 resolvectl  (避免 TUN 启动弹 4 次密码框)
      7. 部署 stub config.yaml (没订阅时 mhctl on 也能起 UI)

    \b
    完成后:
      source ~/.bashrc          # 当前终端立刻生效
      mhctl sub '你的订阅URL'
      mhctl on                  # mixed-port 模式
      mhctl tun on              # TUN 模式 (setcap 已自动装好)
    """
    installer_mod.install(Path(dest), force=force,
                          skip_shell_hook=no_shell_hook, shell=shell,
                          skip_setcap=no_setcap, skip_stub_config=no_stub_config,
                          skip_nm_unmanage=no_nm_unmanage,
                          skip_polkit_resolve=no_polkit_resolve,
                          with_systemd=systemd)
    console.print()
    console.print("[bold]下一步[/]:")
    if not no_shell_hook:
        rc = installer_mod._rc_file(shell or installer_mod._detect_shell())
        console.print(f"  1. 让当前终端立刻生效: [cyan]source {rc}[/]")
        console.print("  2. 拉订阅:             [cyan]mhctl sub '订阅URL'[/]")
        console.print("  3. 启动:               [cyan]mhctl on[/]   (自动 export 代理环境变量)")
    else:
        console.print("  1. 拉订阅: [cyan]mhctl sub '订阅URL'[/]")
        console.print("  2. 启动:   [cyan]mhctl on && eval \"$(mhctl env on)\"[/]")


# ---------- init / setup ----------

@main.command()
@click.option("--mihomo-dir", type=click.Path(file_okay=False), help="mihomo 工作目录(含 config.yaml/log 等)")
@click.option("--mihomo-bin", type=click.Path(dir_okay=False), help="mihomo 可执行文件路径")
@click.option("--port", type=int, help="mixed-port 监听端口")
@click.option("--api-port", type=int, help="external-controller 端口")
@click.option("--secret", help="API/控制面板密码")
def init(mihomo_dir, mihomo_bin, port, api_port, secret) -> None:
    """初始化或更新本机配置 (~/.config/mihomo-ctl/config.toml)。"""
    cfg = cfg_mod.load()
    if mihomo_dir:  cfg.mihomo_dir = mihomo_dir
    if mihomo_bin:  cfg.mihomo_bin = mihomo_bin
    if port:        cfg.proxy_port = port
    if api_port:    cfg.api_port = api_port
    if secret:      cfg.api_secret = secret
    cfg.resolve_paths()
    cfg_mod.save(cfg)
    say(f"已写入 {cfg_mod.CONFIG_FILE}")
    for k in ("mihomo_dir", "mihomo_bin", "proxy_port", "api_port"):
        console.print(f"  {k} = {getattr(cfg, k)}")


@main.command()
def setup() -> None:
    """把当前配置 (port/secret/UI) 注入到 mihomo 的 config.yaml。"""
    cfg = _cfg()
    if not cfg.config_path.exists():
        say(f"config.yaml 不存在: {cfg.config_path}", ok=False); sys.exit(1)
    sub_mod.inject_local_settings(cfg)
    say(f"已更新 {cfg.config_path} 中的 mixed-port/external-controller/external-ui/secret")


# ---------- on / off / status ----------

@main.command()
@click.option("--tun", is_flag=True, help="启动 TUN 模式 (需 CAP_NET_ADMIN)")
def on(tun: bool) -> None:
    """启动 mihomo。"""
    cfg = _cfg()
    ok = proc_mod.start_tun(cfg) if tun else proc_mod.start_mixed(cfg)
    if not ok:
        sys.exit(1)
    if not tun:
        console.print(f"  代理端点: http://{cfg.proxy_host}:{cfg.proxy_port}")


@main.command()
def off() -> None:
    """停止 mihomo。"""
    proc_mod.stop(_cfg())


@main.command()
def status() -> None:
    """查看 mihomo 进程和 API 可达性。"""
    cfg = _cfg()
    pid = mihomo_pid()
    if pid:
        say(f"mihomo 在运行 (PID: {pid})")
    else:
        say("mihomo 未运行", ok=False); return
    a = api_mod.API(cfg)
    if a.alive():
        say(f"API 端点: {cfg.api_url}  (绑定 {cfg.api_bind})")
        console.print(f"  [bold cyan]Web UI: {_build_ui_url(cfg)}[/]")
        if cfg.api_secret:
            console.print(f"  [dim](URL 已带 secret,点开后直接确认即可)[/]")
        else:
            console.print(f"  [dim](免密模式,点开直接 ADD)[/]")
        sels = a.selectors()
        if sels:
            t = Table(title="Selector 组当前选择")
            t.add_column("组"); t.add_column("当前节点")
            for g, n in sels: t.add_row(g, n)
            console.print(t)
    else:
        say(f"API 不可达: {cfg.api_url} (secret 错误?)", ok=False)


# ---------- sysproxy (系统代理:让浏览器等 GUI 应用也走 mixed-port) ----------

@main.group()
def sysproxy() -> None:
    """系统代理(GNOME) 管理。mhctl on/off 已自动调用,这里给你手动覆盖用。

    \b
    背景:
      • mixed-port 模式只覆盖读 http_proxy env 的 CLI 工具(curl/git/...)
      • GUI 应用(Chrome/Firefox/VSCode/Slack) 读 GNOME 系统代理(gsettings)
      • mhctl on 会自动 gsettings set 让两者都覆盖; mhctl off 自动清
    """


@sysproxy.command("on")
def sysproxy_on() -> None:
    """手动打开 GNOME 系统代理(指向 mhctl 的 mixed-port)。"""
    cfg = _cfg()
    sysproxy_mod.enable(cfg.proxy_host, cfg.proxy_port)


@sysproxy.command("off")
def sysproxy_off() -> None:
    """手动关掉 GNOME 系统代理。"""
    sysproxy_mod.disable()


@sysproxy.command("status")
def sysproxy_status() -> None:
    """看 GNOME 系统代理当前状态。"""
    s = sysproxy_mod.gnome_status()
    if not s.get("available"):
        say("gsettings 没装(非 GNOME 桌面),系统代理不可用", ok=False); return
    console.print(f"  mode      = {s.get('mode')}")
    console.print(f"  http_host = {s.get('http_host')}")
    console.print(f"  http_port = {s.get('http_port')}")


# ---------- ui (Web 控制面板配置) ----------

@main.group()
def ui() -> None:
    """Web UI 配置: 免密登录 / 设密码 / 看 URL / 浏览器打开。"""


@ui.command("url")
def ui_url() -> None:
    """打印浏览器可点的 UI URL (带 hostname/port/secret 自动填表)。"""
    cfg = _cfg()
    click.echo(_build_ui_url(cfg))


@ui.command("open")
def ui_open_cmd() -> None:
    """用系统默认浏览器打开 UI(Linux 调 xdg-open)。"""
    import subprocess
    cfg = _cfg()
    url = _build_ui_url(cfg)
    say(f"打开 {url}")
    subprocess.Popen(["xdg-open", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@ui.command("no-auth")
def ui_no_auth() -> None:
    """干掉 secret + 把 API 绑定到 127.0.0.1(仅本机),UI 免密登录。

    \b
    会做:
      1. ~/.config/mihomo-ctl/config.toml 里 secret="" + api_bind="127.0.0.1"
      2. 把空 secret 和新 bind 写进 config.yaml
      3. 热加载(或重启)mihomo 让生效
    """
    cfg = _cfg()
    cfg.api_secret = ""
    cfg.api_bind = "127.0.0.1"
    cfg_mod.save(cfg)
    say("本机配置已更新: secret='' bind=127.0.0.1")

    sub_mod.inject_local_settings(cfg)
    say(f"已写进 {cfg.config_path}")

    _reload_or_restart(cfg)
    console.print()
    say(f"现在 UI 免密可登: {_build_ui_url(cfg)}")
    console.print("  [dim](API 已仅监听 127.0.0.1,局域网访问不到,无 secret 也安全)[/]")


@ui.command("set-auth")
@click.argument("secret")
@click.option("--bind", default="0.0.0.0", show_default=True,
              help="external-controller 监听地址 (127.0.0.1 仅本机, 0.0.0.0 局域网可访问)")
def ui_set_auth(secret: str, bind: str) -> None:
    """设 secret + (可选)改监听地址。

    \b
    例:
      mhctl ui set-auth mySecret123                # 设密码,继续监听 0.0.0.0
      mhctl ui set-auth abc --bind 127.0.0.1       # 设密码 + 仅本机
    """
    cfg = _cfg()
    cfg.api_secret = secret
    cfg.api_bind = bind
    cfg_mod.save(cfg)
    say(f"本机配置已更新: secret='{secret}' bind={bind}")

    sub_mod.inject_local_settings(cfg)
    say(f"已写进 {cfg.config_path}")

    _reload_or_restart(cfg)
    console.print()
    say(f"新 UI URL: {_build_ui_url(cfg)}")


# ---------- systemd (开机自启) ----------

@main.group()
def systemd() -> None:
    """systemd user unit 管理(开机自启 mihomo)。

    \b
    第一次:
      mhctl systemd install        生成 ~/.config/systemd/user/mihomo-ctl.service
      mhctl systemd enable         开机自启 + 立刻启动
      loginctl enable-linger $USER 让你不登录时 user service 也跑(开机自启的前提)

    \b
    日常:
      mhctl systemd status / restart / stop / start / disable
    """


@systemd.command("install")
def systemd_install() -> None:
    """生成 systemd user unit 文件(不启用,不启动)。"""
    cfg = _cfg()
    installer_mod.install_systemd_unit(cfg)
    console.print()
    console.print("[bold]下一步[/]:")
    console.print("  [cyan]mhctl systemd enable[/]            开机自启 + 现在启动")
    console.print("  [cyan]loginctl enable-linger $USER[/]    没登录时也跑(开机自启必需)")


@systemd.command("enable")
def systemd_enable() -> None:
    """启用开机自启 + 立刻启动。"""
    from .utils import run
    run(["systemctl", "--user", "enable", "--now", "mihomo-ctl"])
    say("已 enable + 启动")


@systemd.command("disable")
def systemd_disable() -> None:
    """停止 + 取消开机自启。"""
    from .utils import run
    run(["systemctl", "--user", "disable", "--now", "mihomo-ctl"])
    say("已停止 + disable")


@systemd.command("start")
def systemd_start() -> None:
    from .utils import run
    run(["systemctl", "--user", "start", "mihomo-ctl"]); say("已启动")


@systemd.command("stop")
def systemd_stop() -> None:
    from .utils import run
    run(["systemctl", "--user", "stop", "mihomo-ctl"]); say("已停止")


@systemd.command("restart")
def systemd_restart() -> None:
    from .utils import run
    run(["systemctl", "--user", "restart", "mihomo-ctl"]); say("已重启")


@systemd.command("status")
def systemd_status() -> None:
    """查看 systemd 状态(实时)。"""
    from .utils import run
    run(["systemctl", "--user", "status", "mihomo-ctl"], check=False)


@systemd.command("uninstall")
def systemd_uninstall() -> None:
    """彻底卸载: 停止 + 取消自启 + 删 unit 文件 + reload。幂等可重复跑。"""
    installer_mod.uninstall_systemd_unit()


# ---------- tun (虚拟网卡/路由层透明代理) ----------

@main.group()
def tun() -> None:
    """TUN 虚拟网卡模式: 路由层接管所有 TCP/UDP (Docker/Java/不认环境变量的二进制都走)。

    \b
    需求:
      sudo setcap 'cap_net_admin,cap_net_bind_service,cap_net_raw=+ep' <mihomo>
      (一次性,授予内核创建 utun 设备的能力)
    """


@tun.command("on")
def tun_on() -> None:
    """启动 TUN 模式 (= mhctl on --tun)。"""
    if not proc_mod.start_tun(_cfg()):
        sys.exit(1)


@tun.command("off")
def tun_off() -> None:
    """停止 mihomo (TUN 模式正常退出会自动清 utun 设备和路由)。"""
    proc_mod.stop(_cfg())


@tun.command("clean")
def tun_clean() -> None:
    """强清残留: 停 mihomo + 删 utun 接口。当 SIGKILL/进程崩溃后 utun0 还在时用。

    \b
    *不* 动 iptables/DNS/路由表(那是 clean_tun.sh 的核武器,有需要回去用原脚本)。
    """
    proc_mod.tun_clean()


@tun.command("status")
def tun_status_cmd() -> None:
    """查看 utun0 当前状态。"""
    s = proc_mod.tun_status()
    if s["utun0_up"]:
        say(f"utun0 在: {' '.join(s['addrs']) or '(无地址)'}")
        if s["default_via_utun"]:
            say("默认路由经 utun ✓ (TUN 接管中)")
        else:
            warn("默认路由未经 utun (auto-route 可能没生效)")
    else:
        say("utun0 不存在", ok=False)


# ---------- env (for eval) ----------

@main.group()
def env() -> None:
    """打印 shell 环境变量脚本 (用 `eval $(mhctl env on)` 应用)。"""


@env.command("on")
def env_on() -> None:
    click.echo(shell_mod.env_on_script(_cfg()), nl=False)


@env.command("off")
def env_off() -> None:
    click.echo(shell_mod.env_off_script(), nl=False)


# ---------- init-shell (shell hook: Tab 补全 + on/off 自动 env) ----------

@main.command("init-shell")
@click.argument("shell_name", type=click.Choice(["bash", "zsh"]))
def init_shell(shell_name: str) -> None:
    """打印 shell 初始化脚本: Tab 补全 + mhctl on/off 自动改 env。

    \b
    用法: 加这一行到 ~/.bashrc 或 ~/.zshrc(mhctl install 会自动加,不必手动):
      eval "$(mhctl init-shell bash)"
    """
    click.echo(shell_mod.init_script(shell_name))


# ---------- node ----------

@main.group()
def node() -> None:
    """节点管理 (通过 mihomo external-controller API)。"""


@node.command("list")
@click.option("--group", "-g", default="🚀节点选择", shell_complete=_complete_groups,
              help="节点组名 (Tab 补全可用)")
def node_list(group: str) -> None:
    """列出某组的所有节点及最近延迟。"""
    cfg = _cfg(); a = api_mod.API(cfg)
    g = a.group(group)
    if not g or "all" not in g:
        say(f"组 [{group}] 不存在", ok=False); return
    t = Table(title=f"{group}  ({g.get('type')})  当前: {g.get('now', '-')}")
    t.add_column("节点"); t.add_column("最近延迟(ms)", justify="right")
    for name in g["all"]:
        try:
            info = a.group(name)
            hist = info.get("history") or []
            delay = str(hist[-1]["delay"]) if hist else "n/a"
        except Exception:
            delay = "?"
        t.add_row(name, delay)
    console.print(t)


@node.command("switch")
@click.option("--group", "-g", default="🚀节点选择", shell_complete=_complete_groups,
              help="目标组 (Tab 补全)")
@click.argument("node_name", shell_complete=_complete_nodes)
def node_switch(node_name: str, group: str) -> None:
    """切换组到指定节点 (Tab 补全节点名)。"""
    cfg = _cfg(); a = api_mod.API(cfg)
    try:
        a.switch(group, node_name)
        say(f"[{group}] → [{node_name}]")
    except Exception as e:
        say(f"切换失败: {e}", ok=False); sys.exit(1)


@node.command("test")
@click.option("--group", "-g", default="♻️自动选择", shell_complete=_complete_groups)
def node_test(group: str) -> None:
    """批量测某组所有节点延迟。"""
    cfg = _cfg(); a = api_mod.API(cfg)
    step(f"测试 [{group}] (几秒)...")
    res = a.test_group(group)
    if not res:
        say("无结果(组类型可能不支持)", ok=False); return
    for name, ms in sorted(res.items(), key=lambda kv: kv[1]):
        console.print(f"  {ms:>5} ms  {name}")


@node.command("auto")
@click.option("--group", "-g", default="🚀节点选择", shell_complete=_complete_groups)
def node_auto(group: str) -> None:
    """切到 ♻️自动选择。"""
    cfg = _cfg(); a = api_mod.API(cfg)
    a.switch(group, "♻️自动选择")
    say(f"[{group}] → [♻️自动选择]")


# ---------- limit ----------

@main.group()
def limit() -> None:
    """下行限速 (mixed-port 模式,作用于 lo)。"""


@limit.command("on")
@click.argument("rate", shell_complete=_complete_rates)
def limit_on(rate: str) -> None:
    """启用限速。RATE 如 200kbps (=200KB/s) / 1mbit。"""
    cfg = _cfg()
    if not limit_mod.turn_on(rate, cfg.proxy_port):
        sys.exit(1)


@limit.command("off")
def limit_off() -> None:
    limit_mod.turn_off()


@limit.command("status")
def limit_status() -> None:
    limit_mod.status()


# ---------- sub ----------

@main.command()
@click.argument("url")
@click.option("--no-setup", is_flag=True, help="不自动注入本机 port/secret/ui")
@click.option("--reload", "do_reload", is_flag=True, help="写完后调 API 热加载")
@click.option("--ua", help="自定义 User-Agent (默认模拟 Clash)")
@click.option("-o", "--output", type=click.Path(dir_okay=False),
              help="输出文件 (默认覆盖当前 config.yaml)")
def sub(url: str, no_setup: bool, do_reload: bool, ua: str | None, output: str | None) -> None:
    """从订阅 URL 拉取并安装 Clash YAML 配置。"""
    cfg = _cfg()
    out_path = Path(output) if output else cfg.config_path
    try:
        content = sub_mod.download(url, ua=ua or sub_mod.CLASH_UA)
        sub_mod.identify_and_validate(content)
    except Exception as e:
        say(str(e), ok=False); sys.exit(1)

    sub_mod.install(content, out_path)
    say(f"写入 {out_path}  (节点 ~{sub_mod.node_count(out_path)})")

    if not no_setup and out_path == cfg.config_path:
        step("注入本机 mixed-port/secret/external-ui...")
        sub_mod.inject_local_settings(cfg)
        say("已注入")

    if do_reload:
        if not mihomo_pid():
            warn("--reload 需要 mihomo 在跑,跳过")
        else:
            try:
                api_mod.API(cfg).reload_config(str(out_path))
                say("已热加载")
            except Exception as e:
                say(f"热加载失败: {e},改为重启 mihomo", ok=False)
