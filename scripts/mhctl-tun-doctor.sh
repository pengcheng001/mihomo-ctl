#!/usr/bin/env bash
# mhctl-tun-doctor.sh —— 在「mhctl tun on 之后上不了网」的机器上跑这个，定位到底断在哪一层。
# 全程只读 + 带超时的连通性探测，绝不修改任何网络/进程/配置/DNS。
# 最佳跑法：在出问题的机器上，先 `mhctl tun on`，确认断网，然后【保持断网状态】立刻跑本脚本：
#   bash mhctl-tun-doctor.sh
set +e

PROXY_HOST=127.0.0.1; PROXY_PORT=17890; API_PORT=9090; MIHOMO_DIR="$HOME/.local/share/mihomo-ctl/mihomo"
CONF="$HOME/.config/mihomo-ctl/config.toml"
if [ -f "$CONF" ]; then
  v=$(grep -E '^proxy_port' "$CONF" | grep -oE '[0-9]+');                 [ -n "$v" ] && PROXY_PORT=$v
  v=$(grep -E '^api_port'   "$CONF" | grep -oE '[0-9]+');                 [ -n "$v" ] && API_PORT=$v
  v=$(grep -E '^mihomo_dir' "$CONF" | sed -E 's/.*= *"([^"]+)".*/\1/');   [ -n "$v" ] && MIHOMO_DIR=$v
fi
LOG="$MIHOMO_DIR/mihomo.log"; BIN="$MIHOMO_DIR/bin/mihomo"
sec() { echo; echo "===== $* ====="; }
# 拿到任意非空且非 000 的 HTTP 码 = “字节确实出去又回来了” = 该层通
reachable() { [ -n "$1" ] && [ "$1" != "000" ]; }
# 透明探测：直连 IP 字面量 → 不经 DNS；返回 HTTP 码
http()  { curl -sS -o /dev/null -m "$1" -w '%{http_code}' "$2" 2>/dev/null; }
# 经 mixed-port 代理探测 → 绕开 TUN，单测节点
viaproxy() { curl -sS -o /dev/null -m "$1" -x "http://$PROXY_HOST:$PROXY_PORT" -w '%{http_code}' "$2" 2>/dev/null; }

sec "0. 环境"
. /etc/os-release 2>/dev/null; echo "发行版 : ${PRETTY_NAME:-?}"
echo "内核   : $(uname -r)"
echo "NetworkManager  : $(pgrep -x NetworkManager >/dev/null && echo 运行中 || echo 未运行)"
echo "systemd-resolved: $(systemctl is-active systemd-resolved 2>/dev/null | grep -q '^active' && echo 运行中 || echo 未运行)"
echo "/etc/resolv.conf nameserver:"; grep -E '^\s*nameserver' /etc/resolv.conf 2>/dev/null | head -3 | sed 's/^/  /'

sec "1. mihomo 进程数 (健康 = 恰好 1 个)"
N=$(pgrep -x mihomo | wc -l); echo "进程数: $N"; pgrep -a -x mihomo | sed 's/^/  /'

sec "2. TUN 设备 / 路由 / 策略"
TUN_DEV=$(ip -o link show 2>/dev/null | grep -oE '(utun[0-9]*|Meta|mihomo[0-9]*)' | head -1)
echo "TUN 接口: ${TUN_DEV:-<无>}"
[ -n "$TUN_DEV" ] && ip -br addr show "$TUN_DEV" 2>/dev/null | sed 's/^/  /'
echo "默认路由:"; ip route show default 2>/dev/null | sed 's/^/  /'
echo "ip rule (前 12 条):"; ip rule 2>/dev/null | head -12 | sed 's/^/  /'

sec "3. mihomo capabilities (TUN 需要 cap_net_admin)"
CAP=$(getcap "$BIN" 2>/dev/null); echo "  ${CAP:-<无 capability 或找不到二进制: $BIN>}"

sec "4. 节点直测 —— 绕开 TUN，直连 mixed-port($PROXY_PORT) (单测「节点本身通不通」)"
PX=$(viaproxy 8 "https://www.gstatic.com/generate_204")
echo "经代理取 generate_204 : HTTP=${PX:-超时/失败}   (拿到码=节点通)"

sec "5. 透明出口 —— 按 IP 直连，绕开 DNS (定位「路由/环路/出口网卡」)"
I1=$(http 6 "https://1.1.1.1/");   echo "透明连 1.1.1.1:443   : HTTP=${I1:-超时/失败}"
I2=$(http 6 "https://223.5.5.5/"); echo "透明连 223.5.5.5:443 : HTTP=${I2:-超时/失败}  (国内兜底)"

sec "6. DNS 解析 —— 按域名 (定位「DNS 劫持没生效」)"
G=$(getent hosts www.google.com 2>/dev/null | awk '{print $1}' | head -1); echo "getent www.google.com : ${G:-解析失败}"
D1=$(http 8 "https://www.gstatic.com/generate_204");  echo "透明按域名取 204      : HTTP=${D1:-超时/失败}"
if command -v resolvectl >/dev/null 2>&1; then
  echo "resolvectl 每链路 DNS:"; resolvectl status 2>/dev/null | grep -E 'Link |Current DNS|DNS Servers' | head -14 | sed 's/^/  /'
fi

sec "7. mihomo 日志近 20 行 error/fatal"
if [ -f "$LOG" ]; then grep -E 'level=(error|fatal)' "$LOG" 2>/dev/null | tail -20 | sed 's/^/  /'; else echo "  <无日志文件: $LOG>"; fi

# ---------- 自动分类 ----------
DNS_OK=false; { [ -n "$G" ] && reachable "$D1"; } && DNS_OK=true
sec "★ 诊断结论"
if [ -z "$TUN_DEV" ]; then
  echo "→ [A] TUN 设备根本没建起来。看第 3 节 setcap 是否缺 cap_net_admin、第 7 节日志 fatal。"
  echo "      修: sudo setcap 'cap_net_admin,cap_net_bind_service,cap_net_raw=+ep' $BIN"
elif [ "$N" -gt 1 ]; then
  echo "→ [B] 有 $N 个 mihomo 同时在跑(多实例冲突: 抢 $PROXY_PORT/$API_PORT + 抢路由表)。"
  echo "      多见于「装了 systemd 自启(install --systemd / mhctl systemd enable)」又手动 tun on。"
  echo "      验证/恢复: systemctl --user stop mihomo-ctl ; mhctl tun clean ; 然后只 mhctl tun on"
elif ! reachable "$PX"; then
  echo "→ [C] 连 mixed-port 直测都不通(HTTP=${PX:-失败}) → 是【节点/订阅/上游】本身的问题,跟 TUN 无关。"
  echo "      先 mhctl node test 换个能用的节点,确认 mhctl on 能上网,再回头试 TUN。"
elif ! reachable "$I1" && ! reachable "$I2"; then
  echo "→ [D] 节点通(代理直测 OK)但【透明按 IP 出不去】→ TUN 路由层问题。最可能:"
  echo "      · fwmark 回环: 去代理服务器的包又被绕回 utun(看第2节 ip rule 的 fwmark 0x80000 规则在不在)"
  echo "      · auto-detect-interface 选错出口网卡(多网卡/有 VPN/有 tailscale 时常见)"
  echo "      · 默认路由没正确改到 utun,或 strict-route 与 LAN 冲突。"
elif ! $DNS_OK; then
  echo "→ [E] 按 IP 能出去、但【按域名不行】→ 经典 DNS 劫持没生效:"
  echo "      mihomo 的 dns-hijack/fake-ip 没接管到系统 DNS。重点查 systemd-resolved(第6节):"
  echo "      · 非 Ubuntu/Debian 系: polkit/resolvectl 自动配置未覆盖(见 README 限制),需手动配"
  echo "      · 系统 DNS 走 127.0.0.53(lo) 时,查询不经 utun → 没被 hijack。"
else
  echo "→ [✓] 现在其实是通的(透明出口 + 域名解析都 OK)。可能没在断网态下跑,或问题已自愈。"
fi
echo; echo "(本脚本只读,未改动任何网络/进程/DNS。把 0-7 节完整贴回来可人工复核。)"
