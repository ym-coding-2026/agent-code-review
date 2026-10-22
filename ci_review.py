# ci_review.py
# CI 自动静态审查入口（P2）。让项目"自己审自己的 PR"——
# 这比"只跑一遍 LLM demo"更接近真实代码审查工具：
#   1) 自动拿到本次改动的 diff（本地未提交 / 已暂存 / PR 的 origin/main...HEAD）；
#   2) 只对改动的 .py 文件、且只报"本次新增/修改行"上的问题（行号精确）；
#   3) 打印按文件分组的报告，可选 --json 输出结构化结果；
#   4) 一旦发现 high 级问题，以退出码 1 失败 -> 卡住 PR 合并（CI gate）。
#
# 注意：这一步只跑【确定性静态分析】，不需要 API Key、不花钱，
# 因此适合放在 CI 里每次 PR 都跑。LLM 深度审查（main.py --diff）是按需的人工动作。
#
# 用法：
#   python ci_review.py            # 审当前未提交/已暂存的改动
#   python ci_review.py --json     # 额外输出 JSON
#   python ci_review.py --base origin/main   # 指定与哪个分支比（CI 里常用）

import os
import sys
import json
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from agents.tools.gitdiff import get_diff
from agents.tools.static import scan_diff


def _pick_diff(base=None):
    """自动选择 diff 来源，优先顺序：未提交 -> 已暂存 -> 与 base 的比对。"""
    def _run(args):
        try:
            out = subprocess.run(args, cwd=HERE, capture_output=True,
                                 text=True, timeout=30)
            return out.stdout
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return ""

    if base:
        d = _run(["git", "diff", base + "...HEAD"])
        if d.strip():
            return d
    # 本地未提交
    d = _run(["git", "diff"])
    if d.strip():
        return d
    # 已暂存
    d = _run(["git", "diff", "--cached"])
    if d.strip():
        return d
    # 退而求其次：最近一次提交（用于本地无未提交改动时的演示）
    return _run(["git", "diff", "HEAD~1"])


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    base = None
    for a in args:
        if a.startswith("--base="):
            base = a.split("=", 1)[1]

    diff_text = _pick_diff(base)
    findings = scan_diff(diff_text, repo_root=HERE)

    if not findings:
        print("✅ 静态审查通过：本次改动未命中任何高风险/中风险规则。")
        if as_json:
            print("\n===JSON===\n" + json.dumps([], ensure_ascii=False))
        sys.exit(0)

    # 按文件分组打印
    by_file = {}
    for f in findings:
        by_file.setdefault(f["file"], []).append(f)

    high = [f for f in findings if f["severity"] == "high"]
    med = [f for f in findings if f["severity"] == "medium"]
    low = [f for f in findings if f["severity"] == "low"]

    print("⚠️  静态审查发现 %d 个问题（高 %d / 中 %d / 低 %d）："
          % (len(findings), len(high), len(med), len(low)))
    for fname, items in by_file.items():
        print("\n📄 " + fname)
        for f in items:
            print("  L%d [%-6s] %s：%s" % (f["line"], f["severity"], f["rule"], f["message"]))

    if as_json:
        print("\n===JSON===\n" + json.dumps(findings, ensure_ascii=False, indent=2))

    # CI gate：high 级问题直接失败；中/低只告警不卡（可按需调整）
    if high:
        print("\n❌ 存在高危问题，CI 判定失败（exit 1）。")
        sys.exit(1)
    print("\n✅ 无高危问题，CI 通过（中/低级建议人工跟进）。")
    sys.exit(0)


main()
