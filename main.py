# main.py
# 入口：读一段代码（默认用 samples/bad_example.py，或命令行传文件路径），
# 交给多 Agent 团队做 Review，打印最终报告。
#
# 用法：
#   python main.py                 # 用内置示例（含故意埋的已知问题）
#   python main.py 你的文件.py      # 审你自己的代码
#
# 跑之前必须设环境变量 DEEPSEEK_API_KEY（见 README）。

import os
import sys

from agents import get_client, review

# 默认示例代码的位置（里面有我们故意埋的已知问题，方便演示和评测）
DEFAULT_SAMPLE = os.path.join(os.path.dirname(__file__), "samples", "bad_example.py")


def main():
    client = get_client()

    # 命令行传了代码文件就用文件，否则用内置示例
    if len(sys.argv) > 1:
        path = sys.argv[1]
        with open(path, encoding="utf-8") as f:
            code = f.read()
        print("（读取文件：" + path + "）")
    else:
        with open(DEFAULT_SAMPLE, encoding="utf-8") as f:
            code = f.read()
        print("（未提供文件，使用内置示例 samples/bad_example.py）")

    print("=" * 50)
    report = review(code, client)
    print("\n===== 最终 Review 报告 =====")
    print(report)


main()
