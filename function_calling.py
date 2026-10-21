# function_calling.py
# 真实的 Agent 最小演示：用 DeepSeek 的 function calling，让【模型自己】决定调哪个工具
#
# 这是从 agent_basics.py 的"假大脑（用规则猜）"升级成"真大脑（模型决策）"的关键一跳。
# 区别一句话：以前是"if 看到数字就调计算器"，现在是"把工具说明书给模型，它自己判断该调谁"。
#
# 跑之前必须设环境变量 DEEPSEEK_API_KEY（见底部说明）。

import os
import sys
import json
import datetime

from openai import OpenAI


# ---- 1. 工具箱：Agent 的"手"（和 agent_basics.py 一样，只是这次真被模型调用） ----
def tool_calculator(expression):
    """算一个数学表达式，比如 "23*17+5" """
    try:
        return str(eval(expression))
    except Exception as e:
        return "算不出来：" + str(e)


def tool_current_time(arg=None):
    """返回当前时间（不需要参数）"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tool_search_docs(keyword):
    """模拟查资料库。真实项目里，这里就是你做过的 RAG！"""
    fake_db = {
        "python": "Python 是动态类型语言，用缩进表示代码块。",
        "agent": "Agent 能自主规划并调用工具完成任务。",
        "rag": "RAG 是检索增强生成，先查资料再回答。",
    }
    for key in fake_db:
        if key in keyword:
            return fake_db[key]
    return fake_db.get(keyword, "资料库里没有这条。")


# 名字 -> 函数，Agent 按模型给的名字取用
TOOLS = {
    "calculator": tool_calculator,
    "current_time": tool_current_time,
    "search_docs": tool_search_docs,
}


# ---- 2. 把工具写成"说明书"（JSON），模型看不懂 Python，只能看懂这个 ----
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "计算一个数学表达式，例如 23*17+5",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "要算的数学表达式"}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_time",
            "description": "获取当前的日期和时间",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "在知识库中查找某个概念的解释，例如 agent、rag、python",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要查的关键词"}
                },
                "required": ["keyword"],
            },
        },
    },
]


# ---- 3. 真大脑：DeepSeek（自主决定调哪个工具、调几次） ----
def agent(question, client):
    print("用户问：" + question)
    messages = [{"role": "user", "content": question}]

    # 最多 5 轮：防止模型抽风一直调工具停不下来
    for step in range(5):
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",   # 让模型自己决定：调不调、调哪个
        )
        msg = resp.choices[0].message

        # 模型不再要工具了 -> 直接给最终答案
        if not msg.tool_calls:
            print("  -> 模型最终回答：" + msg.content)
            return msg.content

        # 模型要调工具：必须把它的"意图"原样塞回对话（OpenAI 协议要求）
        messages.append(msg)

        # 逐个执行它想调的工具
        for call in msg.tool_calls:
            name = call.function.name
            args = json.loads(call.function.arguments)
            print("  -> 模型决定调用工具：" + name + "，参数：" + str(args))
            result = TOOLS[name](**args)
            print("  -> 工具返回：" + str(result))
            # 把工具结果喂回给模型，让它接着想下一步（或给出最终答案）
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": str(result),
            })

    return "（超过最大轮数，模型还没给出最终答案）"


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("⚠️ 没找到 DEEPSEEK_API_KEY 环境变量，没法跑真模型。")
        print("   请先在本机终端设置：")
        print("     Windows:  set DEEPSEEK_API_KEY=你的key")
        print("   然后重新运行本脚本。")
        print("   （key 在 DeepSeek 开放平台 https://platform.deepseek.com 申请）")
        return

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    questions = sys.argv[1:] or ["帮我算 23*17+5", "现在几点了", "agent 是什么"]
    for q in questions:
        agent(q, client)
        print("-" * 30)


main()
