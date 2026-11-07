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

# 2. 安装 Python 依赖（已装则跳过；PEP 668 受管环境下失败仅提示，不阻断安装）
echo "🐍 安装 Python 依赖 ..."
NEED_PKGS=""
while IFS= read -r pkg; do
    [ -z "$pkg" ] && continue
    case "$pkg" in \#*) continue ;; esac
    mod=$(echo "$pkg" | sed -E 's/[=<>! ].*//' | tr '-' '_')
    case "$mod" in
        pymupdf) mod=fitz ;;
        beautifulsoup4) mod=bs4 ;;
    esac
    python3 -c "import $mod" 2>/dev/null || NEED_PKGS="$NEED_PKGS $pkg"
done < "${INSTALL_DIR}/requirements.txt"

if [ -n "$NEED_PKGS" ]; then
    echo "   缺失依赖:$NEED_PKGS"
    if ! python3 -m pip install --quiet $NEED_PKGS 2> /tmp/bc_pip_err.log; then
        if grep -q "externally-managed-environment" /tmp/bc_pip_err.log; then
            echo "⚠️  系统 Python 受 PEP 668 保护，无法直接 pip install。"
            echo "   请手动安装缺失依赖后重跑："
            echo "     python3 -m pip install --user $NEED_PKGS"
            echo "   或使用虚拟环境 / pipx / brew 安装对应包。"
        else
            echo "⚠️  依赖安装失败（见下方错误），可手动安装后重跑："
            cat /tmp/bc_pip_err.log
            echo "     python3 -m pip install $NEED_PKGS"
        fi
    fi
else
    echo "   依赖已全部安装，跳过"
fi
rm -f /tmp/bc_pip_err.log

# 3. 验证依赖（可选依赖缺失仅提示能力降级，不阻断）
echo "✅ 验证依赖 ..."
python3 -c "from bs4 import BeautifulSoup; import lxml; print('   核心依赖 OK')" || {
    echo "❌ 核心依赖（beautifulsoup4/lxml）不可用，请按上方提示手动安装"
    exit 1
}
python3 -c "import mistune" 2>/dev/null || echo "   ℹ️  未装 mistune：Markdown 将用内置简易解析器（不支持表格插件）"
python3 -c "import fitz" 2>/dev/null || echo "   ℹ️  未装 pymupdf：PDF 翻译不可用（EPUB/Markdown 不受影响）"
python3 -c "import ebooklib" 2>/dev/null || echo "   ℹ️  未装 ebooklib：PDF 翻译组装不可用（EPUB 翻译用内置 zip 打包，不受影响）"

echo ""
echo "🎉 BookCraft 安装完成！"
echo "   位置：${INSTALL_DIR}"
echo "   现在可以对 AI 助手说「翻译 xxx.epub」开始使用了。"
