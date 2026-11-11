"""同一个用户只能有一个助手：用命名互斥量判断，第二个进程拿不到。"""

import subprocess
import sys
import uuid

import pytest

from lolhex.ui import single_instance

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="命名互斥量只在 Windows 上")

PROBE = "import sys; from lolhex.ui import single_instance as s; print(s.acquire(sys.argv[1], int(sys.argv[2])))"


def _other_process(name, wait_ms=0):
    out = subprocess.run([sys.executable, "-c", PROBE, name, str(wait_ms)],
                         capture_output=True, text=True, timeout=30, check=True)
    return out.stdout.strip()


def test_second_process_cannot_acquire(monkeypatch):
    monkeypatch.setattr(single_instance, "_held", [])
    name = f"lolhex-test-{uuid.uuid4().hex}"
    assert _other_process(name) == "True"  # 没人占着：拿得到，进程退出后自动释放
    assert single_instance.acquire(name)
    assert _other_process(name) == "False"
    assert _other_process(name, 300) == "False"  # 等一会儿也拿不到
