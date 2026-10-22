# agents/tools/static.py
# 真实的静态分析工具（纯标准库：ast + re，无需额外依赖）。
#
# 这是 P1 的"地基"：专家不再只靠 LLM 凭感觉看代码，
# 而是先跑这些工具，拿到【确定性的事实证据】（哪一行、什么规则、什么风险），
# 再把证据交给 LLM 综合成报告。
#
# 设计哲学（面试能讲清楚）：
#   - 静态层负责"找得到"——规则确定、可复现、零成本、零幻觉；
#   - LLM 层负责"讲得清"——把证据翻译成人话、排优先级、给修复建议。
# 两层分离，既避免模型瞎编行号，又能把确定性检查当作评测的"真值"。

import ast
import os
import re
import subprocess

from .gitdiff import parse_diff

# 看起来像密钥/密码的变量名（赋值给字符串字面量时报警）。
# 用带边界的正则，避免误伤 tokenizer / mytoken 这类正常标识符，
# 但能认出 db_pwd、api_key、secret_key、Password 等写法。
_SECRET_RE = re.compile(
    r"(?i)(?<![a-z0-9])(pwd|passwd|password|secret|apikey|api_key|"
    r"token|access_key|accesskey|private_key|privatekey|secret_key)(?![a-z0-9])"
)
# 字面量里像 API Key 的特征
_APIKEY_LITERAL = re.compile(r"(?i)sk-[a-z0-9]{16,}")
# SQL 关键字：出现 SELECT/INSERT... 且带 FROM/INTO/WHERE 之类
_SQL_KEYWORDS = re.compile(
    r"(?i)\b(select|insert|update|delete|drop|merge)\b[^;]{0,120}\b(from|into|table|where|values)\b"
)
# 危险的调用
_DANGEROUS_CALLS = {
    "eval": ("high", "使用了 eval()，可能执行任意代码"),
    "exec": ("high", "使用了 exec()，可能执行任意代码"),
    "os_system": ("medium", "使用了 os.system()，优先改用 subprocess 并避免 shell=True"),
}
_PICKLE = re.compile(r"(?i)pickle\.loads?")
_YAML_LOAD = re.compile(r"(?i)yaml\.load\b")


def _severity_rank(sev):
    return {"high": 3, "medium": 2, "low": 1}.get(sev, 1)


def analyze_security(code):
    """确定性安全检查：硬编码密钥、SQL 字符串拼接、危险调用。

    返回 list[dict]，每项 {line, severity, rule, message}，按行号排序。
    """
    findings = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [{"line": 0, "severity": "low",
                 "rule": "parse_error", "message": "代码无法解析（语法错误），跳过静态扫描"}]

    for node in ast.walk(tree):
        # 1) 硬编码密钥：Name = "字符串字面量"
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Name)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    name = tgt.id.lower()
                    val = node.value.value
                    if _SECRET_RE.search(name):
                        findings.append({
                            "line": node.value.lineno, "severity": "high",
                            "rule": "HARDCODED_SECRET",
                            "message": "变量 %s 把密钥/密码硬编码在源码中" % tgt.id,
                        })
                    elif _APIKEY_LITERAL.search(val):
                        findings.append({
                            "line": node.value.lineno, "severity": "high",
                            "rule": "HARDCODED_SECRET",
                            "message": "源码中出现疑似 API Key 字面量",
                        })

        # 2) 字符串字面量里的 SQL（且是拼接构造的）
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if _SQL_KEYWORDS.search(s) and ("%" in s or "{" in s or "+" in s or ".format" in s):
                findings.append({
                    "line": node.lineno, "severity": "high",
                    "rule": "SQL_INJECTION",
                    "message": "通过字符串拼接构造 SQL，存在 SQL 注入风险（建议参数化查询）",
                })

        # 3) 危险调用
        if isinstance(node, ast.Call):
            func = node.func
            fname = ""
            if isinstance(func, ast.Name):
                fname = func.id
            elif isinstance(func, ast.Attribute):
                fname = func.attr
                # 捕获 os.system / os.popen
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    if fname in ("system", "popen"):
                        findings.append({
                            "line": node.lineno, "severity": "medium",
                            "rule": "OS_SYSTEM",
                            "message": "直接调用 os.%s()，优先用 subprocess 并避免 shell=True" % fname,
                        })
            if fname in _DANGEROUS_CALLS:
                sev, msg = _DANGEROUS_CALLS[fname]
                findings.append({"line": node.lineno, "severity": sev,
                                 "rule": "DANGEROUS_CALL", "message": msg})
            # subprocess 带 shell=True
            if fname == "run" or fname == "Popen" or fname == "call":
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        findings.append({
                            "line": node.lineno, "severity": "high",
                            "rule": "SUBPROCESS_SHELL",
                            "message": "subprocess 使用了 shell=True，存在命令注入风险",
                        })
            src = _call_source(node, code)
            if _PICKLE.search(src):
                findings.append({"line": node.lineno, "severity": "medium",
                                 "rule": "PICKLE",
                                 "message": "使用了 pickle 反序列化，可能执行任意代码（避免反序列化不可信数据）"})
            if _YAML_LOAD.search(src):
                findings.append({"line": node.lineno, "severity": "medium",
                                 "rule": "YAML_LOAD",
                                 "message": "yaml.load() 未指定 SafeLoader，存在反序列化风险"})

    findings.sort(key=lambda f: (f["line"], -_severity_rank(f["severity"])))
    return findings


def analyze_bugs(code):
    """确定性逻辑缺陷检查：除零、裸 except、可变默认参数等。

    返回 list[dict]，每项 {line, severity, rule, message}。
    """
    findings = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        # 1) 除法操作：提醒确认除数不为 0
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            findings.append({
                "line": node.lineno, "severity": "medium",
                "rule": "DIVISION_BY_ZERO",
                "message": "除法操作，需确认右操作数不会出现 0（否则抛 ZeroDivisionError）",
            })
        # 2) 裸 except:（吞掉所有异常）
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append({
                "line": node.lineno, "severity": "medium",
                "rule": "BARE_EXCEPT",
                "message": "使用了裸 except:，会吞掉包括 KeyboardInterrupt 在内的所有异常",
            })
        # 3) 可变默认参数：def f(x=[])
        if isinstance(node, ast.FunctionDef):
            for d in node.args.defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    findings.append({
                        "line": d.lineno, "severity": "medium",
                        "rule": "MUTABLE_DEFAULT",
                        "message": "函数 %s 使用了可变默认参数，多次调用会共享同一对象" % node.name,
                    })
    findings.sort(key=lambda f: f["line"])
    return findings


def analyze_tests(code, filepath=None):
    """测试覆盖分析：AST 数函数 + 真实探测项目里有没有测试文件 / pytest 能收集多少测试。

    返回 dict：{functions, async_functions, classes, test_files, pytest_collected, message}
    """
    result = {"functions": [], "async_functions": [], "classes": [],
              "test_files": [], "pytest_collected": None, "message": ""}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        result["message"] = "代码无法解析，跳过测试分析"
        return result

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            result["functions"].append(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            result["async_functions"].append(node.name)
        elif isinstance(node, ast.ClassDef):
            result["classes"].append(node.name)

    directory = os.path.dirname(filepath) if filepath else None
    if directory:
        try:
            entries = os.listdir(directory)
        except OSError:
            entries = []
        result["test_files"] = [e for e in entries
                                if (e.startswith("test_") or e.endswith("_test.py"))
                                and e.endswith(".py")]

    # 真实跑一次 pytest 收集（可选能力，失败则优雅降级）
    if directory and result["test_files"]:
        try:
            out = subprocess.run(
                ["pytest", "--co", "-q", directory],
                capture_output=True, text=True, timeout=30,
            )
            m = re.search(r"(\d+)\s+tests? collected", out.stdout + out.stderr)
            if m:
                result["pytest_collected"] = int(m.group(1))
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            # pytest 没装或调用失败：保持 None，不报错（优雅降级）
            result["pytest_collected"] = None

    defined = len(result["functions"]) + len(result["async_functions"])
    if result["test_files"]:
        result["message"] = "定义了 %d 个函数，目录内发现 %d 个测试文件" % (
            defined, len(result["test_files"]))
    else:
        result["message"] = ("定义了 %d 个函数，但目录内未发现任何测试文件（test_*.py）"
                             % defined)
    return result


def _call_source(node, code):
    """粗略取一段调用源码用于正则匹配（pickle/yaml）。"""
    try:
        line = code.splitlines()[node.lineno - 1]
        return line
    except (IndexError, AttributeError):
        return ""


def format_findings(findings):
    """把 findings 列表格式化成可读文本，供注入 LLM prompt。"""
    if not findings:
        return "（静态扫描未发现明确风险特征）"
    lines = []
    for f in findings:
        ln = f.get("line", 0)
        lines.append("  L%d [%s] %s：%s" % (ln, f["severity"], f["rule"], f["message"]))
    return "\n".join(lines)


def format_test_result(res):
    """把 analyze_tests 的结果格式化成可读文本。"""
    if not res:
        return "（测试分析不可用）"
    parts = ["  定义函数：%s" % (", ".join(res["functions"]) or "无")]
    if res["async_functions"]:
        parts.append("  异步函数：%s" % ", ".join(res["async_functions"]))
    if res["test_files"]:
        parts.append("  目录内测试文件：%s" % ", ".join(res["test_files"]))
        if res["pytest_collected"] is not None:
            parts.append("  pytest 实际收集到：%d 个测试" % res["pytest_collected"])
        else:
            parts.append("  pytest 未安装/不可用（仅做文件探测）")
    else:
        parts.append("  目录内未发现测试文件 -> 测试覆盖为 0")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# P2 新增：代码风格 / 坏味道检查 + 按 diff 行号过滤的扫描
# ---------------------------------------------------------------------------

def _func_name(node):
    """取调用节点的函数名（support foo() 与 obj.foo()）。"""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _is_requests_call(node):
    """判断调用是不是 requests.<method>。"""
    f = node.func
    return (isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "requests")


def analyze_style(code):
    """代码风格 / 坏味道检查（教学级规则集，非 bandit 全量）：

    - open() 没用 with（资源泄漏）
    - requests.get/post/... 没设 timeout（可能网络挂起卡死）
    - 用 assert 做参数/业务校验（python -O 下被移除）
    - 残留 TODO/FIXME/XXX 注释

    返回 list[dict]，每项 {line, severity, rule, message}。
    """
    findings = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return findings

    # 先标记哪些 open() 处于 with 语句内（避免误报）
    open_in_with = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call) and _func_name(ce) == "open":
                    open_in_with.add(id(ce))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _func_name(node)
            if name == "open" and id(node) not in open_in_with:
                findings.append({
                    "line": node.lineno, "severity": "low",
                    "rule": "OPEN_WITHOUT_WITH",
                    "message": "open() 建议改用 with open(...) 以自动关闭文件",
                })
            if name in ("get", "post", "put", "delete", "patch") and _is_requests_call(node):
                has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                if not has_timeout:
                    findings.append({
                        "line": node.lineno, "severity": "medium",
                        "rule": "REQUEST_NO_TIMEOUT",
                        "message": "requests.%s 未设置 timeout，可能因网络挂起而卡死" % name,
                    })
        if isinstance(node, ast.Assert):
            findings.append({
                "line": node.lineno, "severity": "low",
                "rule": "ASSERT_CONTROL_FLOW",
                "message": "用 assert 做参数/业务校验会在 python -O 下被移除，建议改 raise",
            })

    for i, line in enumerate(code.splitlines(), 1):
        s = line.strip()
        if s.startswith("#") and re.search(r"(?i)\b(TODO|FIXME|XXX)\b", s):
            tag = s.split()[0] if s.split() else "TODO"
            findings.append({
                "line": i, "severity": "low",
                "rule": "TODO_MARKER",
                "message": "残留 %s 标记，建议在跟踪系统中登记后清理" % tag,
            })

    findings.sort(key=lambda f: f["line"])
    return findings


def scan_code(code):
    """对整段代码跑全部静态检查（安全 + 逻辑 + 风格），返回合并后的 findings。"""
    return analyze_security(code) + analyze_bugs(code) + analyze_style(code)


def scan_diff(diff_text, repo_root="."):
    """扫描一个 git diff：只针对改动的 .py 文件、只报『本次新增/修改行』上的问题。

    关键：用 diff 里的 '+' 行号（新文件行号）做精确归因，避免把文件里
    历史遗留问题算到本次 PR 头上。

    返回 list[dict]：{file, line, severity, rule, message}。
    """
    parsed = parse_diff(diff_text)
    out = []
    for f in parsed:
        if not f["file"].endswith(".py"):
            continue                      # 只看 Python 文件
        fpath = os.path.join(repo_root, f["file"])
        if not os.path.isfile(fpath):
            continue
        # 收集本次改动的新文件行号（只看 + 行）
        changed = set()
        for h in f["hunks"]:
            ln = h["start"]
            for kind, _text in h["lines"]:
                if kind == "+":
                    changed.add(ln)
                if kind in (" ", "+"):
                    ln += 1             # 上下文与新增都推进新文件行号；'-' 不推进
        if not changed:
            continue
        with open(fpath, encoding="utf-8") as fh:
            code = fh.read()
        for fd in scan_code(code):
            if fd.get("line") in changed:
                item = dict(fd)
                item["file"] = f["file"]
                out.append(item)
    out.sort(key=lambda x: (x["file"], x["line"]))
    return out
