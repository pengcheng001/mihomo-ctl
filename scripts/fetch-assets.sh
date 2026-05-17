#!/usr/bin/env bash
# fetch-assets.sh — 下载 mihomo 二进制 + geo 数据 + 控制面板到 assets/
# 在 git clone 之后、python -m build 之前跑一次。
#
# 用法:
#   bash scripts/fetch-assets.sh              # 用默认版本(脚本顶部)
#   MIHOMO_VERSION=v1.18.10 bash scripts/fetch-assets.sh
#
# 依赖: curl, gzip, tar (绝大多数 Linux 都有)

set -euo pipefail

MIHOMO_VERSION="${MIHOMO_VERSION:-v1.18.0}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # 切到仓库根
mkdir -p assets/bin assets/config/ui

echo "→ 拉 mihomo ${MIHOMO_VERSION} (linux-amd64)"
curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-amd64-${MIHOMO_VERSION}.gz" \
    | gunzip > assets/bin/mihomo
chmod +x assets/bin/mihomo

echo "→ 拉 geoip.metadb"
curl -fsSL -o assets/config/geoip.metadb \
    https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb

echo "→ 拉 geoip.dat"
curl -fsSL -o assets/config/geoip.dat \
    https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.dat

echo "→ 拉 geosite.dat"
curl -fsSL -o assets/config/geosite.dat \
    https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat

echo "→ 拉 metacubexd dashboard"
curl -fsSL "https://github.com/MetaCubeX/metacubexd/archive/gh-pages.tar.gz" \
    | tar xz --strip-components=1 -C assets/config/ui

echo ""
echo "✓ 完成。现在可以构建 wheel:"
echo "    python -m build --wheel --no-isolation"
echo ""
echo "  最终大小:"
du -sh assets/bin/mihomo assets/config/*.dat assets/config/*.metadb assets/config/ui
