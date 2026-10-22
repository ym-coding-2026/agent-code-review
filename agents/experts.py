# agents/experts.py
# 四个专家 Agent：实现质量 / 逻辑缺陷 / 安全风险 / 测试覆盖
# 每个专家拿到同样的代码，但只盯自己那一亩三分地——这就是"分工审查"。
#
# P1 升级（关键）：专家不再是"纯凭感觉看代码"。
#   security / bug_hunter / tester 三个专家会先跑【真实的静态分析工具】
#   （agents/tools/static.py，纯 AST，确定性、零成本、零幻觉），
#   把"哪一行、什么规则、什么风险"作为【事实证据】注入 prompt，
#   再由 LLM 综合成带行号、可落地的报告。
#
# 这就是 "工具增强型 Agent（tool-augmented agent）"：工具负责"找得到"，
# 模型负责"讲得清"，两层分离，避免模型瞎编行号。

import logging

from .llm import run_agent
from .tools.static import (
    analyze_security, analyze_bugs, analyze_tests,
    format_findings, format_test_result,
)

# 专家的人设（system prompt），决定它看问题的视角
EXPERT_PROMPTS = {
    "implementer": (
        "你是一名资深实现工程师，负责代码 Review 中的『实现质量』维度。"
        "请关注：是否清晰实现了需求、结构/可读性、命名、是否有冗余或过度设计。"
        "用中文分条列出优点和问题，每条给出具体位置和一句话建议。"
    ),
    "bug_hunter": (
        "你是一名 Debug / 测试专家，负责代码 Review 中的『逻辑缺陷』维度。"
        "请关注：逻辑错误、边界条件（如空值/0/空列表）、异常未处理、潜在崩溃、死循环。"
        "下方会附带【自动化静态扫描结果】作为事实依据，请结合它来写，不要凭空编造行号。"
        "用中文分条，每条指出具体位置、会怎样出错、怎么修。"
    ),
    "security": (
        "你是一名安全审计专家，负责代码 Review 中的『安全风险』维度。"
        "请关注：SQL 注入、XSS、硬编码密钥/密码、权限问题、敏感信息泄露、不安全的反序列化。"
        "下方会附带【自动化静态扫描结果】作为事实依据，请结合它来写，不要凭空编造行号。"
        "用中文分条，每条说明风险等级（高/中/低）和修复建议。"
    ),
    "tester": (
        "你是一名测试工程师，负责代码 Review 中的『测试覆盖』维度。"
        "下方会附带【自动化测试分析】作为事实依据（定义了哪些函数、有没有测试文件、pytest 收集到几个测试）。"
        "请基于它列出一份可执行的测试清单：哪些函数最该补测试、该覆盖哪些边界/异常用例。"
        "用中文列出，每条对应一个函数或场景。"
    ),
}

# 专家在报告里出现的中文名（用于主管汇总和评测打分）
EXPERT_NAMES = {
    "implementer": "实现质量",
    "bug_hunter": "逻辑缺陷",
    "security": "安全风险",
    "tester": "测试覆盖",
}


def _evidence_for(role, code, filepath):
    """按角色跑对应的真实静态工具，返回要注入 prompt 的证据文本。"""
    if role == "security":
        findings = analyze_security(code)
        return "【自动化静态扫描结果（安全维度，事实依据）】\n" + format_findings(findings)
    if role == "bug_hunter":
        findings = analyze_bugs(code)
        return "【自动化静态扫描结果（逻辑维度，事实依据）】\n" + format_findings(findings)
    if role == "tester":
        res = analyze_tests(code, filepath)
        return "【自动化测试分析（事实依据）】\n" + format_test_result(res)
    return ""  # implementer 偏主观，不注入工具证据


def run_expert(role, code, client, filepath=None):
    """让某个专家审一遍代码，返回它的分条意见（字符串）。

    role:     专家角色 key（见 EXPERT_NAMES）
    code:     被审查的代码文本
    client:   DeepSeek 客户端
    filepath: 代码文件路径（可选，测试分析需要用来探测同目录测试文件）
    """
    system = EXPERT_PROMPTS[role]
    evidence = _evidence_for(role, code, filepath)

    user = "请审查下面这段代码：\n\n" + code
    if evidence:
        user += "\n\n" + evidence + "\n\n请基于上述事实依据给出你的审查意见。"

    logging.info("专家 [%s] 已加载工具证据，开始审查", EXPERT_NAMES[role])
    # 专家是单轮：不需要工具，直接让模型答；role 用于可观测性统计
    return run_agent(client, system, user, role=role)
