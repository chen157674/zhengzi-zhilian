"""在项目临时目录内使用现有 Playwright/Chromium 验证，避免新装浏览器。"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
TMP=ROOT/".tmp"
TMP.mkdir(exist_ok=True)
os.environ["TEMP"]=os.environ["TMP"]=str(TMP)
BROWSER=Path(r"E:\07_Projects\07_MonetizationTests\02_KnowledgeVideos\open-source\huashu-art-motion\.runtime\playwright\chromium_headless_shell-1243\chrome-headless-shell-win64\chrome-headless-shell.exe")


def run():
    with sync_playwright() as pw:
        context=pw.chromium.launch_persistent_context(str(TMP/"browser"),executable_path=str(BROWSER),headless=True,
            viewport={"width":1440,"height":1080},args=["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"],
            env={**os.environ,"TEMP":str(TMP),"TMP":str(TMP)})
        page=context.pages[0]
        errors=[]
        page.on("pageerror",lambda err:errors.append(str(err)))
        page.on("console",lambda msg:print("BROWSER",msg.type,msg.text[:300]) if msg.type in {"error","warning"} else None)
        page.goto("http://127.0.0.1:8766")
        page.wait_for_selector("#historyList")
        assert page.locator("#start").is_disabled()
        page.screenshot(path=str(TMP/"desktop.png"),full_page=True)
        page.locator("#camera").click()
        expect(page.locator('#modelState')).to_contain_text('已就绪',timeout=65000)
        print("MODEL",page.locator("#modelState").inner_text(),page.locator("#feedbackText").inner_text())
        assert page.locator("#retryModel").is_hidden(),page.locator("#feedbackText").inner_text()
        page.locator("#start").click()
        expect(page.locator('#countdown')).to_be_hidden(timeout=15000)
        expect(page.locator('#pause')).to_be_enabled(timeout=15000)
        page.wait_for_timeout(2000)
        assert page.locator("#metric").inner_text()=="0","Fake camera color bars must not count"
        page.locator("#pause").click()
        frozen=page.locator("#duration").inner_text()
        page.wait_for_timeout(1200)
        assert frozen==page.locator("#duration").inner_text()
        page.locator("#stop").click()
        expect(page.locator('#saveState')).to_contain_text('已保存',timeout=12000)
        page.locator("#refresh").click()
        page.wait_for_selector(".history-row")
        assert page.locator("#video").evaluate("v=>v.srcObject===null")
        reference=ROOT/'web/demo.mp4'
        if reference.exists():
            page.locator('#demo').click()
            expect(page.locator('#start')).to_be_enabled(timeout=25000)
            page.locator('#start').click()
            expect(page.locator('#pause')).to_be_enabled(timeout=12000)
            page.wait_for_timeout(12000)
            print('VIDEO',page.locator('#observed').inner_text(),page.locator('#metric').inner_text(),page.locator('#feedbackText').inner_text())
            page.screenshot(path=str(TMP/'video.png'),full_page=True)
            assert page.locator('#observed').inner_text()!='00:00','Actual video must produce usable pose observations'
            page.route('**/api/sessions/*',lambda route:route.abort() if route.request.method=='PUT' else route.continue_())
            page.locator('#stop').click()
            expect(page.locator('#retrySave')).to_be_visible(timeout=12000)
            assert page.locator('#start').is_disabled()
            page.unroute('**/api/sessions/*')
            page.locator('#retrySave').click()
            expect(page.locator('#saveState')).to_contain_text('已保存',timeout=12000)
        plank=TMP/'plank-reference.mp4'
        if plank.exists():
            page.locator('input[value=plank]').check()
            page.locator('#file').set_input_files(str(plank))
            expect(page.locator('#start')).to_be_enabled(timeout=25000)
            page.locator('#start').click()
            expect(page.locator('#pause')).to_be_enabled(timeout=12000)
            page.wait_for_timeout(9000)
            print('PLANK',page.locator('#metric').inner_text(),page.locator('#feedbackText').inner_text())
            page.screenshot(path=str(TMP/'plank.png'),full_page=True)
            assert int(page.locator('#metric').inner_text())>0,'Actual plank video must accumulate visible hold time'
            page.locator('#stop').click()
            expect(page.locator('#saveState')).to_contain_text('已保存',timeout=12000)
        page.set_viewport_size({"width":600,"height":1000})
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.screenshot(path=str(TMP/"narrow.png"),full_page=True)
        print("PAGE_ERRORS",json.dumps(errors))
        assert not errors
        context.close()


if __name__=="__main__":run()
