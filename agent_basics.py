# agent_basics.py
# 最小 Agent 演示：不依赖任何 API key，纯标准库就能跑
# 目的：让你看懂 Agent 最核心的一件事——"自己决定调哪个工具"
#
# 真实项目里，"决定调哪个工具"这一步是 LLM（大模型）做的；
# 这里为了让你离线也能跑、也能看懂，用几条简单规则代替 LLM。
# 流程是完全一样的：思考 -> 选工具 -> 执行 -> 看结果 -> 回答。

import sys
import re
import datetime


# ---- 1. 工具箱：Agent 的"手" ----
# 每个工具就是一个普通函数。Agent 不会自己算，它靠调这些函数干活。
def tool_calculator(expression):
    """算一个数学表达式，比如 "23*17+5" """
    try:
        return str(eval(expression))
    except Exception as e:
        return "算不出来：" + str(e)


def tool_current_time(arg=None):
    """返回当前时间（不需要参数，但统一接一个参数以便统一调用）"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tool_search_docs(keyword):
    """模拟查资料库。真实项目里，这里就是你的 RAG！"""
    fake_db = {
        "python": "Python 是动态类型语言，用缩进表示代码块。",
        "agent": "Agent 能自主规划并调用工具完成任务。",
        "rag": "RAG 是检索增强生成，先查资料再回答。",
    }
    # 从用户的话里认出已知关键词，比如 "agent 是什么" 认出 agent
    for key in fake_db:
        if key in keyword:
            return fake_db[key]
    return fake_db.get(keyword, "资料库里没有这条。")


# 把工具收进一个字典，Agent 按名字取用
TOOLS = {
    "calculator": tool_calculator,
    "current_time": tool_current_time,
    "search_docs": tool_search_docs,
}


# ---- 2. "假大脑"：决定调哪个工具 ----
# 真实项目里这一步是 LLM 返回 "我要调 calculator，参数是 23*17+5"；
# 这里用规则模拟，方便你离线看懂流程。
def decide(question):
    if any(ch.isdigit() for ch in question) and any(c in question for c in "+-*/()%"):
        # 从自然语言里抠出纯数学表达式，比如 "帮我算 23*17+5" -> "23*17+5"
        m = re.search(r"[\d\+\-\*/\(\)\%\.]+", question)
        expr = m.group(0) if m else question
        return "calculator", expr
    if "时间" in question or "几点" in question:
        return "current_time", ""
    if "资料" in question or "是什么" in question or "文档" in question:
        return "search_docs", question
    return None, None


# ---- 3. Agent 主循环 ----
def agent(question):
    print("用户问：" + question)
    tool_name, arg = decide(question)
    if tool_name is None:
        # 真实 Agent 在这里会调 LLM 兜底生成一句自然语言回答
        return "我暂时不会，真实 Agent 会在这里调 LLM 兜底。"
    print("  -> Agent 决定调用工具：" + tool_name + "，参数：" + repr(arg))
    result = TOOLS[tool_name](arg)
    print("  -> 工具返回：" + result)
    return "答案（来自 " + tool_name + "）：" + result


# 没在命令行传问题，就跑几个内置示例
questions = sys.argv[1:] or ["帮我算 23*17+5", "现在几点了", "agent 是什么"]
for q in questions:
    print(agent(q))
    print("-" * 30)
