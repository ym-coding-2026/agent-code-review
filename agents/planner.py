# agents/planner.py
# 主管的"动态规划"层：在正式派活之前，先用 AST 预分析代码特征，
# 决定【该派哪些专家】，而不是写死"4 个都派"。
#
# 这解决了一个 P0 遗留的诚实问题：原 manager 的 prompt 写死"4 个专家都应派到"，
# 所谓"自主规划"其实是假的。现在规划是真实发生的——
# 例如一段没有数据库操作的纯算法代码，就不需要派安全专家，省 token 也更聚焦。
#
# 预分析纯 AST，零成本；结果作为"建议"注入主管 prompt，
# 最终仍由主管用 function calling 自主拍板（建议可参考、可覆盖）。

import ast
import os


def recommend_experts(code, filepath=None):
    """根据代码特征给出专家派遣建议。

    返回 dict：
        {
          recommend: {role: bool},     # 建议是否派遣
          reasons:   {role: str},      # 建议理由（给主管看，也给日志/报告看）
        }
    """
    recommend = {"implementer": False, "bug_hunter": False,
                 "security": False, "tester": False}
    reasons = {}

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 解析不了就全派，让专家去发现语法问题
        for k in recommend:
            recommend[k] = True
            reasons[k] = "代码无法解析，保守地派出全部专家"
        return {"recommend": recommend, "reasons": reasons}

    has_function = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       for n in ast.walk(tree))
    has_class = any(isinstance(n, ast.ClassDef) for n in ast.walk(tree))

    # 是否像测试文件（自己就是测试，不必再派测试专家审"该补什么测试"）
    is_test_file = bool(filepath) and (
        os.path.basename(filepath).startswith("test_")
        or os.path.basename(filepath).endswith("_test.py")
    )

    # 安全维度：出现 SQL 字符串 / 密钥特征 / 网络或子进程调用
    sec_hit = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if ("select" in s.lower() and ("from" in s.lower() or "where" in s.lower())) \
                    or "password" in s.lower() or "sk-" in s.lower():
                sec_hit = True
                break
        if isinstance(node, ast.Call):
            f = node.func
            fn = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if fn in ("run", "Popen", "system", "popen", "urlopen", "request", "get", "post"):
                sec_hit = True
                break

    # —— 决策规则 ——
    # 实现质量：只要有函数或类，永远值得看
    if has_function or has_class:
        recommend["implementer"] = True
        reasons["implementer"] = "存在函数/类，需要看实现质量与可读性"
    # 逻辑缺陷：有函数就可能有边界/崩溃问题
    if has_function:
        recommend["bug_hunter"] = True
        reasons["bug_hunter"] = "存在函数定义，需排查逻辑缺陷与边界条件"
    # 安全风险：预分析命中 SQL/密钥/网络调用
    if sec_hit:
        recommend["security"] = True
        reasons["security"] = "检测到 SQL 字符串 / 密钥特征 / 网络或子进程调用"
    # 测试覆盖：有被测函数且自身不是测试文件
    if has_function and not is_test_file:
        recommend["tester"] = True
        reasons["tester"] = "存在可被测试的函数，且当前文件不是测试文件"

    return {"recommend": recommend, "reasons": reasons}


def recommendation_text(code, filepath=None):
    """把建议格式化成可读文本，注入主管 prompt。"""
    rec = recommend_experts(code, filepath)
    parts = []
    for role, on in rec["recommend"].items():
        tag = "建议派" if on else "可跳过"
        reason = rec["reasons"].get(role, "无特别信号")
        parts.append("  - %s：%s（%s）" % (role, tag, reason))
    return "【预分析派遣建议】\n" + "\n".join(parts)
