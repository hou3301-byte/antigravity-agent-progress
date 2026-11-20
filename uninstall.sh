#!/usr/bin/env bash
set -euo pipefail

install_dir="$HOME/.gemini/config/plugins"
dry_run=0

while (( $# )); do
  case "$1" in
    --workspace)
      if [[ $# -lt 2 || ! -d "$2" ]]; then
        printf '%s\n' '请给 --workspace 指定一个已存在的项目目录。' >&2
        exit 2
      fi
      install_dir="$(cd -- "$2" && pwd)/.agents/plugins"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      printf '%s\n' '用法：bash uninstall.sh [--workspace 项目目录] [--dry-run]'
      exit 0
      ;;
    *)
      printf '未知参数 %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

package="${install_dir%/*}/antigravity-agent-progress-install/package.py"
if [[ ! -f $package ]]; then
  printf '%s\n' '这个安装位置没有组合包的卸载记录。'
  exit 0
fi

if (( dry_run )); then
  exec /usr/bin/python3 "$package" uninstall "$install_dir" --dry-run
fi
exec /usr/bin/python3 "$package" uninstall "$install_dir"
