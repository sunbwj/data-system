# -*- coding: utf-8 -*-
"""真实浏览器端到端校验：登录 -> 框架页 -> 逐页渲染 -> 主题切换 -> 截图。"""
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
SHOT = "E:/workbuddy-workspace/data-system/.tmp/shots"

errors, console_errors = [], []


def main():
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu"])
        ctx = br.new_context(viewport={"width": 1600, "height": 950}, locale="zh-CN")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        page.goto(BASE + "/login", wait_until="networkidle")
        print("login title:", page.title())
        page.screenshot(path=SHOT + "/00-login.png")

        page.fill("#username", "admin")
        page.fill("#password", "888888")
        page.click("#btnLogin")
        page.wait_for_function("()=>location.pathname==='/index'||location.pathname==='/'", timeout=15000)
        page.wait_for_selector("#menuBox .jd-menu__label", timeout=15000)
        page.wait_for_timeout(1200)
        print("after login url:", page.url)

        for sel in (".jd-top", "#sidebar", "#mainFrame", ".jd-down"):
            print("region %-11s -> %d" % (sel, page.locator(sel).count()))
        print("brand:", page.locator("#brandName").inner_text(),
              "| ver:", page.locator(".jd-top .jd-tag").first.inner_text())
        labels = page.locator("#menuBox .jd-menu__label")
        print("menu labels(%d):" % labels.count(),
              [labels.nth(i).inner_text().strip() for i in range(labels.count())])
        page.screenshot(path=SHOT + "/01-index.png", full_page=False)

        # ---------- 布局断言：中置折叠条 / 图标尺寸 / 旧控件已移除 ----------
        lay = page.evaluate("""()=>{
          const box=s=>{const e=document.querySelector(s);if(!e)return null;const b=e.getBoundingClientRect();
            return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height),
                    r:Math.round(b.right)};};
          const svg=s=>{const e=document.querySelector(s);if(!e)return null;const b=e.getBoundingClientRect();
            const cs=getComputedStyle(e);return {w:Math.round(b.width),h:Math.round(b.height),css:cs.width+'x'+cs.height};};
          const themeUse=document.querySelector('#btnTheme svg use');
          return {
            sidebar:box('#sidebar'), split:box('#btnCollapse'), main:box('main'),
            handle:svg('.jd-splitter__handle svg'), themeIcon:svg('#btnTheme svg'),
            themeHref:themeUse?themeUse.getAttribute('xlink:href')||themeUse.getAttribute('href'):null,
            oldFoot:document.querySelectorAll('.jd-left__foot,.jd-collapse-btn').length
          };}""")
        print("layout sidebar=%s splitter=%s main=%s" %
              (lay["sidebar"], lay["split"], lay["main"]))
        print("ASSERT 折叠条位于左/右区之间 :", bool(
            lay["sidebar"] and lay["split"] and lay["main"] and
            lay["split"]["x"] >= lay["sidebar"]["r"] - 2 and
            lay["split"]["r"] <= lay["main"]["x"] + 2))
        print("ASSERT 旧侧栏底部折叠控件已移除 :", lay["oldFoot"] == 0)
        print("ASSERT 折叠把手图标 12x12 :", lay["handle"] and lay["handle"]["w"] == 12
              and lay["handle"]["h"] == 12, lay["handle"])
        print("ASSERT 主题图标非色盘(i-theme) :",
              (lay["themeHref"] or "").endswith("#i-theme"), lay["themeHref"])
        print("ASSERT 顶栏图标 ≤ 20px（略大于正文 16px）:",
              lay["themeIcon"] and 16 <= lay["themeIcon"]["w"] <= 20, lay["themeIcon"])

        # 折叠 / 展开交互
        page.click("#btnCollapse")
        page.wait_for_timeout(500)
        collapsed = page.evaluate("()=>{const s=document.querySelector('#sidebar');"
                                  "return {cls:s.className,w:Math.round(s.getBoundingClientRect().width)};}")
        page.screenshot(path=SHOT + "/01b-collapsed.png")
        page.click("#btnCollapse")
        page.wait_for_timeout(500)
        expanded = page.evaluate("()=>Math.round(document.querySelector('#sidebar')"
                                 ".getBoundingClientRect().width)")
        print("ASSERT 折叠/展开生效 :", collapsed["w"] < expanded, collapsed, "->", expanded)

        # ---------- 主题切换 ----------
        page.click("#btnTheme")
        page.wait_for_timeout(350)
        items = page.locator(".js-theme")
        keys = [items.nth(i).get_attribute("data-key") for i in range(items.count())]
        print("theme items(%d):" % len(keys), keys)
        # 真实点击第一个主题，验证交互链路
        items.nth(1).click()
        page.wait_for_timeout(350)
        print("click theme -> html attr:", page.get_attribute("html", "data-theme"))
        for k in keys:
            page.evaluate("(k)=>JD.applyTheme(k,true)", k)
            page.wait_for_timeout(320)
            info = page.evaluate("""()=>({attr:document.documentElement.getAttribute('data-theme'),
                bg:getComputedStyle(document.body).backgroundColor,
                btn:getComputedStyle(document.querySelector('.jd-btn--primary')||document.body).backgroundColor,
                txt:getComputedStyle(document.body).color})""")
            print("theme %-7s -> html=%s bodyBg=%-20s btnBg=%-20s text=%s"
                  % (k, info["attr"], info["bg"], info["btn"], info["txt"]))
            page.screenshot(path="%s/theme-%s.png" % (SHOT, k))
        page.evaluate("()=>JD.applyTheme('blue',true)")
        page.wait_for_timeout(250)

        # ---------- 逐页打开 iframe ----------
        pages = ["dashboard", "dataset", "quality", "rule", "pipeline", "label", "search",
                 "lineage", "inbox", "chat", "mailbox",
                 "msg_template", "org", "employee", "user", "role", "menu", "menu_group",
                 "dict", "param", "log"]
        for idx, name in enumerate(pages):
            page.evaluate("(n)=>{location.hash='#/pages/'+n+'.html';}", name)
            page.wait_for_timeout(200)
            try:
                page.wait_for_function(
                    "()=>{const f=document.getElementById('mainFrame');"
                    "return f&&f.contentDocument&&f.contentDocument.readyState==='complete';}", timeout=12000)
            except Exception:
                print("PAGE %-13s IFRAME TIMEOUT" % name)
                continue
            page.wait_for_timeout(1500)
            info = page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
                d.querySelectorAll('.jd-watermark').forEach(e=>e.remove());
                const u=d.querySelector('.jd-watermark'); if(u) u.remove();
                const t=(d.body.innerText||'').replace(/\\s+/g,' ');
                const tb=d.querySelector('table tbody');
                const cells=tb?Array.from(tb.querySelectorAll('tr')).slice(0,3)
                    .map(tr=>Array.from(tr.querySelectorAll('td')).slice(0,4)
                    .map(td=>td.innerText.trim().slice(0,14)).join('/')):[];
                const ops=d.querySelector('.jd-ops');
                const opBtns=ops?ops.querySelectorAll('.jd-ops__btn'):[];
                const opIconW=opBtns.length?Math.round(opBtns[0].querySelector('svg')
                    ?opBtns[0].querySelector('svg').getBoundingClientRect().width:0):0;
                const ths=Array.from(d.querySelectorAll('table thead th')).map(x=>x.innerText.trim());
                const closeSvg=d.querySelector('.jd-dialog__hd .close svg');
                return {txt:t.slice(0,90), rows:tb?tb.querySelectorAll('tr').length:0, cells:cells,
                        ops:!!ops, opCnt:opBtns.length, opIconW:opIconW,
                        opCol:ths.indexOf('操作')>=0, ths:ths,
                        tools:d.querySelectorAll('.chat-tools .chat-tool').length,
                        closeSvg:closeSvg?Math.round(closeSvg.getBoundingClientRect().width):0,
                        split:!!d.querySelector('.jd-splitter'),
                        bad:t.indexOf('undefined')>=0||t.indexOf('NaN')>=0||t.indexOf('[object')>=0};}""")
            flag = " <<< BAD" if info["bad"] else ""
            print("PAGE %-13s rows=%-3s ops=%s(%d) opCol=%s%s"
                  % (name, info["rows"], info["ops"], info["opCnt"],
                     info["opCol"], flag))
            if info["ops"]:
                print("     ops icon=%spx  cols=%s" % (info["opIconW"], info["ths"]))
            if name == "chat":
                print("     chat tools=%d（表情/图片/文件/语音/截屏）" % info["tools"])
            print("     txt : %s" % info["txt"][:80])
            for c in info["cells"]:
                print("     row : %s" % c)
            page.screenshot(path="%s/page-%02d-%s.png" % (SHOT, idx, name), full_page=False)

        # ---------- 弹窗式提示 ----------
        page.evaluate("""()=>{const w=document.getElementById('mainFrame').contentWindow;
            w.JD.alert('保存成功','数据已写入，版本快照已生成（弹出框提示，非 info 条）','success');}""")
        page.wait_for_timeout(600)
        cls = page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
            const b=d.querySelector('.jd-dialog');return b?b.className:'NONE';}""")
        print("alert modal class:", cls)
        print("ASSERT alert 内图标 ≤ 24 :",
              page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
                return Array.from(d.querySelectorAll('.jd-dialog svg')).every(s=>
                  s.getBoundingClientRect().width<=24);}"""))
        page.screenshot(path=SHOT + "/02-alert.png")
        page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
            const b=d.querySelector('.jd-dialog [data-act=ok],.jd-dialog .jd-btn--primary'); if(b) b.click();}""")
        page.wait_for_timeout(300)

        # 带标题弹窗：校验关闭按钮与勾选图标尺寸
        page.evaluate("""()=>{const w=document.getElementById('mainFrame').contentWindow;
            w.JD.dialog({title:'数据集详情 · 版式校验', width:'jd-dialog',
              body:'<div class="jd-kv"><dt>名称</dt><dd>示例数据集</dd></div>',
              footer:'<button class="jd-btn" data-act="close">关闭</button>',
              onOpen:function($m,close){$m.on('click','[data-act=close]',close);}});}""")
        page.wait_for_timeout(600)
        dlg = page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
            const r=e=>{if(!e)return null;const b=e.getBoundingClientRect();
              return Math.round(b.width)+'x'+Math.round(b.height);};
            const c=d.querySelector('.jd-dialog__hd .close');
            return {closeBtn:r(c), closeSvg:r(c?c.querySelector('svg'):null),
                    hdSvg:Array.from(d.querySelectorAll('.jd-dialog__hd svg')).map(x=>r(x))};}""")
        print("ASSERT 弹窗关闭按钮规范（24×24 点击区 / 15×15 图标）:",
              dlg["closeBtn"] == "24x24" and dlg["closeSvg"] == "15x15", dlg)
        page.screenshot(path=SHOT + "/02b-dialog.png")
        page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
            const b=d.querySelector('.jd-dialog__hd .close'); if(b) b.click();}""")
        page.wait_for_timeout(300)

        page.evaluate("""()=>{const w=document.getElementById('mainFrame').contentWindow;
            w.JD.confirm('确认删除该版本？','删除后不可恢复，血缘引用将失效');}""")
        page.wait_for_timeout(600)
        mdl = page.evaluate("""()=>{const d=document.getElementById('mainFrame').contentDocument;
            const m=d.querySelector('.jd-dialog');return m?m.innerText.replace(/\\s+/g,' ').slice(0,60):'NONE';}""")
        print("confirm modal:", mdl)
        page.screenshot(path=SHOT + "/03-confirm.png")

        print("\n=== pageerror ===")
        for e in errors:
            print("ERR:", e[:400])
        print("=== console error ===")
        for e in console_errors[:20]:
            print("CONSOLE:", e[:200])
        br.close()


main()
