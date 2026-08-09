# macOS Claude VPS 客户端开发交接

本文档用于在 macOS 上继续开发 `mihomo-ctl` 的 Claude VPS 客户端。文档是公开资料，不包含任何部署专属地址、SSH 账号、密码、API key、OAuth token、证书私钥或本机运行状态。

## 1. 目标与范围

第一阶段只支持 macOS 上的 `claude-vps` 链路，不承诺 `mhctl`、Mihomo TUN、系统代理、限速等 Linux 功能可用。

目标链路：

```text
Claude Code
  │ HTTPS https://api.anthropic.com:443
  │ /etc/hosts 将域名严格解析到 127.0.0.1
  ▼
macOS 本机 TLS 反向代理
  │ 流式转发 HTTP/SSE 响应
  ▼
macOS 本机 SSH 端口转发
  │ ssh -L，本地回环端口 → VPS 回环端口
  ▼
VPS CLIProxyAPI
```

第一阶段明确不做：

- macOS 版 Mihomo 内核安装与管理。
- macOS TUN、路由、DNS 接管和系统代理。
- Linux 的 systemd、loginctl、sysctl、setcap、polkit、NetworkManager、`tc` 等功能移植。
- 在 wheel、源码、测试夹具或日志中写入真实部署参数。

推荐将首个 macOS 预览版定为 `0.3.0`，不要覆盖已经发布的 Linux v0.2.1。

## 2. 当前可用基线

Linux x86_64 基线版本：v0.2.1。

- Release：<https://github.com/pengcheng001/mihomo-ctl/releases/tag/v0.2.1>
- Wheel：<https://github.com/pengcheng001/mihomo-ctl/releases/download/v0.2.1/mihomo_ctl-0.2.1-py3-none-linux_x86_64.whl>
- SHA-256：`5634ee249015514506d8302ae0ee8fa74be58c28cc3ec5e11ddaf3f34ae98274`

v0.2.1 已完成：

- SSH 本地转发与自动重连。
- 本机 HTTPS:443 反向代理。
- `/etc/hosts`、系统 CA、低端口设置和用户服务的可逆安装。
- 独立的运行时配置、TLS 私钥和 API key 文件。
- Bash Tab 补全。
- SSE/chunked 真流式转发；不会等待完整响应后再一次性返回。
- 延迟 SSE 回归测试，以及真实 VPS 链路验证。

流式响应的单位是上游 `text_delta`，不保证逐字符显示。不要在 macOS 代理中人为拆字或加入“打字机延时”。

## 3. 源码可见性说明

GitHub `main` 当前有意不包含 v0.2.x Claude VPS 的完整开发源码。本 Release 的自动生成源码压缩包也不是 v0.2.1 wheel 的实现来源。

macOS 可以下载 Linux wheel 并把它当 ZIP 解压，不需要、也不能在 macOS 上安装这个 Linux wheel：

```bash
mkdir -p "$HOME/Developer/mihomo-ctl-wheel-reference"
cd "$HOME/Developer/mihomo-ctl-wheel-reference"

curl -fLO \
  https://github.com/pengcheng001/mihomo-ctl/releases/download/v0.2.1/mihomo_ctl-0.2.1-py3-none-linux_x86_64.whl

shasum -a 256 mihomo_ctl-0.2.1-py3-none-linux_x86_64.whl
python3 -m zipfile -e \
  mihomo_ctl-0.2.1-py3-none-linux_x86_64.whl \
  wheel-unpacked
```

重点参考文件：

```text
wheel-unpacked/
├── mihomo_ctl/claude_vps.py
├── mihomo_ctl/claude_vps_system.py
└── mihomo_ctl/_assets/claude_vps/
    ├── claude-vps
    └── cliproxy-443-proxy.py
```

wheel 不含开发测试目录。如果需要当前完整源码和测试，应通过明确授权的私有传输方式移动本机 Git 提交；不要把本地未公开资料、运行时配置或密钥文件上传到公开分支。

## 4. 建议的 Mac 开发起点

在 Mac 上查看本交接分支：

```bash
git clone https://github.com/pengcheng001/mihomo-ctl.git
cd mihomo-ctl
git switch --track origin/agent/macos-claude-vps-handoff
open MACOS-CLAUDE-VPS-HANDOFF.md
```

开始实现时从交接分支建立功能分支：

```bash
git switch -c feature/macos-claude-vps
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

推荐最低目标：

- macOS 13 或更新版本。
- Python 3.9 或更新版本。
- Apple Silicon arm64 与 Intel x86_64。
- Claude Code、OpenSSH、OpenSSL 或等价证书生成能力。

Mac 不保证预装满足要求的 Python。安装流程必须在修改系统前检查依赖并给出明确提示。

## 5. 推荐代码结构

不要在现有 Linux helper 中堆叠大量 `if Darwin`。保留通用编排和流式代理，拆分系统副作用：

```text
src/mihomo_ctl/
├── claude_vps.py                 # console entry point 和通用编排
├── claude_vps_platform.py        # 平台接口与选择
├── claude_vps_linux.py           # Linux systemd/CA/hosts/低端口实现
└── claude_vps_macos.py           # macOS launchd/Keychain/hosts/端口实现

scripts/
├── claude-vps                    # 逐步缩小；最终优先迁移到 Python
└── cliproxy-443-proxy.py         # 通用流式 HTTP/TLS 代理
```

平台模块至少提供：

- `install_system_settings()`
- `uninstall_system_settings()`
- `system_status()`
- `install_service()`
- `start_service()` / `stop_service()` / `restart_service()`
- `service_status()` / `service_logs()`
- `resolve_api_host()`
- `is_listening()`

OS 特定副作用必须留在平台 helper；CLI 只负责编排、提示和结果展示。

## 6. 为什么不应直接复用现有 Bash

macOS 自带 `/bin/bash` 通常是旧版 Bash，而当前 Linux 脚本使用了 Bash 4+ 或 Linux 专属能力，包括：

- `mapfile`，包括 NUL 分隔参数读取。
- `/proc/<pid>/cmdline`。
- `systemctl`、`loginctl`。
- `getent`、`ss`、GNU `timeout`。
- Linux sysctl drop-in 与 CA 更新工具。
- `readlink -f` 等 GNU/Linux 行为。

优先把进程发现、端口探测、配置读写、服务编排迁移到 Python。不要把 Homebrew Bash 设为最终用户的隐式强制依赖；如果开发早期临时依赖 Homebrew Bash，必须在帮助和安装前置检查中明确说明。

Mac 替代方案：

| Linux 能力 | macOS 方案 |
|---|---|
| systemd user unit | `launchd` LaunchAgent |
| `/proc/<pid>/cmdline` | launchd 状态、PID 文件或 `ps`，并校验进程所有者 |
| `ss` / `/dev/tcp` | Python `socket` 探测；诊断可辅以 `lsof` |
| `getent` | Python `socket.getaddrinfo()`；诊断可辅以 `dscacheutil` |
| `sha256sum` | Python `hashlib` 或 `shasum -a 256` |
| Linux CA 目录 | macOS System Keychain |
| systemd journal | LaunchAgent 标准输出/错误日志文件 |

## 7. launchd 服务设计

第一阶段使用当前登录用户的 LaunchAgent：

```text
~/Library/LaunchAgents/com.pengcheng001.claude-vps.plist
```

建议 label：

```text
com.pengcheng001.claude-vps
```

plist 要求：

- `ProgramArguments` 使用绝对路径数组，不经过 shell 字符串拼接。
- 不在 plist 中保存 API key、OAuth token、SSH 密码或 TLS 私钥内容。
- `RunAtLoad` 启动监督进程。
- 使用审慎的 `KeepAlive`，避免配置错误导致高频重启。
- `StandardOutPath` 和 `StandardErrorPath` 指向用户可写、权限受控的日志目录。
- 服务进程以前台模式运行，由 launchd 监督；不要自行 daemonize。
- SSH 与本机代理作为受监督子进程，退出时完整回收。

开发验证命令可基于：

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.pengcheng001.claude-vps.plist"

launchctl kickstart -k \
  "gui/$(id -u)/com.pengcheng001.claude-vps"

launchctl print \
  "gui/$(id -u)/com.pengcheng001.claude-vps"

launchctl bootout \
  "gui/$(id -u)/com.pengcheng001.claude-vps"
```

安装代码必须把“未安装”“已安装但停止”“运行中”和“反复失败”区分开。执行 `bootstrap` 前先检查现状，卸载必须幂等。

LaunchAgent 只在用户会话中运行。如果未来要求未登录时也持续工作，需要单独设计 LaunchDaemon 和最小权限模型，不能简单把整个客户端以 root 运行。

## 8. 443 端口是首要设计门槛

不要假设所有 macOS 版本都允许普通用户绑定 443，也不要照搬 Linux 的 `net.ipv4.ip_unprivileged_port_start`。

安装时先用短生命周期 Python socket 实际探测：

```python
import socket

sock = socket.socket()
try:
    sock.bind(("127.0.0.1", 443))
finally:
    sock.close()
```

设计顺序：

1. 普通用户能绑定 443：使用 LaunchAgent，保持整个运行链路为普通用户。
2. 返回权限错误：停止安装并进入经过审核的 macOS 专用方案。
3. 不要让用户执行 `sudo claude-vps run`；这会扩大密钥、配置和子进程的 root 权限范围。

候选的特权方案包括最小 LaunchDaemon、launchd socket activation，或受控的本机端口重定向。选定前必须在真实 Mac 上验证：

- 只监听 `127.0.0.1`，不能暴露到 LAN。
- root 组件不读取 API key 或 SSH 私钥。
- 安装与卸载都记录并核对原状态。
- 崩溃、升级和睡眠唤醒后不会残留重定向规则。
- 与 VPN、防火墙和已有 443 服务冲突时明确失败，不覆盖外部配置。

这是 macOS 第一阶段的 go/no-go 检查项。

## 9. hosts 与 DNS 缓存

目标是让 `api.anthropic.com` 唯一解析为 `127.0.0.1`。

沿用 Linux 实现的安全原则：

- 修改前检查冲突映射；存在其它有效地址时拒绝继续。
- 只维护带唯一 marker 的块，或在已知旧 marker 中原位启用。
- 保存原始文件属性和结尾换行状态。
- 使用临时文件、权限校验和原子替换。
- 卸载只撤销本工具拥有且内容摘要一致的修改。
- 不改动 localhost、机器名、IPv6 或其它 hosts 记录。

Mac 可使用 Python `socket.getaddrinfo()` 做运行时严格检查，并使用 `dscacheutil` 辅助诊断。修改 hosts 后是否需要刷新 DNS 缓存，应在目标 macOS 版本实测；不要把缓存刷新失败误报成 hosts 写入成功。

## 10. TLS 证书与 Keychain

继续为 `api.anthropic.com` 生成带 Subject Alternative Name 的本机证书。安全要求：

- 私钥只保存在用户配置目录，权限 `0600`。
- sudo/helper 只接收公开证书，不复制或读取私钥。
- 系统 Keychain 中只安装公开证书。
- 安装前计算并记录证书指纹。
- 卸载只删除本工具创建、指纹仍一致的证书。
- 如果存在同名但不同指纹证书，拒绝猜测或批量删除。
- TLS 健康检查必须使用系统信任，不使用 `-k` 或禁用证书校验。

应同时验证：

- Python `ssl.create_default_context()` 能验证本机证书。
- `curl https://api.anthropic.com/...` 能验证本机证书。
- Claude Code/Node 信任该证书；必要时继续设置只指向公开证书的 `NODE_EXTRA_CA_CERTS`。

Keychain 的安装、查询和精确卸载命令必须在 Mac 上通过 `man security` 验证后编码，并为每个命令编写失败回滚测试。

## 11. 运行时目录与权限

推荐使用 `platformdirs` 选择 macOS 目录，不要把 macOS 路径硬塞进 Linux 常量。

建议布局：

```text
~/Library/Application Support/claude-vps/
├── config
├── claude.env
└── tls/
    ├── cert.pem
    └── key.pem

~/Library/Logs/claude-vps/
├── service.log
└── proxy.log

~/Library/LaunchAgents/
└── com.pengcheng001.claude-vps.plist
```

路径包含空格，所有 subprocess 调用必须使用参数数组，禁止依赖 shell 字符串转义。

配置原则：

- `config`、`claude.env`、TLS 私钥：`0600`。
- 配置目录：仅当前用户可访问。
- 公开证书可为 `0644`。
- 日志不得记录 Authorization、x-api-key、完整请求正文或远端部署地址。
- API key 不出现在进程命令行；优先通过内存、标准输入或权限受控环境文件传递。

## 12. SSH 与进程监督

继续使用系统 `/usr/bin/ssh` 和这些基本约束：

- `BatchMode=yes`
- `ConnectTimeout`
- `ServerAliveInterval`
- `ServerAliveCountMax`
- `TCPKeepAlive=yes`
- `ExitOnForwardFailure=yes`
- 本地和远端目标都绑定回环地址

不要通过模糊 `pkill -f` 停止进程。优先由 launchd 回收进程组；如果使用 PID 文件，必须同时验证 PID 所有者、可执行文件和完整参数结构，防止误杀无关进程。

应测试：

- SSH 密钥认证成功与失败。
- VPS 暂时不可达后的退避重连。
- 网络切换、Wi-Fi 断开/恢复。
- Mac 睡眠与唤醒。
- 端口已占用时清晰失败。
- 服务停止后没有遗留 SSH 或代理进程。

## 13. 流式代理不可回退

`cliproxy-443-proxy.py` 是跨平台公共组件。移植时必须保留 v0.2.1 行为：

- 收到上游响应头后立即向客户端发送响应头。
- 使用 `HTTPResponse.read1()` 读取可用数据，不调用完整 `resp.read()`。
- 有可靠 `Content-Length` 时保留长度并逐块转发。
- 上游 chunked 或长度未知时，重新生成合法 HTTP/1.1 chunked 帧。
- 每个转发块后 `flush()`。
- 过滤 hop-by-hop header，包括 `Connection` 声明的动态 header。
- 正确处理 HEAD、1xx、204、304。
- 客户端断开或流中断后关闭连接，不伪造完整终止块。
- 不把内部异常或敏感 header 返回给客户端。

测试不能只检查 `Transfer-Encoding: chunked`。延迟 SSE fixture 必须：

1. 上游发送第一个事件并 flush。
2. 上游暂停，尚未发送第二个事件或结束块。
3. 断言客户端已收到第一个事件。
4. 释放上游，收到第二个事件和正常结束。

真实输出可能按 token 或短语分段；验收标准是“首事件早于完整结束”，不是“逐字显示”。

## 14. zsh 与命令补全

macOS 默认交互 shell 是 zsh。现有 Bash completion 不能算作 Mac 支持完成。

需要新增：

- `claude-vps completion zsh`
- `claude-vps completion install`
- zsh 用户级补全目录安装与卸载
- 顶层命令、嵌套子命令和选项补全
- 路径参数补全，尤其是 SSH identity

安装过程不得无提示修改 `.zshrc`。优先安装到标准用户补全目录并输出一次性加载说明；如果必须修改 shell 配置，需要预览、marker、幂等写入和可逆卸载。

## 15. Wheel 与构建策略

当前 build hook 固定生成 `linux_x86_64`，且 Linux wheel 强制包含 Linux Mihomo 二进制和 UI/geo 资产。Mac 构建不能只修改文件名标签。

第一阶段需要先选择：

### 方案 A：同一发行包，多平台 wheel

- Linux wheel 保持现有完整 `mhctl + claude-vps`。
- macOS wheel 只包含可用的 Claude VPS 资产。
- `mhctl` 在 macOS 上明确提示第一阶段不支持，不能执行 Linux 副作用。
- 构建配置按目标平台选择 force-include。

### 方案 B：拆出独立 Claude VPS 包

- 共享代理和配置模块。
- Linux/macOS 各自实现平台 helper。
- 避免 Mac 用户安装一个名为 `mihomo-ctl`、但主要 Mihomo 功能不可用的包。

方案 B 长期边界更清晰；方案 A 对当前仓库改动较小。选定前先做一个最小 macOS wheel 原型。

PyPA macOS platform tag：

- `macosx_<version>_arm64`
- `macosx_<version>_x86_64`
- `macosx_<version>_universal2`，仅在所有随包二进制都同时支持 arm64/x86_64 时使用

如果第一阶段 wheel 只含 Python/Bash 资产，仍应明确限制为 macOS，避免被 Linux 或 Windows 错装。不要继续使用 `linux_x86_64` build hook。

构建后检查：

```bash
python3 -m zipfile -l "dist/<macOS-wheel>.whl"
python3 -m pip debug --verbose
python3 -m pip install --force-reinstall "dist/<macOS-wheel>.whl"
```

分别在原生 arm64 和原生 x86_64 环境安装；只在 Rosetta 下成功不算双架构支持。

## 16. CI 与真实 Mac 测试

截至本文编写时，GitHub-hosted runner 文档列出了 arm64 macOS runner，以及显式 Intel runner。开始实现前再次核对官方 runner label。

建议 CI 至少覆盖：

```yaml
strategy:
  matrix:
    os:
      - macos-15
      - macos-15-intel

runs-on: ${{ matrix.os }}
```

每个 job 首先输出并断言：

```bash
sw_vers
uname -m
python3 --version
```

CI 适合：

- Python 单元测试。
- 延迟 SSE 回环测试。
- plist 生成与 `plutil -lint`。
- wheel 构建、标签和隔离安装。
- 无密钥静态审计。

仍需真实 Mac 手工集成测试：

- System Keychain 信任与精确卸载。
- `/etc/hosts` 修改、冲突与恢复。
- 443 权限策略。
- LaunchAgent 登录、注销和重启行为。
- 睡眠/唤醒和 Wi-Fi 切换。
- Claude Code 真实 SSE 链路。

真实 VPS 参数只能通过 CI secret 或本机权限文件注入，且默认不要在公共 PR CI 中运行真实链路。

## 17. 安全与隐私红线

不得提交或打包：

- VPS IP、域名、SSH 用户名或 SSH identity 绝对路径。
- 密码、API key、OAuth token、Authorization header。
- TLS 私钥、真实证书副本或 Keychain 导出物。
- 用户运行时 config/env/state/log。
- 私人订阅 URL、代理账号或生成配置。
- 本地未公开文档、终端记录和诊断输出。

必须做到：

- 所有部署参数由用户安装时输入，或从权限 `0600` 的本机配置读取。
- 帮助、README、测试和日志只使用 `<VPS_HOST>`、`<SSH_USER>` 等占位符。
- wheel 构建后扫描成员名、私钥 PEM、token 特征和环境专属字面值。
- 证书私钥永不传给 sudo helper。
- 卸载前核对所有权、摘要和当前内容；外部修改后拒绝误删。
- PR 只暂存明确文件，不使用无审核的 `git add -A`。

## 18. 建议测试清单

### 无 root 单元测试

- 平台选择：Darwin 进入 macOS helper，Linux 行为不变。
- 路径包含空格、Unicode 和 shell 元字符。
- 配置权限和原子写入。
- LaunchAgent plist 内容、绝对参数与无密钥检查。
- SSH 参数数组和 identity 可选路径。
- 进程停止不会误杀无关进程。
- 固定长度、chunked、无长度、HEAD/204/304 响应。
- 客户端断开和上游中断。
- Bash/zsh completion。

### 可逆系统集成测试

- hosts 已正确、缺失、注释、冲突、外部修改四类状态。
- Keychain 中不存在、已有相同证书、同名不同证书。
- 安装中途失败时回滚 hosts/Keychain/launchd。
- 重复 install/uninstall 幂等。
- 443 已占用时不抢占、不杀进程。
- LaunchAgent start/stop/restart/status/logs。

### 发布前端到端测试

- 全新用户从 macOS wheel 安装。
- `claude-vps configure` 不写入密钥。
- `claude-vps install` 先预览，再请求 sudo。
- `api.anthropic.com` 唯一解析到回环地址。
- TLS 使用系统 CA 验证通过。
- SSH 隧道和本机 HTTPS 端口正常监听。
- `/v1/models` 健康检查通过。
- SSE 首事件早于完整结束。
- 服务重启、系统重启、睡眠唤醒后恢复。
- `claude-vps uninstall` 精确恢复系统状态。

## 19. 完成定义

只有同时满足以下条件，才可以声明 macOS 第一阶段可用：

- arm64 和 x86_64 的兼容 wheel 能被 pip 正常接受。
- 不依赖 Linux 命令或 `/proc`。
- 不要求用户把完整客户端作为 root 运行。
- hosts、Keychain、443 和 launchd 修改可预览、幂等、可回滚。
- 系统状态和密钥文件权限通过检查。
- 代理通过延迟 SSE 回归测试和真实 Claude Code 测试。
- zsh 补全可安装和卸载。
- wheel/README/日志隐私扫描为零。
- Linux x86_64 的现有 38 项测试不回退。
- 在一台真实 Apple Silicon Mac 和一台真实/CI Intel Mac 上完成安装验证。

## 20. 推荐实施顺序

1. 在 Mac 上解压 v0.2.1 wheel，并阅读四个 Claude VPS 文件。
2. 建立平台接口，把 Linux 行为锁进回归测试。
3. 先让通用流式代理在 macOS 高端口通过测试。
4. 实现 macOS 路径、依赖检查和 socket 端口探测。
5. 实现 LaunchAgent，不涉及 root。
6. 实现 hosts 和 Keychain 的预览、状态、安装、回滚、卸载。
7. 决定并验证 443 最小权限方案。
8. 新增 zsh completion。
9. 实现 macOS wheel 选择性打包与标签。
10. 跑双架构 CI、真实 Mac 集成测试和隐私审计。
11. 发布预览版，保留 Linux v0.2.1 下载与升级路径。

## 21. 官方参考

- [Apple：Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
- [Apple：Service Management](https://developer.apple.com/documentation/servicemanagement/)
- [Apple：Change certificate trust policies on Mac](https://support.apple.com/guide/mac-help/change-certificate-trust-policies-on-mac-mchlp2824/mac)
- [GitHub：GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [PyPA：Platform compatibility tags](https://packaging.python.org/specifications/platform-compatibility-tags/)

---

交接原则：先保持 Linux 行为不回退，再增加经过真实 Mac 验证的独立平台实现；任何系统变更都必须可解释、可预览、可验证、可恢复。
