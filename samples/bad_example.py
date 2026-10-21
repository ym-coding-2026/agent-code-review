# samples/bad_example.py
# 这是一段"故意带问题"的示例代码，用于演示和评测。
# 注意：它只会被 main.py / eval.py 当作【文本】读取（open），不会被 import 执行。
#
# 里面埋了 4 个已知问题（与 eval/known_issues.json 一一对应）：
#   1. 硬编码数据库密码            DB_PWD = "123456"
#   2. SQL 字符串拼接 -> 注入风险   "SELECT * FROM users WHERE ... % (user, pwd)"
#   3. divide 未处理 b=0          return a / b
#   4. 缺少单元测试
#
# 这些问题是多 Agent 团队（以及评测集）要能揪出来的目标。

import os

DB_PWD = "123456"          # 问题1：密码硬编码在源码里


def login(user, pwd):
    # 问题2：SQL 拼接，有注入风险
    sql = "SELECT * FROM users WHERE name='%s' AND password='%s'" % (user, pwd)
    return sql             # 这里简化，真实会执行 sql


def divide(a, b):
    return a / b           # 问题3：没处理 b=0，会抛 ZeroDivisionError


def double_list(items):
    result = []
    for i in range(len(items)):
        result.append(items[i] * 2)
    return result
