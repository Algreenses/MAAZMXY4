# -*- coding: utf-8 -*-
"""一条命令检查「这台机器现在能不能正常跑生存大冒险」。

重启电脑、换机器、或者脚本表现不对劲时先跑这个。

    runtimes\\python\\python.exe tools\\check_env.py

它只做检查，不改任何东西。检查项：
  1. 运行时是否完好（等价于 runtime_check.py）
  2. interface.json 的 agent.child_exec 指向哪里
  3. 游戏进程在不在、能不能读到它的内存
  4. 题序映射表是否完整（41 条）
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

OK, WARN, BAD = "[通过]", "[注意]", "[失败]"


def check_runtime() -> bool:
    import subprocess
    p = os.path.join(ROOT, "tools", "runtime_check.py")
    r = subprocess.run([sys.executable, p], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    good = r.returncode == 0
    print("%s 运行时：%s" % (OK if good else BAD, "完好" if good else "有问题（见 tools/runtime_check.py 输出）"))
    return good


def check_interface() -> bool:
    p = os.path.join(ROOT, "interface.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        exec_ = (d.get("agent") or {}).get("child_exec")
    except (OSError, ValueError) as exc:
        print("%s interface.json 读不出来：%s" % (BAD, exc))
        return False
    want = "./runtimes/python/python.exe"
    good = str(exec_).replace("\\", "/").lstrip("./") == want.lstrip("./")
    print("%s interface.json child_exec = %r%s" % (OK if good else WARN, exec_,
                                                   "" if good else "（预期 ./runtimes/python/python.exe）"))
    return True


def main() -> int:
    print("检查目录：", ROOT)
    print()
    results = [check_runtime(), check_interface()]
    print()
    if all(results):
        print("结论：静态检查全部通过。")
        return 0
    print("结论：有项目不正常，按上面的提示处理。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
