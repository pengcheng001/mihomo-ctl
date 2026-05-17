"""Shell integration: env var scripts + combined hook with completion + mh wrapper."""
from __future__ import annotations

from . import config as cfg_mod


def env_on_script(cfg: cfg_mod.Config) -> str:
    p = f"http://{cfg.proxy_host}:{cfg.proxy_port}"
    s = f"socks5://{cfg.proxy_host}:{cfg.proxy_port}"
    no = "localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"
    return "\n".join([
        f"export http_proxy={p!r}",
        f"export https_proxy={p!r}",
        f"export all_proxy={s!r}",
        f"export HTTP_PROXY={p!r}",
        f"export HTTPS_PROXY={p!r}",
        f"export ALL_PROXY={s!r}",
        f"export no_proxy={no!r}",
        f"export NO_PROXY={no!r}",
    ]) + "\n"


def env_off_script() -> str:
    vars_ = ["http_proxy", "https_proxy", "all_proxy",
             "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
             "no_proxy", "NO_PROXY"]
    return "unset " + " ".join(vars_) + "\n"


def init_script(shell: str) -> str:
    """完整 shell 初始化: Tab 补全 + `mh` 包装函数。

    用户用法:
        eval "$(mhctl init-shell bash)"
    """
    if shell == "bash":
        completion = '_MHCTL_COMPLETE=bash_source mhctl'
    elif shell == "zsh":
        completion = '_MHCTL_COMPLETE=zsh_source mhctl'
    else:
        raise ValueError(shell)

    return f"""# mihomo-ctl shell hook: Tab 补全 + `mhctl on/off` 顺带 export/unset 代理环境变量

# 1. click 内置 Tab 补全 (注册在 'mhctl' 名字上,完整支持子命令/选项/动态补全)
eval "$({completion})"

# 2. 用 shell 函数覆盖二进制 mhctl,on/off 时多做一步 eval 把 env 真正改到当前 shell。
#    其它子命令直接转发给 'command mhctl' (绕过函数本身,调用真二进制)。
mhctl() {{
    case "$1" in
        on)
            command mhctl on "${{@:2}}" || return $?
            eval "$(command mhctl env on)"
            ;;
        off)
            eval "$(command mhctl env off)"
            command mhctl off "${{@:2}}"
            ;;
        *)
            command mhctl "$@"
            ;;
    esac
}}
"""
