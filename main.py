# main.py
# 入口：把代码交给多 Agent 团队做 Review，打印最终报告。
#
# 用法：
#   python main.py                 # 用内置示例（含故意埋的已知问题）
#   python main.py 你的文件.py      # 审你自己的某个代码文件
#   python main.py --diff          # 审当前仓库未提交的 git 改动（真实工作流）
#
# 跑之前必须设环境变量 DEEPSEEK_API_KEY（见 README）。

import os
import sys

from agents import get_client, review
from agents.llm import print_stats
from agents.tools.gitdiff import get_diff, diff_to_review_text

# 默认示例代码的位置（里面有我们故意埋的已知问题，方便演示和评测）
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SAMPLE = os.path.join(HERE, "samples", "bad_example.py")


def main():
    client = get_client()

    # —— 解析参数：--diff 模式 / 指定文件 / 默认示例 ——
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]

    if "--diff" in flags:
        diff_text = get_diff(HERE)
        review_text = diff_to_review_text(diff_text)
        if not review_text.strip():
            print("（当前没有未提交的改动。可先改点代码，或用 `python main.py 文件.py` 审指定文件）")
            return
        print("（审查模式：git diff 未提交改动）")
        code = review_text
        filepath = None
        diff_mode = True
    elif args:
        path = args[0]
        with open(path, encoding="utf-8") as f:
            code = f.read()
        filepath = os.path.abspath(path)
        print("（读取文件：" + path + "）")
        diff_mode = False
    else:
        with open(DEFAULT_SAMPLE, encoding="utf-8") as f:
            code = f.read()
        filepath = DEFAULT_SAMPLE
        print("（未提供文件，使用内置示例 samples/bad_example.py）")
        diff_mode = False

    print("=" * 50)
    report = review(code, client, filepath=filepath, diff_mode=diff_mode)
    print("\n===== 最终 Review 报告 =====")
    print(report)
    print_stats()


main()
