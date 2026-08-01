#!/usr/bin/env bash
#
# BookCraft 一键安装脚本
#
# 用法：
#   远程一键安装：  curl -fsSL https://raw.githubusercontent.com/pf711-dev/BookCraft-Skill/main/install.sh | bash
#   本地运行：      ./install.sh
#
# 脚本会：复制 BookCraft 到 ~/.agents/skills/BookCraft → 安装 Python 依赖 → 验证环境

set -euo pipefail

SKILLS_DIR="${HOME}/.agents/skills"
INSTALL_DIR="${SKILLS_DIR}/BookCraft"
REPO_URL="https://github.com/pf711-dev/BookCraft-Skill.git"
TMP_DIR=""

cleanup() {
    [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# 判断运行模式：脚本作为真实文件存在 → 本地模式（源 = 脚本所在目录）；
# 否则（curl | bash）→ 远程模式（从 GitHub clone）
SELF="${BASH_SOURCE[0]:-$0}"
if [ -f "$SELF" ] && [ "${SELF##*/}" = "install.sh" ]; then
    SRC_DIR="$(cd "$(dirname "$SELF")" && pwd)"
else
    echo "📦 远程模式：从 GitHub 克隆 BookCraft ..."
    TMP_DIR="$(mktemp -d)"
    git clone --depth 1 "$REPO_URL" "$TMP_DIR/BookCraft"
    SRC_DIR="$TMP_DIR/BookCraft"
fi

# 前置检查：python3
command -v python3 >/dev/null 2>&1 || {
    echo "❌ 未找到 python3，请先安装 Python ≥ 3.9（https://www.python.org/）"
    exit 1
}

# 1. 复制到 skills 目录
echo "📂 安装到 ${INSTALL_DIR} ..."
mkdir -p "$SKILLS_DIR"
rm -rf "$INSTALL_DIR"
cp -R "$SRC_DIR" "$INSTALL_DIR"
rm -rf "${INSTALL_DIR}/.git"   # skills 目录无需保留 git 历史

# 2. 安装 Python 依赖
echo "🐍 安装 Python 依赖 ..."
python3 -m pip install -r "${INSTALL_DIR}/requirements.txt"

# 3. 验证依赖
echo "✅ 验证依赖 ..."
python3 -c "from bs4 import BeautifulSoup; import lxml; print('依赖检查通过')"

echo ""
echo "🎉 BookCraft 安装完成！"
echo "   位置：${INSTALL_DIR}"
echo "   现在可以对 AI 助手说「翻译 xxx.epub」开始使用了。"
