# -*- coding: utf-8 -*-
"""布局尺寸采样：定位侧栏/折叠按钮/趋势图的实际盒模型。"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"

with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu"])
    ctx = br.new_context(viewport={"width": 1600, "height": 950}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("#username", "admin")
    page.fill("#password", "888888")
    page.click("#btnLogin")
    page.wait_for_selector("#menuBox .jd-menu__label", timeout=15000)
    page.wait_for_timeout(1200)

    info = page.evaluate("""()=>{
      const r = s => { const e=document.querySelector(s); if(!e) return null;
        const b=e.getBoundingClientRect(); const cs=getComputedStyle(e);
        return {w:Math.round(b.width), h:Math.round(b.height), x:Math.round(b.x),
                y:Math.round(b.y), text:(e.innerText||'').replace(/\\s+/g,' ').slice(0,20),
                ws:cs.whiteSpace}; };
      return {sidebar:r('#sidebar'), foot:r('.jd-left__foot'), btn:r('#btnCollapse'),
              scroll:r('#menuBox'), down:r('.jd-down'), top:r('.jd-top'),
              sidebarW:getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w')};}""")
    for k, v in info.items():
        print("%-10s %s" % (k, v))

    # 折叠后再测
    page.click("#btnCollapse")
    page.wait_for_timeout(400)
    print("collapsed btn:", page.evaluate("""()=>{const e=document.querySelector('#btnCollapse');
        const b=e.getBoundingClientRect();return {w:Math.round(b.width),t:e.innerText.trim()};}"""))
    page.screenshot(path="E:/workbuddy-workspace/data-system/.tmp/shots/layout-collapsed.png")
    page.click("#btnCollapse")
    page.wait_for_timeout(400)
    br.close()
