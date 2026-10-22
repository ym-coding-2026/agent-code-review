# tests_tools.py
# 确定性自测：不花一分钱、不需要 API Key，专门验证"静态工具层"
# 能稳定地把示例代码里埋的 4 个已知问题找出来。
#
# 这是 P1 的"真值校验"——LLM 层是非确定的，但静态层是确定的。
# 把它当作评测的 ground truth：只要静态层能命中，专家就有事实依据可讲。
#
# 用法：python tests_tools.py   （退出码 0 = 全过，非 0 = 有失败）

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from agents.tools.static import analyze_security, analyze_bugs, analyze_tests
from agents.planner import recommend_experts

SAMPLE = os.path.join(HERE, "samples", "bad_example.py")


def load():
    with open(SAMPLE, encoding="utf-8") as f:
        return f.read()


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (mark, name, ("  -> " + detail) if detail else ""))
    return cond


def run():
    code = load()
    ok = True

    # 1) 安全：硬编码密钥
    sec = analyze_security(code)
    has_secret = any(f["rule"] == "HARDCODED_SECRET" for f in sec)
    ok &= check("SEC-1 硬编码密钥能被静态扫描命中",
                has_secret, "命中行：" + str([f["line"] for f in sec if f["rule"] == "HARDCODED_SECRET"]))

    # 2) 安全：SQL 注入
    has_sql = any(f["rule"] == "SQL_INJECTION" for f in sec)
    ok &= check("SEC-2 SQL 注入能被静态扫描命中",
                has_sql, "命中行：" + str([f["line"] for f in sec if f["rule"] == "SQL_INJECTION"]))

    # 3) 逻辑：除零
    bugs = analyze_bugs(code)
    has_div = any(f["rule"] == "DIVISION_BY_ZERO" for f in bugs)
    ok &= check("BUG-1 除零风险能被静态扫描命中",
                has_div, "命中行：" + str([f["line"] for f in bugs if f["rule"] == "DIVISION_BY_ZERO"]))

    # 4) 测试：缺测试
    tst = analyze_tests(code, SAMPLE)
    no_tests = ("未发现测试文件" in tst["message"]) or (not tst["test_files"])
    ok &= check("TEST-1 能识别『缺少测试文件』", no_tests, tst["message"])

    # 5) 规划：示例代码应建议派安全专家
    rec = recommend_experts(code, SAMPLE)["recommend"]
    ok &= check("预分析建议派遣『安全审计』", rec.get("security", False), str(rec))

    print("\n结果：" + ("全部通过 ✅" if ok else "存在失败 ❌"))
    sys.exit(0 if ok else 1)


run()
