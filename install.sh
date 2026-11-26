#!/usr/bin/env bash
set -euo pipefail

repository=hou3301-byte/antigravity-agent-progress
install_dir="$HOME/.gemini/config/plugins"
include_chinese=1
include_launcher=1
bundle_root=''
upgrade=0
explicit_scope=0

if [[ -n ${BASH_SOURCE[0]:-} ]]; then
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$script_dir/agent-progress/plugin.json" ]]; then
    bundle_root="$script_dir"
  elif [[ ${BASH_SOURCE[0]##*/} == upgrade.sh && -f "$script_dir/install.json" ]]; then
    install_dir="${script_dir%/*}/plugins"
  fi
fi

while (( $# )); do
  case "$1" in
    --full)
      install_dir="$HOME/.gemini/config/plugins"
      include_chinese=1
      include_launcher=1
      explicit_scope=1
      shift
      ;;
    --progress-only)
      include_chinese=0
      include_launcher=0
      explicit_scope=1
      shift
      ;;
    --plugins-only)
      include_launcher=0
      explicit_scope=1
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
  bash install.sh --full                切换到完整安装，包含额度面板
  bash install.sh --plugins-only        只安装执行进度和中文规则
  bash install.sh --workspace <目录>     只安装插件到指定项目
  bash install.sh --progress-only       只安装执行进度
  bash 安装记录目录/upgrade.sh           按原安装范围升级到最新正式版

安装后从「Antigravity 中文」入口启动。已有同名入口会复用。
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

if [[ -z $bundle_root ]]; then
  state_dir="${install_dir%/*}/antigravity-agent-progress-install"
  if [[ $explicit_scope == 0 && -f "$state_dir/install.json" ]]; then
    options="$(/usr/bin/python3 -c '
import json, sys
with open(sys.argv[1]) as file:
    state = json.load(file)
print(int("antigravity-zh-prompt" in state["plugins"]), int(bool(state["launcher"])))
' "$state_dir/install.json")"
    read -r include_chinese include_launcher <<< "$options"
    if [[ -f "$state_dir/upgrade.sh" ]]; then
      upgrade=1
    fi
  fi

  if ! release="$(
    /usr/bin/curl -4 -fsSL --connect-timeout 5 --max-time 15 "https://api.github.com/repos/$repository/releases/latest" |
    /usr/bin/python3 -c '
import json, re, sys
try:
    release = json.load(sys.stdin)
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not re.fullmatch(r"v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", tag):
        raise ValueError
except (KeyError, TypeError, ValueError):
    sys.exit(1)
print(tag)
'
  )"; then
    printf '%s\n' '未能获取最新正式版，请稍后重试。' >&2
    exit 1
  fi

  installed_manifest="$install_dir/agent-progress/plugin.json"
  if [[ -f $installed_manifest ]]; then
    comparison="$(/usr/bin/python3 -c '
import json, re, sys
with open(sys.argv[1]) as file:
    installed = json.load(file).get("version", "")
if isinstance(installed, str) and re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", installed):
    current = tuple(map(int, installed.split(".")))
    latest = tuple(map(int, sys.argv[2][1:].split(".")))
    print("newer" if current > latest else "same" if current == latest else "older")
' "$installed_manifest" "$release")"
    if [[ $comparison == newer ]]; then
      printf '已安装的版本高于 GitHub 当前正式版（%s）。\n' "$release"
      exit 0
    fi
    if [[ $comparison == same && $upgrade == 1 ]]; then
      printf '已安装最新正式版（%s）。\n' "$release"
      exit 0
    fi
  fi

  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/antigravity-progress.XXXXXX")"
  trap 'rm -r -- "$temp_dir"' EXIT
  bundle_root="$temp_dir/bundle"
  mkdir "$bundle_root"
  /usr/bin/curl -4 -fsSL --connect-timeout 5 --max-time 60 "https://codeload.github.com/$repository/tar.gz/refs/tags/$release" -o "$temp_dir/bundle.tar.gz"
  /usr/bin/tar -xzf "$temp_dir/bundle.tar.gz" -C "$bundle_root" --strip-components 1
  /usr/bin/python3 -c '
import json, sys
with open(sys.argv[1]) as file:
    version = json.load(file).get("version")
if version != sys.argv[2][1:]:
    sys.exit("安装包版本与 GitHub Release 标签不一致。")
' "$bundle_root/agent-progress/plugin.json" "$release"
fi

/usr/bin/python3 "$bundle_root/scripts/package.py" install "$install_dir" "$bundle_root" "$include_chinese" "$include_launcher"
if [[ -f "${install_dir%/*}/antigravity-agent-progress-install/upgrade.sh" ]]; then
  printf '升级入口 bash %q\n' "${install_dir%/*}/antigravity-agent-progress-install/upgrade.sh"
fi
printf '卸载入口 %s\n' "${install_dir%/*}/antigravity-agent-progress-install/uninstall.sh"
