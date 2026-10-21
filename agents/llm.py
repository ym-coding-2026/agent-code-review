# agents/llm.py
# 共享层：DeepSeek 客户端 + 一个通用的 Agent 对话循环
# 所有 Agent（主管、专家）都靠它跟模型说话，避免重复代码。
#
# 这是整个项目的"发动机"：Agent 的本质就是"循环调用 LLM + 必要时执行工具"。

import os
from openai import OpenAI

# DeepSeek 的接入配置（和 function_calling.py 一致）
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"


def get_client():
    """拿到一个 DeepSeek 客户端；没设 key 就报错提醒。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "⚠️ 没找到 DEEPSEEK_API_KEY 环境变量。\n"
            "   请先在本机终端设置：\n"
            "     Windows:  set DEEPSEEK_API_KEY=你的key\n"
            "   然后重新运行。"
        )
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def run_agent(client, system, user, tools=None, tool_executor=None, max_rounds=10):
    """
    一个 Agent 的通用循环：
       发消息 -> 看模型要不要调工具 -> 调完把结果喂回去 -> 直到模型直接回答。

    参数：
        system: 人设（system prompt）
        user:   用户问题
        tools:  工具的 JSON 说明书（不需要工具就传 None）
        tool_executor: 函数 (name, args_json) -> 结果字符串，主管用它来"派专家"
        max_rounds:   最多循环几轮，防止模型抽风一直调工具停不下来
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    for _ in range(max_rounds):
        kwargs = {"model": MODEL, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"   # 让模型自己决定调不调、调哪个

        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        # 模型不再要工具 -> 它在给最终答案
        if not msg.tool_calls:
            return msg.content

        # 模型要调工具：必须把它的"意图"原样塞回对话（OpenAI 协议要求）
        messages.append(msg)

        # 逐个执行它想调的工具，并把结果喂回去
        if tool_executor:
            for call in msg.tool_calls:
                result = tool_executor(call.function.name, call.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })
        else:
            # 建模异常：模型要调工具却没给执行器，直接收尾
            break

    return "（超过最大轮数，Agent 还没给出最终回答）"
