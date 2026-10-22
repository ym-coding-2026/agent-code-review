# agents/tools/__init__.py
# 真实工具层：把"静态分析 / git diff"等确定性能力包成可被专家调用的工具。
# 这些工具只依赖 Python 标准库（外加可选的 pytest），不需要额外 pip 安装，
# 因此它们能作为"事实证据"喂给 LLM，而不是让模型凭空猜。
from .static import analyze_security, analyze_bugs, analyze_tests
from .gitdiff import get_diff, parse_diff

__all__ = [
    "analyze_security",
    "analyze_bugs",
    "analyze_tests",
    "get_diff",
    "parse_diff",
]
