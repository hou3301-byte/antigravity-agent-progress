#!/usr/bin/env bash
set -euo pipefail

repository=hou3301-byte/antigravity-agent-progress
release=v0.3.0
install_dir="$HOME/.gemini/config/plugins"
include_chinese=1
include_launcher=1

while (( $# )); do
  case "$1" in
    --progress-only)
      include_chinese=0
      include_launcher=0
      shift
      ;;
    --plugins-only)
      include_launcher=0
      shift
      ;;
    --workspace)
      if [[ $# -lt 2 || ! -d "$2" ]]; then
        printf '%s\n' '请给 --workspace 指定一个已存在的项目目录。' >&2
        exit 2
      fi
      install_dir="$(cd -- "$2" && pwd)/.agents/plugins"
      include_launcher=0
      shift 2
      ;;
    -h|--help)
      cat <<'HELP'
在 macOS 安装 Antigravity 中文启动入口和执行进度插件。

  bash install.sh                       安装全局插件和中文启动入口
  bash install.sh --plugins-only        只安装执行进度和中文规则
  bash install.sh --workspace <目录>     只安装插件到指定项目
  bash install.sh --progress-only       只安装执行进度

安装后从「Antigravity 中文」入口启动；已有同名入口会复用。
运行 uninstall.sh 可撤销本安装器管理的文件。
HELP
      exit 0
      ;;
    *)
      printf '未知参数 %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ $(uname -s) != Darwin ]] || ! /usr/bin/python3 --version >/dev/null 2>&1; then
  printf '%s\n' '当前安装包适用于 macOS，需要可用的 /usr/bin/python3。' >&2
  exit 1
fi

bundle_root=''
if [[ -n ${BASH_SOURCE[0]:-} ]]; then
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$script_dir/agent-progress/plugin.json" ]]; then
    bundle_root="$script_dir"
  fi
fi

if [[ -z $bundle_root ]]; then
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/antigravity-progress.XXXXXX")"
  trap 'rm -r -- "$temp_dir"' EXIT
  bundle_root="$temp_dir/bundle"
  mkdir "$bundle_root"
  /usr/bin/curl -fsSL "https://codeload.github.com/$repository/tar.gz/refs/tags/$release" -o "$temp_dir/bundle.tar.gz"
  /usr/bin/tar -xzf "$temp_dir/bundle.tar.gz" -C "$bundle_root" --strip-components 1
fi

/usr/bin/python3 "$bundle_root/scripts/package.py" install "$install_dir" "$bundle_root" "$include_chinese" "$include_launcher"
printf '卸载入口 %s\n' "${install_dir%/*}/antigravity-agent-progress-install/uninstall.sh"
