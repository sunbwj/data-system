# -*- coding: utf-8 -*-
"""前端脚本语法体检：jd.js / main.html / index.html / pages/*.html 的初始化脚本。

页面模板的 `{% block init %}` 会被 layout.html 包进
`JD.init({...}).then(function () { ... });`，所以这里用同样的包装再交给
`node --check` 解析。少一个括号这类问题会让整页脚本静默失效（页面看着在，
表格永远是空的），必须在提测前拦住。
"""
import glob
import os
import re
import shutil
import subprocess
import sys

TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   ".tmp", "_jscheck.js")
bad = []


def check(name, code):
    with open(TMP, "w", encoding="utf-8") as f:
        f.write(code)
    r = subprocess.run(["node", "--check", TMP], capture_output=True, text=True)
    if r.returncode:
        bad.append(name)
        lines = [ln for ln in r.stderr.strip().splitlines() if ln.strip()][:4]
        print("SYNTAX %s\n   %s" % (name, "\n   ".join(lines)))


def main():
    if not shutil.which("node"):
        print("未找到 node，跳过前端脚本检查")
        return 0

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.dirname(TMP), exist_ok=True)

    # 1) 独立 JS 文件
    for p in glob.glob(os.path.join(root, "app", "static", "js", "*.js")):
        check(os.path.relpath(p, root), open(p, encoding="utf-8").read())

    # 2) 页面初始化块：按 layout 的包装方式解析
    for p in sorted(glob.glob(os.path.join(root, "app", "templates", "pages", "*.html"))):
        src = open(p, encoding="utf-8").read()
        m = re.search(r"{%\s*block init\s*%}(.*?){%\s*endblock\s*%}", src, re.S)
        if not m:
            continue
        check(os.path.relpath(p, root),
              "(function () {\n" + m.group(1) + "\n});\n")

    # 3) main / index 的独立 script（含 Jinja 变量，先替换成占位符）
    for name in ("main.html", "index.html"):
        p = os.path.join(root, "app", "templates", name)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        for i, block in enumerate(re.findall(
                r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)):
            block = re.sub(r"{{.*?}}", "null", block, flags=re.S)
            block = re.sub(r"{%.*?%}", "", block, flags=re.S)
            if block.strip():
                check("%s#script%d" % (name, i), block)

    print("前端脚本检查：%d 个文件，问题 %d 处" % (
        len(glob.glob(os.path.join(root, "app", "static", "js", "*.js"))) +
        len(glob.glob(os.path.join(root, "app", "templates", "pages", "*.html"))) + 2,
        len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
