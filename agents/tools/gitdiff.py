# agents/tools/gitdiff.py
# 真实 git 集成：把"未提交的代码改动"变成可供 Review 的文本。
#
# 为什么要有这一层：真实工作流里我们审的是 PR / 提交里的 diff，
# 而不是把整个文件又读一遍。这里用 `git diff` 拿到改动，
# 再解析成"每个文件改了哪些行"，只把这些行喂给多 Agent 团队——
# 既聚焦、又省钱。解析纯手写（标准库），不依赖任何第三方库。

import os
import subprocess


def get_diff(repo_dir):
    """拿到工作区改动文本。优先未暂存 diff，空则退回已暂存 diff。"""
    def _run(args):
        try:
            out = subprocess.run(args, cwd=repo_dir, capture_output=True,
                                 text=True, timeout=30)
            return out.stdout
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return ""

    diff = _run(["git", "diff"])
    if not diff.strip():
        diff = _run(["git", "diff", "--cached"])
    return diff


def parse_diff(diff_text):
    """把 unified diff 解析成改动片段列表。

    返回 list[dict]，每项：
        {file, hunks: [ {start, lines:[ (kind, text), ... ]} ]}
    kind 取值：'+' 新增、'-' 删除、' ' 上下文。
    只保留"有改动"的文件（含 + 或 -）。
    """
    files = []
    current = None
    cur_hunk = None
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            # 例：diff --git a/foo.py b/foo.py  -> 取 b/ 路径
            parts = raw.split()
            bpath = ""
            for p in parts:
                if p.startswith("b/"):
                    bpath = p[2:]
            current = {"file": bpath, "hunks": []}
            files.append(current)
            cur_hunk = None
        elif raw.startswith("@@") and current is not None:
            # 例：@@ -1,7 +1,8 @@
            cur_hunk = {"start": _hunk_start(raw), "lines": []}
            current["hunks"].append(cur_hunk)
        elif cur_hunk is not None:
            if raw.startswith("+"):
                cur_hunk["lines"].append(("+", raw[1:]))
            elif raw.startswith("-"):
                cur_hunk["lines"].append(("-", raw[1:]))
            elif raw.startswith(" "):
                cur_hunk["lines"].append((" ", raw[1:]))
            elif raw == "\\ No newline at end of file":
                continue
            else:
                # 文件头之类，忽略
                continue

    # 只保留真正有改动的文件
    return [f for f in files if any(h["lines"] for h in f["hunks"])]


def _hunk_start(hunk_header):
    """从 @@ -a,b +c,d @@ 里取新文件起始行 c。"""
    try:
        right = hunk_header.split("+")[1].split(" ")[0]  # "c,d"
        return int(right.split(",")[0])
    except (IndexError, ValueError):
        return 0


def diff_to_review_text(diff_text):
    """把 diff 整理成适合喂给 LLM 的"改动代码"文本（带文件名与行号）。"""
    parsed = parse_diff(diff_text)
    if not parsed:
        return ""
    blocks = []
    for f in parsed:
        blocks.append("===== 文件：%s =====" % f["file"])
        for h in f["hunks"]:
            ln = h["start"]
            for kind, text in h["lines"]:
                if kind == " ":
                    blocks.append("  L%d  %s" % (ln, text))
                    ln += 1
                elif kind == "+":
                    blocks.append("+ L%d  %s" % (ln, text))  # 新增行
                    ln += 1
                else:  # 删除行
                    blocks.append("- (删除) %s" % text)
    return "\n".join(blocks)


if __name__ == "__main__":  # 仅本地调试用，不会被项目主流程触发
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = get_diff(here)
    print(diff_to_review_text(d) or "（当前没有未提交的改动）")
