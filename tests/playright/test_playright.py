from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # headless=False 表示显示浏览器界面，方便你直观看到效果
    browser = p.chromium.launch(headless=False)  
    page = browser.new_page()
    page.goto("https://example.com")
    
    # 打印网页标题
    print("页面标题:", page.title())
    
    # 保存一张全页截图
    page.screenshot(path="example.png", full_page=True)
    browser.close()
