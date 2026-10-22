# agents/manager.py
# 主管 Agent：用 function calling 把任务派给专家，再把意见汇总成一份报告。
#
# 这是整个项目的"大脑"——主管也是用 function calling，
# 只不过它调的"工具"是另外几个 Agent（专家）。
# 这就是多 Agent 协作的最小形态：有人统筹，有人分工。
#
# P1 升级：主管不再写死"4 个专家都派到"（那是假的规划）。
# 改为先用 agents/planner.py 做真实 AST 预分析，把"该派哪些专家"的建议
# 注入 prompt，再由主管用 function calling 自主拍板（建议可参考、可覆盖）。
# 同时把 filepath 透传给专家，让测试专家能探测同目录的真实测试文件。

import logging

from .llm import run_agent
from .experts import run_expert, EXPERT_NAMES
from .planner import recommendation_text


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


def _dispatch(name, args_json, code, client, filepath):
    """主管派专家：把工具名翻译成专家角色，调专家、打印、返回意见。"""
    role = TOOL_TO_ROLE[name]
    logging.info("主管派出专家：%s", EXPERT_NAMES[role])
    return run_expert(role, code, client, filepath)


def review(code, client, filepath=None, diff_mode=False):
    """跑一整轮多 Agent Review，返回最终报告字符串。

    filepath:   被审代码路径（可选，测试专家用它探测同目录测试文件）
    diff_mode:  True 表示审的是 git diff，要求专家在每条问题后给可复制的修复代码
    """

    # 闭包：把 code/client/filepath 绑进去，满足 run_agent 的 (name, args_json) 签名
    def executor(name, args_json):
        return _dispatch(name, args_json, code, client, filepath)

    # 真实动态规划：先 AST 预分析，再交给主管自主决策
    plan_hint = recommendation_text(code, filepath)
    logging.info("主管预分析完成：\n%s", plan_hint)

    diff_instruction = ""
    if diff_mode:
        diff_instruction = (
            "本次审查的是 git 改动（diff）。请在每条问题后给出【具体修复代码】"
            "（可直接复制应用的片段，标注改动位置）。"
        )

    system = (
        "你是一个代码 Review 团队的主管 Agent。"
        "你会收到一段代码（可能是完整文件，也可能是 git diff 改动）。"
        "请参考下方的【预分析派遣建议】，用合适的专家工具分工审查——"
        "不必把 4 个专家都派出去，按代码特征自主决定派哪些、派几次。"
        "等专家返回意见后，停止调用工具，输出一份综合 Review 报告："
        "按『实现质量 / 逻辑缺陷 / 安全风险 / 测试覆盖』四个维度组织，"
        "每维度给出最关键的问题与建议，最后给一个总体结论（能否上线）。用中文。"
        + diff_instruction
    )
    user = plan_hint + "\n\n请对以下代码做 Review：\n\n" + code

    logging.info("主管 Agent 开始协调专家做代码 Review ...")
    return run_agent(
        client,
        system,
        user,
        tools=TOOL_SCHEMAS,
        tool_executor=executor,
        max_rounds=10,
        role="manager",
    )
