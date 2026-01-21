import os
import re
import subprocess
import sys
from pathlib import Path


# 1) CLI 入口：如果你的项目不是安装成 expense-tracker，
#    改成：CLI = [sys.executable, "-m", "expense_tracker"]
CLI = ["expense-tracker"]

# 2) 可选：如果你的程序支持用环境变量指定数据文件/DB路径，
#    推荐在测试里强制指向临时目录，保证测试互不影响。
#
#    你需要把 ENV_DB_KEY 改成你项目真实使用的环境变量名；
#    如果你项目没有这个功能，也没关系——把 ENV_DB_KEY 设为 None 即可。
ENV_DB_KEY = "EXPENSE_TRACKER_DB"  # 或 None


def run_cmd(args, env=None):
    """Run CLI command and return CompletedProcess."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
    )


def extract_id(output: str) -> int:
    """
    从输出中提取新增成功的 ID，例如：
    "Expense added successfully (ID: 1)"
    """
    m = re.search(r"\(ID:\s*(\d+)\)", output)
    assert m, f"Cannot find '(ID: N)' in output:\n{output}"
    return int(m.group(1))


def test_add_creates_expense_and_returns_incremental_id(tmp_path: Path):
    # 准备隔离的环境变量（如果你的项目支持）
    env = os.environ.copy()
    if ENV_DB_KEY:
        # 给每次测试一个独立数据文件路径（或目录路径，取决于你的实现）
        env[ENV_DB_KEY] = str(tmp_path / "test_db.json")

    # 第一次 add
    p1 = run_cmd(CLI + ["add", "--description", "Lunch", "--amount", "20"], env=env)
    assert p1.returncode == 0, f"add failed.\nSTDOUT:\n{p1.stdout}\nSTDERR:\n{p1.stderr}"
    assert "Expense added successfully" in p1.stdout
    id1 = extract_id(p1.stdout)

    # 第二次 add
    p2 = run_cmd(CLI + ["add", "--description", "Dinner", "--amount", "10"], env=env)
    assert p2.returncode == 0, f"add failed.\nSTDOUT:\n{p2.stdout}\nSTDERR:\n{p2.stderr}"
    assert "Expense added successfully" in p2.stdout
    id2 = extract_id(p2.stdout)

    # ID 递增校验（至少应不同且递增 1）
    assert id2 == id1 + 1, f"Expected incremental IDs, got {id1} then {id2}"
