"""Validate the Windows runtime without operating the game or input devices.

    python tools/runtime_check.py --root . --smoke-agent
    python tools/runtime_check.py --root . --smoke-agent --json

Imports, resource loading and the optional IPC smoke test run in separate runtime
processes. The checks never create a controller, post a task or start input hooks.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = str(Path(__file__).resolve().parent.parent)
PY_DIR = os.path.join(ROOT, "runtimes", "python")
PY_EXE = os.path.join(PY_DIR, "python.exe")
SP = os.path.join(PY_DIR, "Lib", "site-packages")
_RESULT_MARKER = "MAAZMXY4_CHECK_JSON="
EXPECTED_ACTIONS = {
    "adventure_init", "battle_play", "loop_count", "battle_record", "open_recordings",
}
EXPECTED_RECOGNITIONS = {"adventure_result", "adventure_read"}
REQUIRED = [
    "python.exe", "python312.dll", "vcruntime140.dll", "DLLs",
    "Lib/os.py", "Lib/tkinter/__init__.py", "DLLs/_tkinter.pyd",
    "tcl/tcl8.6/init.tcl", "tcl/tk8.6/tk.tcl",
    "Lib/site-packages/maa/__init__.py", "Lib/site-packages/numpy/__init__.py",
    "Lib/site-packages/strenum/__init__.py", "Lib/site-packages/MaaAgentBinary",
    "Lib/site-packages/pynput/__init__.py",
]


def _set_root(path: str) -> None:
    global ROOT, PY_DIR, PY_EXE, SP
    ROOT = str(Path(path).expanduser().resolve())
    PY_DIR = os.path.join(ROOT, "runtimes", "python")
    PY_EXE = os.path.join(PY_DIR, "python.exe")
    SP = os.path.join(PY_DIR, "Lib", "site-packages")


def _result(name: str, ok: bool, message: str, **details) -> dict:
    return {"name": name, "ok": bool(ok), "message": message, **details}


def _manifest() -> dict:
    path = Path(ROOT) / "tools/runtime.lock.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_files() -> list[str]:
    """Return missing runtime-relative paths (kept for existing callers)."""
    return [rel for rel in REQUIRED if not (Path(PY_DIR) / rel).exists()]


def check_interface() -> list[tuple[str, str]]:
    """Check source and runnable interfaces, including real Agent targets."""
    problems = []
    for path in (Path(ROOT) / "assets/interface.json", Path(ROOT) / "interface.json"):
        if not path.is_file():
            problems.append((str(path), "文件不存在"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            agent = data.get("agent") or {}
            executable = str(agent.get("child_exec", "")).replace("\\", "/")
            if executable.removeprefix("./") != "runtimes/python/python.exe":
                problems.append((str(path), "child_exec 应指向 ./runtimes/python/python.exe"))
            args = agent.get("child_args") or []
            if "-E" not in args or "-s" not in args:
                problems.append((str(path), "child_args 缺少 -E -s"))
            if not any(str(a).replace("\\", "/").removeprefix("./") == "agent/main.py" for a in args):
                problems.append((str(path), "child_args 缺少 ./agent/main.py"))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            problems.append((str(path), f"无法解析：{exc}"))
    return problems


def _hide_console() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _stop_process(process: subprocess.Popen, tree: bool = False) -> None:
    """Stop only our own check process (and its own IPC child on timeout)."""
    if process.poll() is not None:
        return
    if tree and os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False, **_hide_console(),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_probe(kind: str, timeout: int = 60) -> dict:
    if not Path(PY_EXE).is_file():
        return _result(kind, False, f"解释器不存在：{PY_EXE}")
    command = [
        PY_EXE, "-E", "-s", "-X", "utf8", str(Path(__file__).resolve()),
        "--root", ROOT, "--_probe", kind,
    ]
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", **_hide_console(),
        )
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_process(process, tree=kind == "ipc")
            output, _ = process.communicate(timeout=5)
            return _result(kind, False, f"检查超时（{timeout}s）", output=output[-4000:])
    except (OSError, subprocess.SubprocessError) as exc:
        return _result(kind, False, f"无法启动检查：{exc}")
    for line in reversed(output.splitlines()):
        if line.startswith(_RESULT_MARKER):
            try:
                result = json.loads(line[len(_RESULT_MARKER):])
                if process.returncode and result.get("ok"):
                    result["ok"] = False
                    result["message"] = f"子进程异常退出：{process.returncode}"
                return result
            except ValueError:
                break
    return _result(kind, False, f"检查进程退出码 {process.returncode}，没有返回结果", output=output[-4000:])


def check_probe() -> tuple[bool, str]:
    """Run actual imports in the isolated runtime (legacy public interface)."""
    result = _run_probe("dependencies")
    return result["ok"], result["message"]


def _normal_version(version: str) -> str:
    return str(version).strip().lower().removeprefix("v")


def _prepare_native() -> Path:
    native = Path(ROOT) / "runtimes/win-x64/native"
    if not native.is_dir():
        raise FileNotFoundError(f"原生库目录不存在：{native}")
    # Avoid inheriting another project's MAAFW_BINARY_PATH in a shared shell.
    os.environ["MAAFW_BINARY_PATH"] = str(native)
    return native


def _dependency_probe() -> dict:
    if os.name != "nt":
        raise RuntimeError("完整运行时仅支持 Windows x64")
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeError(f"需要 Python 3.12 x64，实际为 {sys.version.split()[0]} / {struct.calcsize('P') * 8} 位")
    manifest = _manifest()
    expected_python = manifest.get("python", {}).get("version")
    if expected_python and platform.python_version() != expected_python:
        raise RuntimeError(f"Python={platform.python_version()}，锁文件要求 {expected_python}")
    _prepare_native()
    # ctypes is imported before third-party native extensions by this module.
    import numpy
    import strenum  # noqa: F401
    import pynput  # noqa: F401 - no Listener objects are constructed
    import tkinter
    from maa.library import Library
    from maa.resource import Resource
    from maa.toolkit import Toolkit

    for library in (Library.framework, Library.toolkit, Library.agent_client):
        library()  # Actually load native DLLs and their VC runtime dependencies.
    package_version = importlib.metadata.version("MaaFw")
    native_version = Library.version()
    if _normal_version(package_version) != _normal_version(native_version):
        raise RuntimeError(f"MaaFw Python={package_version} 与实际原生库={native_version} 不匹配")
    expected_framework = manifest.get("client", {}).get("framework_version")
    if expected_framework and _normal_version(native_version) != _normal_version(expected_framework):
        raise RuntimeError(f"原生库={native_version}，锁文件要求 {expected_framework}")
    names = manifest.get("packages") or {
        "MaaFw": None, "MaaAgentBinary": None, "numpy": None,
        "StrEnum": None, "pynput": None, "six": None,
    }
    versions = {}
    for name, expected in names.items():
        actual = importlib.metadata.version(name)
        versions[name] = actual
        if expected and _normal_version(actual) != _normal_version(expected):
            raise RuntimeError(f"{name}={actual}，锁文件要求 {expected}")
    for key, folder in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        os.environ[key] = str(Path(PY_DIR) / "tcl" / folder)
    tcl_version = tkinter.Tcl().eval("info patchlevel")  # No Tk()/GUI window.
    if not hasattr(Resource, "post_bundle") or not hasattr(Toolkit, "init_option"):
        raise RuntimeError("MaaFw 缺少项目所需 API")
    return _result(
        "dependencies", True,
        f"Python {platform.python_version()} x64；MaaFw/native {package_version}；Tk/Tcl {tcl_version}；pynput 与原生 DLL 加载通过",
        versions=versions, native_version=native_version, tcl_version=tcl_version,
        executable=sys.executable, numpy_version=numpy.__version__,
    )


def _agent_import_probe() -> dict:
    _prepare_native()
    sys.path.insert(0, str(Path(ROOT) / "agent"))
    import main  # noqa: F401 - import only; do not call main.main()
    from maa.agent.agent_server import AgentServer

    actions = set(AgentServer._custom_action_holder)
    recognitions = set(AgentServer._custom_recognition_holder)
    if not EXPECTED_ACTIONS.issubset(actions) or not EXPECTED_RECOGNITIONS.issubset(recognitions):
        raise RuntimeError(f"Agent 注册缺失：actions={sorted(actions)} recognitions={sorted(recognitions)}")
    return _result(
        "agent", True, "完整 Agent 导入与 5 个动作、2 个识别器注册通过",
        actions=sorted(actions), recognitions=sorted(recognitions),
    )


def _resource_probe() -> dict:
    _prepare_native()
    from maa.resource import Resource

    resource = Resource()
    resource.use_cpu()
    bundle = Path(ROOT) / "resource/base"
    if not resource.post_bundle(str(bundle)).wait().succeeded:
        raise RuntimeError(f"MaaFramework 资源加载失败：{bundle}")
    interface = json.loads((Path(ROOT) / "interface.json").read_text(encoding="utf-8-sig"))
    node_names = set(resource.node_list)
    missing = [t.get("entry") for t in interface.get("task", []) if t.get("entry") not in node_names]
    if missing:
        raise RuntimeError(f"任务入口缺失：{missing}")
    return _result("resource", True, f"实际加载 OCR/模板/Pipeline 通过：{len(node_names)} 个节点、{len(interface.get('task', []))} 个入口", nodes=len(node_names))


def _ipc_probe() -> dict:
    _prepare_native()
    # This process MUST NOT import maa.agent / agent/main: that switches the
    # binding's global Library context from AgentClient to AgentServer.
    from maa.agent_client import AgentClient
    from maa.resource import Resource
    from maa.tasker import Tasker

    log_dir = Path(ROOT) / "debug/runtime_check/ipc"
    log_dir.mkdir(parents=True, exist_ok=True)
    Tasker.set_log_dir(log_dir)
    started = time.monotonic()
    diagnostics = []

    def mark(phase: str, **state) -> None:
        diagnostics.append({"phase": phase, "elapsed": round(time.monotonic() - started, 3), **state})

    # MaaAgentClientConnect requires a bound Resource even for the startup
    # handshake (v5.12.3 AgentClient.cpp:162). No controller or task is needed.
    resource = Resource()
    client = AgentClient()
    if not client.bind(resource):
        raise RuntimeError("AgentClient 无法绑定烟测资源")
    if not client.set_timeout(10_000):
        raise RuntimeError("AgentClient 无法设置 IPC 超时")
    identifier = client.identifier
    if not identifier:
        raise RuntimeError("AgentClient 未生成连接标识")
    process = None
    with tempfile.TemporaryFile() as output:
        try:
            mark("client_ready", identifier=identifier)
            process = subprocess.Popen(
                [PY_EXE, "-E", "-s", "-X", "utf8", str(Path(ROOT) / "agent/main.py"), identifier],
                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, **_hide_console(),
            )
            mark("child_started", pid=process.pid)
            connected = client.connect()
            mark("connect_returned", success=connected, child_exit=process.poll())
            if not connected:
                raise RuntimeError("Agent IPC 握手失败")
            if not client.connected:
                raise RuntimeError("Agent IPC 已连接状态检查失败")
            if not client.alive:
                raise RuntimeError("Agent IPC 存活检查失败")
            actions = set(client.custom_action_list)
            recognitions = set(client.custom_recognition_list)
            mark("registrations_received", actions=len(actions), recognitions=len(recognitions))
            if not EXPECTED_ACTIONS.issubset(actions) or not EXPECTED_RECOGNITIONS.issubset(recognitions):
                raise RuntimeError(f"IPC 注册表不完整：actions={sorted(actions)} recognitions={sorted(recognitions)}")
            if not client.disconnect():
                raise RuntimeError("Agent IPC 断开失败")
            mark("disconnected")
            process.wait(timeout=15)
            if process.returncode != 0:
                raise RuntimeError(f"Agent 退出码为 {process.returncode}")
            mark("child_exited", returncode=process.returncode)
            return _result("ipc", True, "Agent 真实 IPC 连接、注册查询、断开及正常退出通过（未执行任务）", actions=sorted(actions), recognitions=sorted(recognitions), diagnostics=diagnostics)
        except Exception as exc:
            try:
                client.disconnect()
            except Exception:
                pass
            if process is not None:
                _stop_process(process)
            output.seek(0)
            native_log = log_dir / "maafw.log"
            native_tail = native_log.read_text(encoding="utf-8", errors="replace")[-4000:] if native_log.is_file() else "（未生成）"
            raise RuntimeError(
                f"{exc}\nIPC 阶段：{json.dumps(diagnostics, ensure_ascii=False)}"
                f"\nAgent 输出：{output.read().decode('utf-8', errors='replace')[-4000:]}"
                f"\nNative 日志（{native_log}）：{native_tail}"
            ) from exc
        finally:
            if process is not None:
                _stop_process(process)


def _check_layout() -> dict:
    root = Path(ROOT)
    expected = [
        "interface.json", "agent/main.py", "agent/adventure.py", "agent/battle_player.py",
        "agent/battle_record.py", "assets/data/adventure.json", "assets/data/adventure_ui.json",
        "resource/base/pipeline", "resource/base/image", "resource/base/model/ocr/det.onnx",
        "resource/base/model/ocr/rec.onnx", "resource/base/model/ocr/keys.txt",
        "runtimes/win-x64/native/MaaFramework.dll", "runtimes/win-x64/native/MaaToolkit.dll",
        "runtimes/win-x64/native/MaaAgentClient.dll", "runtimes/win-x64/native/MaaAgentServer.dll",
    ]
    missing = [name for name in expected if not (root / name).exists()]
    executable = _manifest().get("client", {}).get("executable")
    clients = [executable] if executable else ["MFAAvalonia.exe", "MAA造梦西游4.exe"]
    if not any((root / name).is_file() for name in clients):
        missing.append(" / ".join(clients))
    return _result("layout", not missing, "客户端、Agent、题库、资源与原生库文件齐全" if not missing else "缺少文件：" + "、".join(missing), missing=missing)


def _check_dotnet() -> dict:
    major = int(_manifest().get("system", {}).get("dotnet_desktop_major", 10))
    candidates = [shutil.which("dotnet")]
    if os.environ.get("ProgramFiles"):
        candidates.append(str(Path(os.environ["ProgramFiles"]) / "dotnet/dotnet.exe"))
    outputs = []
    for candidate in dict.fromkeys(c for c in candidates if c):
        if not Path(candidate).is_file():
            continue
        try:
            run = subprocess.run([candidate, "--list-runtimes"], capture_output=True, text=True, errors="replace", timeout=15, **_hide_console())
            outputs.extend(run.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            continue
    versions = sorted(set(re.findall(rf"Microsoft\.WindowsDesktop\.App\s+({major}\.\S+)", "\n".join(outputs))))
    return _result("dotnet", bool(versions), f".NET {major} Desktop Runtime：" + "、".join(versions) if versions else f"未检测到 .NET {major} Desktop Runtime（客户端需要）", versions=versions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=ROOT, help="已组装项目根目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--smoke-agent", action="store_true", help="执行真实 IPC 连接/退出烟测，不操作游戏")
    parser.add_argument("--_probe", choices=("dependencies", "agent", "resource", "ipc"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    _set_root(args.root)
    if args._probe:
        functions = {"dependencies": _dependency_probe, "agent": _agent_import_probe, "resource": _resource_probe, "ipc": _ipc_probe}
        try:
            result = functions[args._probe]()
        except Exception as exc:
            result = _result(args._probe, False, f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        print(_RESULT_MARKER + json.dumps(result, ensure_ascii=True))
        return 0 if result["ok"] else 1

    try:
        _manifest()
        missing = check_files()
        results = [_result("files", not missing, "Python/Tk/第三方包文件齐全" if not missing else "运行时缺少：" + "、".join(missing), missing=missing)]
        results.append(_check_layout())
        problems = check_interface()
        results.append(_result("interface", not problems, "源码与运行版 interface 配置正确" if not problems else "；".join(f"{p}: {why}" for p, why in problems)))
        results.append(_check_dotnet())
        if Path(PY_EXE).is_file():
            results.extend((_run_probe("dependencies"), _run_probe("agent"), _run_probe("resource", timeout=90)))
            if args.smoke_agent:
                results.append(_run_probe("ipc", timeout=60))
        else:
            results.append(_result("dependencies", False, f"解释器不存在，无法执行实际导入/加载：{PY_EXE}"))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        results = [_result("configuration", False, f"检查配置无效：{exc}")]
    report = {"root": ROOT, "runtime": PY_DIR, "ok": all(r["ok"] for r in results), "checks": results}
    if args.json:
        # Keep machine output independent of the host's legacy Windows codepage.
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f"项目根：{ROOT}\n运行时：{PY_DIR}")
        for result in results:
            print(f"[{'通过' if result['ok'] else '失败'}] {result['name']}：{result['message']}")
        print("结论：" + ("全部通过" if report["ok"] else "有问题，见上面"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
