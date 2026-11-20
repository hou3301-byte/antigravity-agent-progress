#!/usr/bin/env bash
set -euo pipefail

repository=hou3301-byte/antigravity-agent-progress
release=v0.1.0
install_dir="$HOME/.gemini/config/plugins"
include_chinese=1

while (( $# )); do
  case "$1" in
    --progress-only)
      include_chinese=0
      shift
      ;;
    --workspace)
      if [[ $# -lt 2 || ! -d "$2" ]]; then
        printf '%s\n' '请给 --workspace 指定一个已存在的项目目录。' >&2
        exit 2
      fi
      install_dir="$(cd -- "$2" && pwd)/.agents/plugins"
      shift 2
      ;;
    -h|--help)
      cat <<'HELP'
在 macOS 安装 Antigravity 的「执行进度」和「全流程中文思考提示词」插件。

  bash install.sh                       安装到本机所有项目
  bash install.sh --workspace <目录>     只安装到指定项目
  bash install.sh --progress-only       只安装执行进度

现有中文提示词插件会保留当前版本和启用状态。
在 Antigravity 中新建对话，发送「演示执行进度卡」即可试用。
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

/usr/bin/python3 - "$bundle_root" "$install_dir" "$include_chinese" <<'PY'
import json
import shutil
import sys
import tempfile
from pathlib import Path


bundle, destination = map(Path, sys.argv[1:3])
progress = bundle / "agent-progress"
chinese = bundle / "vendor" / "antigravity-zh-prompt"
plugins = [progress, chinese] if sys.argv[3] == "1" else [progress]
to_install = []

for source in plugins:
    manifest = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    name = manifest["name"]
    if name != source.name:
        raise SystemExit(f"安装包中的插件名称不匹配 {source.name}")
    target = destination / name
    if not (target.exists() or target.is_symlink()):
        to_install.append(source)
        continue
    if source == progress:
        for packaged in source.rglob("*"):
            if packaged.is_file():
                installed = target / packaged.relative_to(source)
                if not installed.is_file() or installed.read_bytes() != packaged.read_bytes():
                    raise SystemExit(f"已存在不同版本的执行进度插件，请先备份并移走 {target}")
    print(f"已存在，保留当前版本和启用状态 {name}")

if to_install:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".progress-install-", dir=destination.parent) as temp:
        staging = Path(temp)
        for source in to_install:
            staged = staging / source.name
            shutil.copytree(source, staged)
            if source == chinese:
                manifest_path = staged / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.pop("disabled", None)
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        installed = []
        try:
            for source in to_install:
                target = destination / source.name
                if target.exists() or target.is_symlink():
                    raise FileExistsError(target)
                (staging / source.name).rename(target)
                installed.append(target)
        except OSError:
            for target in reversed(installed):
                target.rename(staging / target.name)
            raise
        for target in installed:
            print(f"已安装 {target}")

print("打开 Antigravity，新建对话并发送「演示执行进度卡」。")
PY
