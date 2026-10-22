# agents/llm.py
# 共享层：DeepSeek 客户端 + 通用 Agent 对话循环
# 所有 Agent（主管、专家、评测裁判）都靠它跟模型说话，避免重复代码。
#
# 这是整个项目的"发动机"：Agent 的本质就是"循环调用 LLM + 必要时执行工具"。
#
# 相对第一版，这一版补了两块"工程化"能力（P0）：
#   1) 可观测性：每次调用自动统计 token 消耗与预估花费，并按 Agent 角色拆分，
#      方便看清"哪个 Agent 最费钱 / 调用最多 / 哪个维度最耗算力"。
#   2) 容错：API 调用带超时（30s）+ 失败重试（指数退避），
#      避免一次网络抖动或限流就让整个 Review 崩掉。

import logging
import os
import time

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# 日志统一格式：时间 + 级别 + 信息，方便后续接文件/监控
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# DeepSeek 的接入配置（和 function_calling.py 一致）
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"

# 全局调用统计（可观测性的核心数据源）
# by_role 按 Agent 角色拆分：主管 manager / 四个专家 / 单 Agent 基线 / 评测裁判 judge
STATS = {
    "calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cost_rmb": 0.0,
    "by_role": {},
}

# DeepSeek 公开计费近似（元 / 1M token）：输入 1 元、输出 2 元
# 这里只是估算，真实账单以平台为准
_PRICE_IN = 1.0
_PRICE_OUT = 2.0


def get_client():
    """创建并缓存一个 DeepSeek 客户端；没设 key 就报错提醒。

    关键改动：timeout=30 给每次 HTTP 请求设上限——
    模型卡住或网络慢时会在 30 秒内抛超时异常，进入下面的重试逻辑，
    而不是直接挂死。
    """
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，请先 pip install openai")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "⚠️ 没找到 DEEPSEEK_API_KEY 环境变量。\n"
            "   请先在本机终端设置：\n"
            "     Windows:  set DEEPSEEK_API_KEY=你的key\n"
            "   然后重新运行。"
        )
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, timeout=30)


def _record(role, usage):
    """把一次调用的 token 消耗记进全局统计，并按角色拆分。"""
    if usage is None:
        return
    pin = getattr(usage, "prompt_tokens", 0) or 0
    pout = getattr(usage, "completion_tokens", 0) or 0
    cost = pin / 1_000_000 * _PRICE_IN + pout / 1_000_000 * _PRICE_OUT
    STATS["calls"] += 1
    STATS["prompt_tokens"] += pin
    STATS["completion_tokens"] += pout
    STATS["cost_rmb"] += cost
    bucket = STATS["by_role"].setdefault(
        role, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_rmb": 0.0}
    )
    bucket["calls"] += 1
    bucket["prompt_tokens"] += pin
    bucket["completion_tokens"] += pout
    bucket["cost_rmb"] += cost


def _create(client, kwargs, role):
    """真正发起一次 API 调用（无重试，重试由上层 run_agent 负责）。"""
    resp = client.chat.completions.create(**kwargs)
    _record(role, resp.usage)
    return resp


def run_agent(client, system, user, tools=None, tool_executor=None,
              max_rounds=10, role="agent", max_retries=3):
    """
    一个 Agent 的通用循环：
       发消息 -> 看模型要不要调工具 -> 调完把结果喂回去 -> 直到模型直接回答。

    参数：
        system: 人设（system prompt）
        user:   用户问题
        tools:  工具的 JSON 说明书（不需要工具就传 None）
        tool_executor: 函数 (name, args_json) -> 结果字符串，主管用它来"派专家"
        max_rounds:   最多循环几轮，防止模型抽风一直调工具停不下来
        role:         这个 Agent 的角色名，仅用于可观测性统计
        max_retries:  单次 API 调用失败后的最大重试次数（指数退避）
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

        # —— 带重试的调用：网络抖动 / 限流不会直接整崩 ——
        resp = None
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = _create(client, kwargs, role)
                break
            except Exception as e:  # noqa: BLE001 —— 网络/限流等统一重试
                last_err = e
                logging.warning(
                    "[%s] 调用失败（%s）：%s，%.0f 秒后重试（第 %d/%d 次）",
                    role, type(e).__name__, e, float(attempt * 2), attempt, max_retries,
                )
                time.sleep(attempt * 2)  # 指数退避：1s、2s、4s...
        if resp is None:
            logging.error("[%s] 重试 %d 次后仍失败，终止本轮", role, max_retries)
            raise last_err

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
            # 边界情况：模型要调工具却没给执行器，直接收尾
            break

    return "（超过最大轮数，Agent 还没给出最终回答）"


def print_stats():
    """打印累计的可观测性统计（token / 花费 / 按角色拆分）。

    在 main.py / eval.py 末尾调用一次，即可看到本次运行的成本画像。
    """
    print("\n" + "=" * 50)
    print("可观测性统计（本次运行，花费为 DeepSeek 公开价估算）")
    print("  总调用次数：" + str(STATS["calls"]))
    print("  总输入 token：" + str(STATS["prompt_tokens"]))
    print("  总输出 token：" + str(STATS["completion_tokens"]))
    print("  预估花费：¥%.4f" % STATS["cost_rmb"])
    print("  --- 按 Agent 角色拆分（按花费降序） ---")
    if not STATS["by_role"]:
        print("    （无数据）")
    ranked = sorted(STATS["by_role"].items(),
                    key=lambda kv: kv[1]["cost_rmb"], reverse=True)
    for role, b in ranked:
        print("    [%-12s] 调用%d次 | 输入%d | 输出%d | ¥%.4f" % (
            role, b["calls"], b["prompt_tokens"], b["completion_tokens"], b["cost_rmb"]))
