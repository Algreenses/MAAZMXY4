"""Build the locked Windows x64 client and standalone Python runtime.

Run: py -3.12 tools/setup_runtime.py
Only the standard library is needed to bootstrap. The system Python is not
modified. Downloads are verified and the replacement is checked before deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".runtime-cache"
STATE = ROOT / ".runtime-state.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_lock() -> dict:
    return json.loads((ROOT / "tools/runtime.lock.json").read_text(encoding="utf-8"))


def lock_fingerprint(lock: dict) -> str:
    data = (ROOT / "tools/runtime.lock.json").read_bytes()
    data += (ROOT / "tools" / lock["requirements"]).read_bytes()
    return hashlib.sha256(data).hexdigest()


def cached_download(artifact: dict, cache: Path, offline: bool = False) -> Path:
    """Only return an artifact whose bytes match the checked-in digest."""
    target = cache / artifact["filename"]
    if target.exists():
        if sha256(target) != artifact["sha256"]:
            raise ValueError(f"SHA256 mismatch: {target}. Remove this cached file and retry.")
        print(f"Cached: {target.name}", flush=True)
        return target
    if offline:
        raise FileNotFoundError(f"Offline cache is missing {target}")
    cache.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(artifact["url"], headers={"User-Agent": "MAAZMXY4-runtime-setup/1"})
    print(f"Downloading: {artifact['url']}", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        if sha256(partial) != artifact["sha256"]:
            raise ValueError(f"Downloaded SHA256 mismatch: {target.name}")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def validate_member(name: str, destination: Path) -> None:
    normalized = name.replace("\\", "/")
    if ":" in normalized or not (destination / normalized).resolve().is_relative_to(destination.resolve()):
        raise ValueError(f"Unsafe archive member: {name}")


def extract_archive(archive: Path, destination: Path) -> None:
    """Reject traversal and links before extracting either locked archive."""
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                validate_member(member.filename, destination)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(f"Archive links are unsupported: {member.filename}")
            bundle.extractall(destination)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                validate_member(member.name, destination)
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"Archive links/special files are unsupported: {member.name}")
            bundle.extractall(destination, filter="data")


def run(command: list[str], *, cwd: Path = ROOT, env: dict | None = None) -> None:
    print("Running: " + subprocess.list2cmdline([str(part) for part in command]), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def pip(python: Path, arguments: list[str]) -> None:
    run([str(python), "-E", "-s", "-X", "utf8", "-m", "pip", "--isolated",
         "--disable-pip-version-check", *arguments])


def install_packages(python: Path, lock: dict, offline: bool) -> None:
    requirements = ROOT / "tools" / lock["requirements"]
    wheels = CACHE / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    common = ["--require-hashes", "--only-binary=:all:", "--no-deps", "-r", str(requirements)]
    if not offline:
        pip(python, ["download", "--dest", str(wheels), "--timeout", "60", "--retries", "3", *common])
    pip(python, ["install", "--no-index", "--find-links", str(wheels), *common])
    pip(python, ["check"])


def sync_resources(destination: Path, *, overwrite: bool = False, backup: Path | None = None) -> None:
    """Copy canonical assets. Existing recording dropdowns survive normal reruns."""
    pairs = [(ROOT / "assets/interface.json", destination / "interface.json")]
    pairs += [(source, destination / "resource" / source.relative_to(ROOT / "assets/resource"))
              for source in sorted((ROOT / "assets/resource").rglob("*")) if source.is_file()]
    for source, target in pairs:
        if target.exists() and (not overwrite or source.read_bytes() == target.read_bytes()):
            continue
        if target.exists() and backup is not None:
            saved = backup / target.relative_to(destination)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def runtime_env(root: Path) -> dict:
    env = dict(os.environ)
    env["MAAFW_BINARY_PATH"] = str(root / "runtimes/win-x64/native")
    return env


def check(root: Path = ROOT) -> None:
    run([sys.executable, "-B", "-X", "utf8", str(ROOT / "tools/runtime_check.py"),
         "--root", str(root), "--smoke-agent"], cwd=root, env=runtime_env(root))


def _deployment_path(path: Path) -> Path:
    resolved = path.resolve()
    root = ROOT.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"Runtime deployment path is outside the project: {path}")
    return resolved


def _rollback_deployment(journal: list[dict], backup: Path) -> None:
    """Restore in reverse order, retaining replaced components for diagnosis."""
    failed = _deployment_path(backup / "_failed-new")
    problems = []
    while journal:
        entry = journal.pop()
        if not entry["changed"]:
            continue
        try:
            target = _deployment_path(entry["target"])
            if target.exists():
                retained = _deployment_path(failed / target.relative_to(ROOT.resolve()))
                if retained.exists():
                    raise FileExistsError(f"Rollback destination already exists: {retained}")
                retained.parent.mkdir(parents=True, exist_ok=True)
                target.rename(retained)
            if entry["saved"] is not None:
                saved = _deployment_path(entry["saved"])
                target.parent.mkdir(parents=True, exist_ok=True)
                saved.rename(target)
        except (OSError, ValueError) as exc:
            problems.append(f"{entry['target']}: {exc}")
    if problems:
        raise OSError("Runtime rollback needs manual recovery from " + str(backup) + ": " + "; ".join(problems))
    print(f"Previous runtime restored. Failed replacement files retained under: {failed}", file=sys.stderr)


def _snapshot_deployment_file(target: Path, backup: Path, journal: list[dict]) -> None:
    """Protect files that the resource synchronizer or state writer may modify."""
    target = _deployment_path(target)
    saved = None
    if target.exists():
        saved = _deployment_path(backup / target.relative_to(ROOT.resolve()))
        if saved.exists():
            raise FileExistsError(f"Runtime backup already exists: {saved}")
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
    journal.append({"target": target, "saved": saved, "changed": True})


def replace_directory(source: Path, target: Path, backup: Path, journal: list[dict] | None = None) -> None:
    """Replace one component; restore the old component if its move fails."""
    own_journal = journal is None
    journal = [] if own_journal else journal
    source, target, backup = (_deployment_path(path) for path in (source, target, backup))
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {"target": target, "saved": None, "changed": False}
    journal.append(entry)
    try:
        if target.exists():
            saved = _deployment_path(backup / target.relative_to(ROOT.resolve()))
            if saved.exists():
                raise FileExistsError(f"Runtime backup already exists: {saved}")
            saved.parent.mkdir(parents=True, exist_ok=True)
            target.rename(saved)
            entry.update(saved=saved, changed=True)
        source.rename(target)
        entry["changed"] = True
    except BaseException:
        if own_journal:
            _rollback_deployment(journal, backup)
        raise


def deploy(stage: Path, backup: Path) -> list[dict]:
    stage, backup = (_deployment_path(path) for path in (stage, backup))
    journal: list[dict] = []
    try:
        for name in ("MaaAgentBinary", "libs", "plugins"):
            if (stage / name).exists():
                replace_directory(stage / name, ROOT / name, backup, journal)
        for source in (stage / "runtimes").iterdir():
            replace_directory(source, ROOT / "runtimes" / source.name, backup, journal)
        for source in stage.iterdir():
            if not source.is_file() or source.name in {"interface.json", "DependencySetup_依赖库安装_win.bat"}:
                continue
            replace_directory(source, ROOT / source.name, backup, journal)
    except BaseException:
        _rollback_deployment(journal, backup)
        raise
    return journal


def build(lock: dict, offline: bool, sync: bool) -> None:
    if Path(sys.executable).resolve().is_relative_to((ROOT / "runtimes/python").resolve()):
        raise ValueError("Rebuild with the external bootstrap Python: py -3.12 tools/setup_runtime.py --rebuild")
    python_archive = cached_download(lock["python"], CACHE, offline)
    client_archive = cached_download(lock["client"], CACHE, offline)
    stage = Path(tempfile.mkdtemp(prefix="build-", dir=CACHE))
    try:
        if not stage.resolve().is_relative_to(CACHE.resolve()):
            raise ValueError(f"Staging directory is outside the runtime cache: {stage}")
        project = stage / "project"
        print("Extracting the verified client and standalone Python...", flush=True)
        extract_archive(client_archive, project)
        extract_archive(python_archive, stage / "interpreter")
        (project / "runtimes").mkdir(exist_ok=True)
        shutil.move(str(stage / "interpreter/python"), str(project / "runtimes/python"))
        install_packages(project / "runtimes/python/python.exe", lock, offline)
        for directory in ("agent", "assets", "tools"):
            shutil.copytree(ROOT / directory, project / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        sync_resources(project, overwrite=True)
        print("Checking the staged client, Python, resources and Agent IPC...", flush=True)
        check(project)
        backup = CACHE / "backups" / time.strftime("%Y%m%d-%H%M%S")
        journal = deploy(project, backup)
        try:
            resources = [(ROOT / "assets/interface.json", ROOT / "interface.json")]
            resources += [(source, ROOT / "resource" / source.relative_to(ROOT / "assets/resource"))
                          for source in (ROOT / "assets/resource").rglob("*") if source.is_file()]
            for source, target in resources:
                if not target.exists() or (sync and source.read_bytes() != target.read_bytes()):
                    _snapshot_deployment_file(target, backup, journal)
            sync_resources(ROOT, overwrite=sync, backup=backup)
            check()
            _snapshot_deployment_file(STATE, backup, journal)
            STATE.write_text(json.dumps({"lock_sha256": lock_fingerprint(lock),
                                         "python": lock["python"]["version"],
                                         "client": lock["client"]["version"]}, indent=2) + "\n", encoding="utf-8")
        except BaseException:
            _rollback_deployment(journal, backup)
            raise
        print(f"Runtime ready. Previous runtime files, if any: {backup}", flush=True)
    except Exception:
        print(f"Staging files and diagnostics retained: {stage}", file=sys.stderr)
        raise
    else:
        # stage was resolved and checked against CACHE above before cleanup.
        shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check only; do not download or rebuild")
    parser.add_argument("--offline", action="store_true", help="Use only cached archives and wheels")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild even when the installed lock matches")
    parser.add_argument("--sync-resources", action="store_true",
                        help="Refresh runtime interface/resources from assets, backing up changed files")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Bootstrap requires Python 3.12 or newer (py -3.12).")
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"} or struct.calcsize("P") != 8:
        parser.error("Only Windows x64 is supported by this runtime lock.")
    try:
        if args.check:
            check()
            return 0
        lock = load_lock()
        CACHE.mkdir(parents=True, exist_ok=True)
        installed = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        if not args.rebuild and installed.get("lock_sha256") == lock_fingerprint(lock):
            backup = CACHE / "backups" / time.strftime("%Y%m%d-%H%M%S")
            sync_resources(ROOT, overwrite=args.sync_resources, backup=backup)
            check()
            print("Installed runtime matches the lock; no rebuild was needed.")
        else:
            build(lock, args.offline, args.sync_resources)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"Runtime setup failed: {exc}", file=sys.stderr)
        print("Cached downloads are retained. Close the client before rebuilding; retry with --rebuild.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
