# -*- coding: utf-8 -*-
"""
生成 MAA造梦西游4 的增量更新包 + version.json。

用法（在包根目录，也就是 MAA造梦西游4.exe 所在目录）：

    python tools/build_update.py --version 2.2 --note "修了刷副本中断"

产物（默认输出到 dist/）：

    dist/update-v2.2.zip   只含 resource/、agent/、interface.json
    dist/version.json      客户端 updater.py 读取的清单

发布时把 update-vX.zip 传到对应的 GitHub Release（再传一份到 R2 镜像），
把 version.json 传到仓库根和 R2（R2 上传后把前缀填进 updater.py 的 R2_BASE）。

    python tools/build_update.py --version 2.2 --note "修了刷副本中断" \
        --base-url "https://maa.example.com/maa"
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile

ITEMS = ("resource", "agent")
REPO = "Inkyu-Zero/MAAZMXY4"

# 发布版 interface.json 优先取 assets/ 下那份（干净的对外配置），
# 根目录那份是自用副本，可能多出私有任务（比如「掉落预判」）。
INTERFACE_CANDIDATES = ("assets/interface.json", "interface.json")

# 绝不进更新包的文件：私有插件、备份、字节码、私有 pipeline。
EXCLUDE_NAMES = {
    "adventure_order.py",        # 题序规则（自用）
    "adventure_local_hook.py",   # 本地钩子（自用）
    "adventure_drops.py",        # 掉落预判（自用）
    "adventure_drops_action.py", # 掉落预判的 CustomRecognition 注册（自用）
    "掉落预判.json",              # 上面那个插件的 pipeline
    "jzld_action.py",             # 九转炼丹（自用）
    "九转炼丹.json",               # 上面那个插件的 pipeline
}
EXCLUDE_DIRS = {"__pycache__", ".git", "jzld", "recordings"}


def is_local_instructions(name):
    """Local agent instructions are never distributed as client data."""
    return name.casefold() == "agents.md"


def _skip(name):
    if name in EXCLUDE_NAMES or is_local_instructions(name) or name in ('.gitignore', '.gitattributes', '.gitkeep'):
        return True
    if ".bak" in name or name.endswith(".pyc") or name.endswith(".pyo"):
        return True
    return False


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pick_dir(root, item):
    """优先用 assets/ 下的规范源，没有再退回根目录的运行副本。"""
    for cand in (os.path.join("assets", item), item):
        p = os.path.join(root, cand)
        if os.path.exists(p):
            return p
    return None


def collect(root):
    """返回 [(绝对路径, zip 内相对路径)]。"""
    files = []
    for item in ITEMS:
        p = _pick_dir(root, item)
        if not p:
            raise SystemExit("缺少 %s，确认在包根目录运行" % item)
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for name in filenames:
                if _skip(name):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, p).replace("\\", "/")
                files.append((full, item + "/" + rel))

    # Root recordings are personal. agent/recordings contains only the reviewed
    # manifest and its declared public defaults; never sweep that folder whole.
    defaults = os.path.join(_pick_dir(root, 'agent'), 'recordings')
    manifest = os.path.join(defaults, 'manifest.json')
    if os.path.isfile(manifest):
        with open(manifest, encoding='utf8') as stream:
            bundle = json.load(stream)
        if bundle.get('version') != 1 or not isinstance(bundle.get('items'), list):
            raise ValueError('公共录制清单格式无效。')
        files.append((manifest, 'agent/recordings/manifest.json'))
        seen = set()
        for item in bundle['items']:
            name = item.get('file', '')
            if (not name.endswith('.json') or name.startswith('.') or name == 'manifest.json'
                    or any(char in name for char in '/\\:') or name in seen):
                raise ValueError('公共录制清单路径无效。')
            seen.add(name)
            path = os.path.join(defaults, name)
            if not os.path.isfile(path) or sha256_of(path) != item.get('sha256'):
                raise ValueError('公共录制与清单哈希不一致：' + name)
            files.append((path, 'agent/recordings/' + name))

    for cand in INTERFACE_CANDIDATES:
        p = os.path.join(root, cand)
        if os.path.isfile(p):
            files.append((p, "interface.json"))
            break
    else:
        raise SystemExit("缺少 interface.json（assets/ 或根目录都没有）")
    return files


def build_resource(root, destination, *, min_client_version, max_client_version=None):
    """Protocol-2 resource package; metadata is read by new installers only.

    It still changes exactly resource/, agent/ and interface.json. No new feed
    is published implicitly, and legacy version.json is deliberately untouched.
    """
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from update_engine.packages import build_inventory, write_inventory_files, RESOURCE_RECORD
    files = collect(str(root))
    interface_path = next(source for source, name in files if name == 'interface.json')
    version = json.loads(Path(interface_path).read_text(encoding='utf8'))['version']
    inventory = build_inventory(files, kind='resource', resource_version=version,
                                min_client_version=min_client_version, max_client_version=max_client_version)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        write_inventory_files(archive, files, inventory)
        archive.writestr(RESOURCE_RECORD, json.dumps(inventory.to_dict(), ensure_ascii=False, indent=2))
    return Path(destination)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="版本号，例如 2.2")
    ap.add_argument("--legacy", action='store_true', help="仅供维护旧客户端兼容分支；输出旧 version.json")
    ap.add_argument("--min-client-version", default='2.3.9', help="新资源包最低兼容客户端版本")
    ap.add_argument("--max-client-version", help="可选的最高兼容客户端版本")
    ap.add_argument("--tag", default=None, help="Release tag，默认 v<version>")
    ap.add_argument("--note", default="", help="更新说明，显示在更新弹窗里")
    ap.add_argument("--root", default=None, help="包根目录，默认是本脚本上一级")
    ap.add_argument("--out", default="dist", help="输出目录")
    ap.add_argument("--base-url", default="",
                    help="镜像前缀（如 R2：https://maa.example.com/maa），会追加进 urls")
    args = ap.parse_args()

    root = os.path.abspath(args.root or os.path.join(os.path.dirname(__file__), ".."))
    version = str(args.version).lstrip("vV")
    tag = args.tag or ("v" + version)
    out_dir = os.path.join(root, args.out)
    os.makedirs(out_dir, exist_ok=True)

    files = collect(root)
    zip_name = "update-v%s.zip" % version
    zip_path = os.path.join(out_dir, zip_name)

    if not args.legacy:
        interface_path = next(source for source, name in files if name == 'interface.json')
        if json.loads(open(interface_path, encoding='utf8').read()).get('version') != version:
            ap.error('--version 必须与规范 interface.json 版本一致。')
        print(build_resource(root, zip_path, min_client_version=args.min_client_version,
                             max_client_version=args.max_client_version))
        print('未修改旧 version.json。使用 tools/build_update_manifest.py 从真实资产生成新清单。')
        return 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for full, rel in files:
            zf.write(full, rel)

    size = os.path.getsize(zip_path)
    digest = sha256_of(zip_path)

    github_url = "https://github.com/%s/releases/download/%s/%s" % (REPO, tag, zip_name)
    urls = [github_url]
    if args.base_url.strip():
        urls.append(args.base_url.strip().rstrip("/") + "/" + zip_name)

    manifest = {
        "version": version,
        "file": zip_name,
        "sha256": digest,
        "size": size,
        "urls": urls,       # 客户端依次尝试，第一个成功就用
        "url": urls[0],     # 兼容旧版客户端
        "note": args.note,
    }
    manifest_path = os.path.join(out_dir, "version.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("文件数: %d" % len(files))
    print("更新包: %s（%.2f MB）" % (zip_path, size / 1048576.0))
    print("sha256: %s" % digest)
    print("清单  : %s" % manifest_path)
    print("下载地址:")
    for u in urls:
        print("  - %s" % u)
    return 0


if __name__ == "__main__":
    sys.exit(main())
