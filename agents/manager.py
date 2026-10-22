# agents/manager.py
# 主管 Agent：用 function calling 把任务派给四个专家，再把意见汇总成一份报告。
#
# 这是整个项目的"大脑"——主管也是用 function calling，
# 只不过它调的"工具"是另外 4 个 Agent（专家）。
# 这就是多 Agent 协作的最小形态：有人统筹，有人分工。

import logging

from .llm import run_agent
from .experts import run_expert, EXPERT_NAMES

# 主管的工具箱：4 个"派发专家"的工具
# 模型看不懂 Python 函数，只能看懂这种 JSON 说明书
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


def _dispatch(name, args_json, code, client):
    """主管派专家：把工具名翻译成专家角色，调专家、打印、返回意见。"""
    role = TOOL_TO_ROLE[name]
    logging.info("主管派出专家：%s", EXPERT_NAMES[role])
    return run_expert(role, code, client)


def review(code, client):
    """跑一整轮多 Agent Review，返回最终报告字符串。"""

    # 闭包：把 code/client 绑进去，满足 run_agent 的 (name, args_json) 签名
    def executor(name, args_json):
        return _dispatch(name, args_json, code, client)

    system = (
        "你是一个代码 Review 团队的主管 Agent。"
        "你会收到一段代码，请调用合适的专家工具分工审查（4 个专家都应派到）。"
        "等所有专家返回意见后，停止调用工具，输出一份综合 Review 报告："
        "按『实现质量 / 逻辑缺陷 / 安全风险 / 测试覆盖』四个维度组织，"
        "每维度给出最关键的问题与建议，最后给一个总体结论（能否上线）。用中文。"
    )
    user = "请对以下代码做完整 Review：\n\n" + code

    logging.info("主管 Agent 开始协调 4 位专家做代码 Review ...")
    return run_agent(
        client,
        system,
        user,
        tools=TOOL_SCHEMAS,
        tool_executor=executor,
        max_rounds=10,
        role="manager",
    )
