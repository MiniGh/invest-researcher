"""让测试的 sys.path 与运行时一致。

`backend/server/app.py` 和 `server_utils.py` 用的是 `from utils import ...`
(顶层名,不是 `backend.utils`)—— 这是上游写法,从仓库根目录起服务时能解析,
但 pytest 的 import 模式下 `backend/` 不在 sys.path 上,于是
tests/test_logging.py、test_logs.py、test_security_fix.py 三个模块在收集阶段就
ImportError,test_logging_output.py、test_researcher_logging.py 两个在运行时
ModuleNotFoundError。

不去改上游那些 import(会牵动 server 的启动路径),只在测试里补齐同样的路径。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
