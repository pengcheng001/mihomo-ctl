# mihomo-ctl

> 一个命令搞定 mihomo (Clash.Meta) 的安装、启停、订阅、节点切换、限速 —— 替代 Clash Verge GUI 的命令行方案。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20x86__64-lightgrey.svg)]()

## 为什么有这个项目

[mihomo](https://github.com/MetaCubeX/mihomo)（原 Clash.Meta）功能强大但官方只提供一个内核二进制，桌面端用户基本都用 [Clash Verge](https://github.com/clash-verge-rev/clash-verge-rev) GUI 包一层。但服务器、无 GUI 环境、想脚本化的场景 GUI 不顺手——之前要么自己写一堆 bash，要么干跑裸 mihomo。

**mihomo-ctl 把这些事打包进一个 `pip install` 就能用的 CLI**：

- ✅ 自带内核：wheel 内置 mihomo 二进制 + geoip/geosite + Web UI，**不用额外下载**
- ✅ 一键 install：解二进制、写配置、装 shell 补全、setcap、抑制 polkit 弹框、systemd 单元，**全自动**
- ✅ 两种模式都好用：`mhctl on`（mixed-port，CLI + GUI 都走代理）/ `mhctl tun on`（路由层透明接管，Docker/Java 全覆盖）
- ✅ 完整 Tab 补全：子命令 / 选项 / 节点组名 / 节点名（动态查 mihomo API）
- ✅ 浏览器 Web UI 直通：`mhctl ui open` 一键带 secret 跳转
- ✅ 下行限速：`mhctl limit on 200kbps` 防共享机被偷下载

## 一行 TL;DR (English)

> A self-contained CLI wrapper for mihomo (Clash.Meta) proxy. Bundles the
> binary + geo data + web UI in the wheel. One `mhctl install` configures
> everything (shell completion, setcap, polkit, NetworkManager). Linux x86_64 only.

## 快速上手 (4 步)

```bash
# 1. 装 wheel (pip 会自动装 click/httpx/pyyaml/rich 等依赖)
pip install --user mihomo_ctl-0.1.0-py3-none-linux_x86_64.whl

# 2. 一键部署 (会用 sudo 写 setcap/NM/polkit,中间最多输 1 次密码)
mhctl install

# 3. 让 Tab 补全 + 自动 export http_proxy 在当前终端生效
source ~/.bashrc

# 4. 拉订阅 → 启动
mhctl sub 'https://你的机场.com/subscribe?token=xxx'
mhctl on            # 或者 mhctl tun on (TUN 模式)
```

完事后 `mhctl <TAB>` 看所有子命令；`mhctl status` 看当前状态。

## 主要命令

```
mhctl install                 一键(展开内核+geo+UI,装 shell hook,setcap,polkit)
mhctl on / off / status       启停 mixed-port (CLI 走 env, GUI 走 GNOME 系统代理)
mhctl tun on / off / clean    启停 TUN 模式 (路由层透明,Docker/Java 都走)
mhctl node list/switch/test   节点列表 / 切换 / 测延迟
mhctl limit on 200kbps        下行限速 (上传不限)
mhctl sub <URL>               拉订阅 (自动注入本机 port/secret)
mhctl ui no-auth/open/url     Web 面板免密 / 浏览器打开 / 复制 URL
mhctl systemd install/enable  开机自启 (systemd user unit)
mhctl env on/off              打印 export/unset 行 (供 eval)
mhctl sysproxy on/off/status  手动开关 GNOME 系统代理
```

完整帮助：`mhctl --help`，子命令帮助：`mhctl <子命令> --help`。

## `mhctl install` 都做了什么

| # | 做的事 | 路径 | 需要 sudo |
|---|---|---|---|
| 1 | 解 mihomo 二进制 + geoip/geosite + UI | `~/.local/share/mihomo-ctl/mihomo/` | 否 |
| 2 | 写本机配置 | `~/.config/mihomo-ctl/config.toml` | 否 |
| 3 | 改 `~/.bashrc` 加 hook（Tab 补全 + on/off 改 env） | `~/.bashrc` | 否 |
| 4 | `setcap cap_net_admin,...` 让 mihomo 能建 TUN | mihomo 二进制 | ✓ |
| 5 | NetworkManager dropin 让 NM 忽略 `utun*/Meta` | `/etc/NetworkManager/conf.d/` | ✓ |
| 6 | polkit 规则放行 `resolvectl`（避免 TUN 启动弹密码框） | `/etc/polkit-1/...` | ✓ |
| 7 | stub config.yaml（没订阅时 `mhctl on` 也能起 UI） | mihomo 工作目录 | 否 |

每一步都有对应的 `--no-xxx` 跳过：

```bash
mhctl install --no-setcap --no-nm-unmanage --no-polkit-resolve --no-shell-hook
```

也可以一并装 systemd 单元自动启动：

```bash
mhctl install --systemd
mhctl systemd enable     # 开机自启 + 立刻启动
```

## 两种启停模式的差异

| 维度 | `mhctl on` (mixed-port) | `mhctl tun on` (TUN) |
|---|---|---|
| 工作原理 | HTTP/SOCKS 代理监听 `127.0.0.1:17890`，应用主动连 | 创建 `utun0` 网卡 + 修路由表，所有 TCP/UDP 透明接管 |
| 谁能走代理 | 读 `http_proxy` 环境变量的 CLI（curl/git/pip）<br>+ 读 GNOME 系统代理的 GUI（Chrome/Firefox/VSCode） | **所有进程**（Docker、Java、不认 env 的二进制都走） |
| 需要 root/capability | 否 | 需要 `cap_net_admin`（install 自动 setcap） |
| 启停副作用 | export/unset env vars + 改 GNOME 系统代理 | 无（路由层接管，不动 env） |
| 适合 | 笔记本日常 | 服务器、要让 Docker 走代理、不想配代理 env 的环境 |

两种模式**不能同时跑**（都监听 `mixed-port:17890`，端口冲突）。`mhctl on` / `mhctl tun on` 都会先停掉对方再启自己。

## Tab 补全示例

```bash
mhctl <TAB>                      # 子命令: install / on / off / tun / node / ...
mhctl tu<TAB>                    # 自动补 "tun "
mhctl node switch <TAB>          # 动态查 mihomo API 列出所有节点
mhctl node list -g <TAB>         # 列出所有节点组名(🚀节点选择/♻️自动选择/...)
mhctl limit on <TAB>             # 列常用速率值
```

## 配置文件

`~/.config/mihomo-ctl/config.toml`（`mhctl install` / `mhctl init` 自动生成）：

```toml
mihomo_dir = "/home/USER/.local/share/mihomo-ctl/mihomo"
mihomo_bin = "/home/USER/.local/share/mihomo-ctl/mihomo/bin/mihomo"
config_file = "config.yaml"
log_file = "mihomo.log"
api_port = 9090
api_secret = "mypassword123"
api_bind = "0.0.0.0"              # 改 127.0.0.1 后局域网访问不到 API
proxy_host = "127.0.0.1"
proxy_port = 17890
```

修改后 `mhctl setup` 把端口/secret 同步到 mihomo 的 `config.yaml`。

## 限速

mixed-port 模式下，给 `lo` 网卡按源端口 17890 做 HTB 限速，**只限"mihomo→应用"方向**，国内直连、SSH、本地数据库等其它流量不受影响：

```bash
mhctl limit on 200kbps    # 200 KB/s 下行 (=1600 Kbit)
mhctl limit on 1mbps      # 1 MB/s
mhctl limit on 5mbit      # 5 Mbit/s ≈ 600 KB/s
mhctl limit off
mhctl limit status        # 看实时 Sent 字节数
```

注意 `kbps` 在 tc 约定里是 *KiloByte*/s，`kbit` 才是 KiloBit/s。

## 安装

### 从 wheel 装（推荐）

```bash
pip install --user mihomo_ctl-0.1.0-py3-none-linux_x86_64.whl
```

wheel **只支持 Linux x86_64**，因为内置的 mihomo 二进制是该平台的 ELF。其它平台 pip 会直接拒绝。

### 从源码构建

```bash
git clone https://github.com/pengcheng001/mihomo-ctl.git
cd mihomo-ctl

# 1. 拉 bundled 资源 (mihomo 二进制 + geo 数据 + 控制面板)
#    放到 assets/ 下 (gitignored,默认 v1.18.0,改 MIHOMO_VERSION 覆盖)
bash scripts/fetch-assets.sh

# 2. 构建 wheel
pip install hatchling build
python -m build --wheel --no-isolation

# 3. wheel 在 dist/ 下,直接装
pip install --user dist/mihomo_ctl-0.1.0-py3-none-linux_x86_64.whl
```

### 开发模式（editable install）

```bash
pip install --user -e .
# 注意:editable 模式下 _assets/ 里没有 bundled 二进制,
# mhctl install 会报"找不到 _assets/bin/mihomo"。
# 开发时直接用现有 ~/.local/share/mihomo-ctl/ 里的二进制即可。
```

## 架构

```
src/mihomo_ctl/
├── __init__.py        版本号
├── __main__.py        python -m mihomo_ctl 入口
├── cli.py             click 入口 + 所有子命令定义
├── config.py          ~/.config/mihomo-ctl/config.toml 读写 (TOML)
├── api.py             mihomo external-controller REST API 客户端 (httpx)
├── process.py         start/stop mihomo,等 TUN 设备建立,日志中提取 fatal
├── installer.py       mhctl install 的全部步骤 (setcap/NM/polkit/stub)
├── subscription.py    拉订阅 + 校验 Clash YAML + 注入本机 port/secret
├── limit.py           tc 限速 (HTB on lo,按源端口过滤)
├── sysproxy.py        GNOME 系统代理 (gsettings)
├── shell.py           init-shell 生成的 hook (Tab 补全 + on/off 改 env)
├── utils.py           rich console, mihomo_pid, sudo wrapper
└── _assets/           wheel 打包时通过 hatchling force-include 注入:
                       _assets/bin/mihomo
                       _assets/config/{geoip.metadb,geoip.dat,geosite.dat}
                       _assets/config/ui/
                       _assets/config/tun-overlay.yaml
                       _assets/config/config.yaml.example  (源码自带)
```

## 已知限制

- **只支持 Linux x86_64**：内置二进制是该平台的 ELF，其它平台 pip 直接拒绝。如要在 ARM/macOS 用，手动放新二进制，`mhctl init --mihomo-bin /path/to/mihomo`。
- **GUI 应用走代理只支持 GNOME**：靠 `gsettings` 改 `org.gnome.system.proxy`。KDE/i3 等用户用 TUN 模式或自己改浏览器代理设置。
- **polkit / NetworkManager 配置只支持 Ubuntu / Debian 家族**：其它发行版的 polkit 路径或 NM 配置位置可能不同；用 `--no-polkit-resolve --no-nm-unmanage` 跳过自动配置自己手动加。
- **`mhctl on` / `mhctl tun on` 端口冲突**：都监听 `mixed-port:17890`，只能二选一。
- **某些机场只给 base64 V2Ray 订阅**：`mhctl sub` 只支持 Clash YAML，base64 链接需要先用 [subconverter](https://github.com/tindy2013/subconverter) 转换。

## FAQ

**Q: `mhctl on` 输出说启动成功，但浏览器还是不通**
A: `mixed-port` 模式只覆盖读 `http_proxy` 的 CLI 工具。我会自动设 GNOME 系统代理给 GUI 用，但**已经打开的浏览器进程可能不读新配置**——关掉浏览器重开。或者直接 `mhctl tun on` 走路由层透明接管。

**Q: 启动 TUN 弹密码框**
A: 跑 `mhctl install --force` 把 polkit + NM 配置写好。如果还弹，看 `journalctl --since "1 min ago" | grep polkit`，把输出贴出来。

**Q: `mhctl status` 显示 API 不可达**
A: secret 不匹配。`mhctl init --secret <你机场 config.yaml 里的 secret>` 或直接 `mhctl ui set-auth <新 secret>` 一行重设并热加载。

**Q: 想去掉密码登录 Web UI**
A: `mhctl ui no-auth`——清 secret + 绑 127.0.0.1（只能本机访问，没密码也安全）。

**Q: 换机器**
A: 把 wheel `scp` 过去，对方 `pip install + mhctl install + mhctl sub <URL>` 三条命令即可。配置不会跟过去（每台机器独立的 `~/.config/mihomo-ctl/config.toml`）。

## 卸载

```bash
mhctl off                          # 停 mihomo
mhctl systemd uninstall            # 删 systemd unit (如果装了)
pip uninstall mihomo-ctl           # 卸 Python 包
rm -rf ~/.local/share/mihomo-ctl   # 删二进制 + geo 数据
rm -rf ~/.config/mihomo-ctl        # 删本机配置

# 删 shell hook
sed -i '/# >>> mihomo-ctl >>>/,/# <<< mihomo-ctl <<</d' ~/.bashrc

# 删系统级配置 (需要 sudo)
sudo rm -f /etc/NetworkManager/conf.d/99-mihomo-unmanage-utun.conf
sudo rm -f /etc/polkit-1/rules.d/49-mihomo-resolve.rules
sudo rm -f /etc/polkit-1/localauthority/50-local.d/49-mihomo-resolve.pkla
sudo systemctl reload NetworkManager
```

## 致谢

- [mihomo (Clash.Meta)](https://github.com/MetaCubeX/mihomo) — 内核
- [metacubexd](https://github.com/MetaCubeX/metacubexd) — Web 控制面板
- [meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat) — geoip / geosite 数据
- [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) — GUI 思路参考

## License

MIT，见 [LICENSE](LICENSE)。

Bundled 的 mihomo / geo 数据 / metacubexd 各自保留原 license（GPL-3.0 / CC-BY-SA / MIT）。
