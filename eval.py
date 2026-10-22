# eval.py
# 评测脚本：用内建评测集（eval/known_issues.json）检验多 Agent 团队
# 能否把示例代码里"故意埋的已知问题"都揪出来，并和"单 Agent 基线"对比。
#
# 指标：已知问题检出率 = 命中数 / 总数
# 判断方式：默认用 LLM-as-judge（让模型判断报告是否命中该问题，比关键词更准）；
#           若 judge 调用失败，退回关键词子串匹配作为兜底。
#
# 用法：python eval.py   （需要 DEEPSEEK_API_KEY）
#
# 说明：示例代码 samples/bad_example.py 里埋了 4 个已知问题，与 known_issues.json 一一对应。

import os
import json

from agents import get_client, review
from agents.llm import run_agent, print_stats

HERE = os.path.dirname(__file__)
ISSUES_PATH = os.path.join(HERE, "eval", "known_issues.json")
SAMPLE_PATH = os.path.join(HERE, "samples", "bad_example.py")


def load_issues():
    with open(ISSUES_PATH, encoding="utf-8") as f:
        return json.load(f)


def pct(x):
    return "%.0f%%" % (x * 100)


def judge_by_keyword(issue, report):
    kw = issue.get("keyword", "")
    return kw in report


def judge_by_llm(issue, report, client):
    """让模型判断报告是否明确指出了这个已知问题。失败则退回关键词匹配。"""
    system = (
        "你是一个严格的评测员。下面给你一份代码 Review 报告和一个『已知问题』。"
        "请判断报告是否明确指出了这个已知问题。"
        "只输出两行：第一行 YES 或 NO（是否命中），第二行用一句话说明理由。"
    )
    user = (
        "【已知问题】" + issue["id"] + " (" + issue["dimension"] + ", "
        + issue["severity"] + ")：" + issue["desc"] + "\n\n"
        + "【Review 报告】\n" + report + "\n\n"
        + "这个已知问题是否被报告明确指出？"
    )
    try:
        out = run_agent(client, system, user, role="judge")
        hit = out.strip().upper().startswith("YES")
        return hit, out.strip().replace("\n", " / ")
    except Exception:
        hit = judge_by_keyword(issue, report)
        return hit, "(judge 调用失败，退回关键词匹配)"


def review_single(code, client):
    """单 Agent 基线：不分工，让模型一次性审完四个方面。"""
    system = (
        "你是一名资深工程师，请对下面代码做完整的代码 Review，"
        "覆盖实现质量、逻辑缺陷、安全风险、测试覆盖四个方面，用中文分条。"
    )
    user = "请审查下面这段代码：\n\n" + code
    return run_agent(client, system, user, role="single_agent")


def evaluate(report, issues, client):
    hits = 0
    rows = []
    for issue in issues:
        hit, reason = judge_by_llm(issue, report, client)
        hits += 1 if hit else 0
        rows.append((hit, issue, reason))
    return hits, rows


def main():
    data = load_issues()
    issues = data["issues"]
    with open(SAMPLE_PATH, encoding="utf-8") as f:
        code = f.read()

    client = get_client()   # 没设 key 会直接报错并给出设置提示

    print("读取评测集：" + str(len(issues)) + " 个已知问题")
    print("=" * 50)

    print("[多 Agent 团队] 主管正在协调 4 位专家 ...")
    report_multi = review(code, client)

    print("[单 Agent 基线] 模型一次性审查 ...")
    report_single = review_single(code, client)

    hits_multi, rows_multi = evaluate(report_multi, issues, client)
    hits_single, rows_single = evaluate(report_single, issues, client)

    print("\n===== 评测结果 =====")
    print("\n[多 Agent 团队]")
    for hit, issue, reason in rows_multi:
        mark = "✅ 命中" if hit else "❌ 漏检"
        print("  [" + mark + "] " + issue["id"] + " " + issue["desc"])
        print("       " + reason)

    print("\n[单 Agent 基线]")
    for hit, issue, reason in rows_single:
        mark = "✅ 命中" if hit else "❌ 漏检"
        print("  [" + mark + "] " + issue["id"] + " " + issue["desc"])
        print("       " + reason)

    r_multi = hits_multi / len(issues)
    r_single = hits_single / len(issues)
    print("\n已知问题检出率对比：")
    print("  多 Agent 团队：" + str(hits_multi) + "/" + str(len(issues)) + " = " + pct(r_multi))
    print("  单 Agent 基线：" + str(hits_single) + "/" + str(len(issues)) + " = " + pct(r_single))
    if hits_multi > hits_single:
        print("  => 多 Agent 协作比单 Agent 多揪出 " + str(hits_multi - hits_single) + " 个问题。")
    elif hits_multi == hits_single:
        print("  => 两者检出数相同。")
    else:
        print("  => 这次单 Agent 反而更多（样本小，属正常波动，可多次运行取平均）。")

    print_stats()


main()
