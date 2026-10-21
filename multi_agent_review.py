# multi_agent_review.py
# 多 Agent 代码 Review 团队：把"单 Agent 调工具"升级成"一个主管带四个专家"
#
# 架构（就是之前那张图）：
#   主管 Agent  ──用 function calling 派活──▶  实现专家 / Bug 猎人 / 安全审计 / 测试
#        ▲                                            │ 各自审一个维度
#        └──────────── 汇总 4 份意见成报告 ◀──────────┘
#
# 关键点：每个"专家"本身也是一个 Agent（带自己人设的 DeepSeek 调用），
# 主管只是负责"决定派谁、收集意见、写总结"——这就是多 Agent 协作的最小形态。
#
# 跑之前必须设环境变量 DEEPSEEK_API_KEY（和 function_calling.py 一样）。

import os
import sys
from openai import OpenAI


# ---- 0. 内置一段"故意带问题"的示例代码，方便你没文件也能直接跑 ----
SAMPLE_CODE = '''
import os

DB_PWD = "123456"          # 问题：密码硬编码在源码里

def login(user, pwd):
    # 问题：SQL 拼接，有注入风险
    sql = "SELECT * FROM users WHERE name='%s' AND password='%s'" % (user, pwd)
    return sql             # 这里简化，真实会执行 sql

def divide(a, b):
    return a / b           # 问题：没处理 b=0，会抛 ZeroDivisionError

def double_list(items):
    result = []
    for i in range(len(items)):
        result.append(items[i] * 2)
    return result
'''


# ---- 1. 四个专家 Agent 的"人设"（system prompt） ----
# 每个专家拿到同样的代码，但只盯自己那一亩三分地
EXPERT_PROMPTS = {
    "implementer": (
        "你是一名资深实现工程师，负责代码 Review 中的『实现质量』维度。"
        "请关注：是否清晰实现了需求、结构/可读性、命名、是否有冗余或过度设计。"
        "用中文分条列出优点和问题，每条给出具体位置和一句话建议。"
    ),
    "bug_hunter": (
        "你是一名 Debug / 测试专家，负责代码 Review 中的『逻辑缺陷』维度。"
        "请关注：逻辑错误、边界条件（如空值/0/空列表）、异常未处理、潜在崩溃、死循环。"
        "用中文分条，每条指出具体位置、会怎样出错、怎么修。"
    ),
    "security": (
        "你是一名安全审计专家，负责代码 Review 中的『安全风险』维度。"
        "请关注：SQL 注入、XSS、硬编码密钥/密码、权限问题、敏感信息泄露、不安全的反序列化。"
        "用中文分条，每条说明风险等级（高/中/低）和修复建议。"
    ),
    "tester": (
        "你是一名测试工程师，负责代码 Review 中的『测试覆盖』维度。"
        "请关注：应该补哪些测试用例、哪些边界/异常用例最该覆盖、如何验证函数正确性。"
        "用中文列出一份可执行的测试清单。"
    ),
}


# ---- 2. 专家 Agent：一个带人设的 DeepSeek 调用 ----
def expert_review(role, code, client):
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": EXPERT_PROMPTS[role]},
            {"role": "user", "content": "请审查下面这段代码：\n\n" + code},
        ],
    )
    return resp.choices[0].message.content


# ---- 3. 主管 Agent 的工具箱：4 个"派发专家"的工具 ----
# 主管看不懂 Python 函数，只能看懂这种 JSON 说明书
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_implementer",
            "description": "派出『实现专家』审查代码实现质量/可读性",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_bug_hunter",
            "description": "派出『Bug 猎人』找逻辑缺陷和崩溃风险",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_security",
            "description": "派出『安全审计』找注入/硬编码密钥等安全风险",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_tester",
            "description": "派出『测试工程师』列出该补的测试用例",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# 工具名 -> 专家角色
TOOL_TO_ROLE = {
    "delegate_to_implementer": "implementer",
    "delegate_to_bug_hunter": "bug_hunter",
    "delegate_to_security": "security",
    "delegate_to_tester": "tester",
}


# ---- 4. 主管 Agent：编排 + 汇总 ----
def orchestrator(code, client):
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个代码 Review 团队的主管 Agent。"
                "你会收到一段代码，请调用合适的专家工具分工审查（4 个专家都应派到）。"
                "等所有专家返回意见后，停止调用工具，输出一份综合 Review 报告："
                "按『实现质量 / 逻辑缺陷 / 安全风险 / 测试覆盖』四个维度组织，"
                "每维度给出最关键的问题与建议，最后给一个总体结论（能否上线）。用中文。"
            ),
        },
        {"role": "user", "content": "请对以下代码做完整 Review：\n\n" + code},
    ]

    # 最多 10 轮，主管可能一次派多个、也可能分几次派
    for step in range(10):
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        # 主管不再调工具 -> 它在给最终报告
        if not msg.tool_calls:
            return msg.content

        messages.append(msg)
        for call in msg.tool_calls:
            role = TOOL_TO_ROLE[call.function.name]
            print("  -> 主管派出专家：" + role)
            result = expert_review(role, code, client)
            # 把专家意见喂回给主管
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })

    return "（超过最大轮数，主管还没给最终报告）"


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("⚠️ 没找到 DEEPSEEK_API_KEY 环境变量，没法跑真模型。")
        print("   先：set DEEPSEEK_API_KEY=你的key （Windows）")
        return

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    # 命令行传了代码文件就用文件，否则用内置示例
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            code = f.read()
        print("（读取文件：" + sys.argv[1] + "）")
    else:
        code = SAMPLE_CODE
        print("（未提供文件，使用内置示例代码片段）")

    print("=" * 50)
    print("主管 Agent 开始协调 4 位专家做代码 Review ...\n")
    report = orchestrator(code, client)
    print("\n===== 最终 Review 报告 =====")
    print(report)


main()
