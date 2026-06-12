#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xhs_auto.py — 小红书图文笔记全自动生成 + 发布

流程：
  1. 联网搜索话题相关内容做参考素材（必应，失败则跳过）
  2. AI（豆包/Ark）生成标题、正文、话题标签、图片卡片内容（去 AI 味）
  3. HTML 模板 + Playwright 截图，生成 3:4 小红书风格封面图和内容卡
  4. Playwright 打开小红书创作者中心，自动上传图片、填入标题/正文/话题
     （首次运行需扫码登录一次，之后记住登录态）

用法：
  /usr/bin/python3 xhs_auto.py "秋冬护肤思路"
  /usr/bin/python3 xhs_auto.py "成都周末遛娃" --pages 4 --theme tea
  /usr/bin/python3 xhs_auto.py "话题" --no-publish        # 只生成不发布
  /usr/bin/python3 xhs_auto.py "话题" --yes               # 发布前不再人工确认
"""

import argparse
import datetime
import json
import os
import random
import re
import sys
import time

# ============================================================
# 1. 配置常量块
# ============================================================
# AI 接口配置：优先读环境变量，其次读脚本同目录的 config_local.py（不要提交到 git）
AI_BASE_URL   = os.getenv("XHS_AI_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding")
AI_API_KEY    = os.getenv("XHS_AI_API_KEY", "")
AI_MODEL      = os.getenv("XHS_AI_MODEL", "doubao-seed-2.0-pro")
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass

OUT_ROOT      = os.path.expanduser("~/Downloads/xhs_auto")
PROFILE_DIR   = os.path.join(OUT_ROOT, "browser_profile")   # 登录态保存在这里

XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&target=image"

IMG_W, IMG_H  = 1242, 1656          # 3:4 竖图
TITLE_MAX     = 20                  # 小红书标题上限 20 字

SEARCH_TIMEOUT  = 12
LOGIN_TIMEOUT_S = 300               # 等扫码登录最多 5 分钟

# 配色主题：bg / 卡片 / 主色 / 强调 / 正文色
THEMES = {
    "cream":  dict(bg="#FBF6EE", card="#FFFFFF", main="#C75B39", accent="#E8A87C", text="#3D3229", sub="#8C7B6B"),
    "tea":    dict(bg="#F3EDE4", card="#FFFDF9", main="#8C6244", accent="#C9A87C", text="#42362B", sub="#94836F"),
    "green":  dict(bg="#EFF5EC", card="#FFFFFF", main="#3E6B4F", accent="#9CBD8F", text="#2E3B30", sub="#7E907F"),
    "blue":   dict(bg="#EDF3F8", card="#FFFFFF", main="#2F5D8A", accent="#8FB6D9", text="#2A3540", sub="#7C8B99"),
    "pink":   dict(bg="#FBEFF0", card="#FFFFFF", main="#C04B5E", accent="#ECA7B0", text="#433235", sub="#9A8186"),
}

# ============================================================
# 2. Prompt 模板
# ============================================================
COPY_PROMPT = """你是一个小红书重度用户，自己运营着一个不错的账号。现在围绕话题「{TOPIC}」写一篇图文笔记。

{REFERENCE_BLOCK}

【写作要求 —— 必须严格遵守，目标是让人完全看不出是 AI 写的】
1. 用第一人称真实经历的口吻，像跟朋友发微信一样说话，可以有点小情绪、小吐槽
2. 加入具体的细节：具体数字、具体场景、具体的失败教训，宁可编得具体也不要写得空泛
3. 禁止出现这些 AI 腔：首先/其次/最后、总之/综上所述、值得注意的是、不仅...还...、赋能、闭环、攻略来啦、宝子们（最多出现一次）、家人们（最多一次）、排比句堆砌
4. 句子要短，多换行，一段不超过 3 行。全文带 3~6 个 emoji，放在段首或句尾点缀，别堆在一起
5. 标题不超过 {TITLE_MAX} 个字，要有钩子（数字、反差、悬念、利益点选一种），别用感叹号堆砌
6. 正文 350~550 字，结尾自然收住，可以抛个问题引导评论，但别写"你们觉得呢？"这种烂大街的
7. 话题标签 5~8 个，由热门大词 + 精准长尾词组成，不带 # 号

【图片卡片内容】
同时为这篇笔记设计 {N_PAGES} 张图的文字内容：
- 第 1 张是封面：一句大字主标题（10 字以内，可以和笔记标题不同、更夸张更抓眼）+ 一句小字副标题（16 字以内）+ 一个角标短词（2~6 字，如"亲测""避坑""第3期"）
- 其余是内容卡：每张一个小标题（8 字以内）+ 3~5 条要点，每条要点 14 字以内（超长会被截断），可以用 1 个贴合内容的 emoji 开头，干货密度要高

【输出格式】只输出 JSON，不要任何解释、不要 markdown 代码块：
{{
  "title": "笔记标题",
  "body": "正文（用\\n换行）",
  "topics": ["话题1", "话题2"],
  "pages": [
    {{"type": "cover", "line1": "封面主标题", "line2": "副标题", "badge": "角标"}},
    {{"type": "content", "heading": "卡片标题", "items": ["要点1", "要点2", "要点3"]}}
  ]
}}"""

REFERENCE_TPL = """【参考素材 —— 这是从网上搜到的相关内容，提炼里面有用的信息点融进笔记，但表达必须完全是你自己的话，不许照抄句子】
{REF}
"""

# ============================================================
# 3. HTML 模板（图片卡片，__TOKEN__ 占位替换）
# ============================================================
HTML_BASE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:__W__px; height:__H__px; overflow:hidden;
  font-family:"PingFang SC","Hiragino Sans GB",sans-serif; }
body { background:__BG__; display:flex; align-items:center; justify-content:center; }
.deco { position:absolute; border-radius:50%; opacity:.35; }
__EXTRA_CSS__
</style></head><body>__BODY__</body></html>"""

COVER_CSS = """
.wrap { width:100%; height:100%; padding:96px 88px; position:relative; overflow:hidden;
  display:flex; flex-direction:column; justify-content:center; }
.dotgrid { position:absolute; top:110px; left:88px; width:300px; height:130px; opacity:.45;
  background-image:radial-gradient(__ACCENT__ 7px, transparent 7px); background-size:48px 44px; }
.badge { position:absolute; top:96px; right:88px; background:__MAIN__; color:#fff;
  font-size:38px; font-weight:600; padding:20px 42px; letter-spacing:3px;
  border-radius:48px 48px 48px 10px; box-shadow:0 14px 34px __MAIN__44; }
.kicker { display:flex; align-items:center; color:__SUB__; font-size:40px;
  letter-spacing:8px; margin-bottom:54px; overflow:hidden; white-space:nowrap; }
.kicker::before { content:""; width:64px; height:10px; border-radius:5px;
  background:__MAIN__; margin-right:24px; flex:none; }
.line1 { font-size:__T1SIZE__px; font-weight:800; color:__TEXT__; line-height:1.24;
  letter-spacing:3px; margin-bottom:56px; max-height:2.6em; overflow:hidden; }
.line1 em { font-style:normal; color:__MAIN__;
  background:linear-gradient(transparent 70%, __ACCENT__66 70%); border-radius:4px; }
.line2box { display:inline-block; align-self:flex-start; max-width:100%; background:__CARD__;
  border:3px solid __ACCENT__; color:__SUB__; border-radius:26px; padding:28px 42px;
  font-size:46px; line-height:1.45; box-shadow:10px 10px 0 __ACCENT__55;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.foot { position:absolute; bottom:88px; left:88px; right:88px; display:flex;
  justify-content:space-between; align-items:center; color:__SUB__; font-size:36px;
  letter-spacing:2px; border-top:3px dashed __ACCENT__88; padding-top:38px; }
"""

COVER_BODY = """<div class="wrap">
<div class="deco" style="width:560px;height:560px;background:__ACCENT__;top:-200px;left:-200px;opacity:.30;"></div>
<div class="deco" style="width:420px;height:420px;background:__MAIN__;bottom:-160px;right:-140px;opacity:.14;"></div>
<div class="deco" style="width:180px;height:180px;border:14px solid __ACCENT__;background:transparent;opacity:.35;bottom:300px;right:120px;"></div>
<div class="dotgrid"></div>
<div class="badge">__BADGE__</div>
<div class="kicker">__KICKER__</div>
<div class="line1">__LINE1__</div>
<div class="line2box">__LINE2__</div>
<div class="foot"><span>持续更新中</span><span>左滑看干货 →</span></div>
</div>"""

CONTENT_CSS = """
.wrap { width:100%; height:100%; padding:72px 64px; position:relative; overflow:hidden; }
.card { width:100%; height:100%; background:__CARD__; border-radius:44px;
  padding:80px 72px; border:2px solid __ACCENT__40;
  box-shadow:0 24px 70px rgba(0,0,0,.07);
  display:flex; flex-direction:column; overflow:hidden; position:relative; }
.corner { position:absolute; top:-90px; right:-90px; width:280px; height:280px;
  border-radius:50%; background:__ACCENT__; opacity:.18; }
.head { display:flex; align-items:center; margin-bottom:30px; }
.head .num { font-size:42px; font-weight:800; color:#fff;
  background:linear-gradient(135deg,__MAIN__,__ACCENT__);
  width:92px; height:92px; border-radius:26px; display:flex; align-items:center;
  justify-content:center; margin-right:34px; flex-shrink:0;
  box-shadow:0 10px 24px __MAIN__40; }
.head .h { font-size:__HSIZE__px; font-weight:800; color:__TEXT__; line-height:1.2;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.hbar { width:150px; height:12px; border-radius:6px; margin:0 0 58px 126px;
  background:linear-gradient(90deg,__MAIN__,__ACCENT__); }
.item { background:__BG__; border-radius:30px; padding:40px 44px; margin-bottom:34px;
  display:flex; align-items:flex-start; overflow:hidden; }
.item .mark { width:20px; height:20px; border-radius:50%; background:__MAIN__;
  outline:8px solid __ACCENT__44; margin:24px 36px 0 4px; flex-shrink:0; }
.item .t { font-size:__ISIZE__px; color:__TEXT__; line-height:1.5;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.pagefoot { margin-top:auto; display:flex; justify-content:space-between; align-items:center;
  color:__SUB__; font-size:36px; padding-top:38px; border-top:3px dashed __ACCENT__88; }
.pagefoot .pg b { color:__MAIN__; font-size:42px; }
"""

CONTENT_BODY = """<div class="wrap">
<div class="card">
<div class="corner"></div>
<div class="head"><div class="num">__NUM__</div><div class="h">__HEADING__</div></div>
<div class="hbar"></div>
__ITEMS__
<div class="pagefoot"><span>__TOPIC__</span><span class="pg"><b>__PAGE__</b> / __TOTAL__</span></div>
</div></div>"""

# ============================================================
# 4. 工具函数
# ============================================================
def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def pause_for_user(msg, fallback_wait=20):
    """终端等用户回车；非交互环境（stdin 不可用）则等待固定秒数后继续。"""
    try:
        input(msg)
    except (EOFError, OSError):
        log(f"（非交互模式，{fallback_wait} 秒后自动继续）")
        time.sleep(fallback_wait)

def strip_tags(html):
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&[a-zA-Z]+;", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def parse_ai_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("AI 输出中找不到 JSON")
    return json.loads(text[start:end + 1])

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def fill(tpl, mapping):
    for k, v in mapping.items():
        tpl = tpl.replace(f"__{k}__", str(v))
    return tpl

def clip(s, n):
    """超长截断加省略号，防止文字溢出卡片。"""
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n - 1] + "…"

# ============================================================
# 5. Step 函数
# ============================================================
def search_reference(topic):
    """必应搜索话题，抓取摘要 + 头部网页正文做参考素材。失败返回空串。"""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    chunks = []
    try:
        log(f"搜索参考素材：{topic}")
        r = requests.get("https://www.bing.com/search",
                         params={"q": topic, "setlang": "zh-hans"},
                         headers=headers, timeout=SEARCH_TIMEOUT)
        blocks = re.findall(r'(?s)<li[^>]*class="b_algo[^"]*".*?</li>', r.text)
        links = []
        for b in blocks[:8]:
            m = re.search(r'(?s)<h2[^>]*><a[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>', b)
            snippet = strip_tags(re.sub(r"(?s)<h2>.*?</h2>", "", b))[:200]
            if m:
                title = strip_tags(m.group(2))
                links.append(m.group(1))
                chunks.append(f"- {title}：{snippet}")
        # 抓前 2 个网页的正文片段
        for url in links[:2]:
            try:
                pr = requests.get(url, headers=headers, timeout=SEARCH_TIMEOUT)
                pr.encoding = pr.apparent_encoding or "utf-8"
                body = strip_tags(pr.text)
                if len(body) > 300:
                    chunks.append(f"- 网页正文摘录：{body[:1200]}")
            except Exception:
                continue
    except Exception as e:
        log(f"搜索失败（不影响后续，AI 将凭自身知识写作）：{e}")
    ref = "\n".join(chunks)
    if ref:
        log(f"拿到参考素材 {len(ref)} 字")
    return ref[:4000]

def ai_generate_copy(topic, reference, n_pages):
    """调豆包生成标题/正文/话题/卡片内容。"""
    import anthropic
    if not AI_API_KEY:
        sys.exit('缺少 AI API Key：请设置环境变量 XHS_AI_API_KEY，'
                 '或在脚本同目录创建 config_local.py 写入 AI_API_KEY = "你的key"')
    log(f"AI 生成文案中（模型 {AI_MODEL}）…")
    ref_block = REFERENCE_TPL.format(REF=reference) if reference else ""
    prompt = COPY_PROMPT.format(TOPIC=topic, REFERENCE_BLOCK=ref_block,
                                TITLE_MAX=TITLE_MAX, N_PAGES=n_pages)
    client = anthropic.Anthropic(api_key=AI_API_KEY, base_url=AI_BASE_URL)
    msg = client.messages.create(model=AI_MODEL, max_tokens=8192,
                                 messages=[{"role": "user", "content": prompt}])
    data = parse_ai_json(msg.content[0].text)
    # 兜底校验
    data["title"] = str(data.get("title", topic))[:TITLE_MAX]
    data["topics"] = [t.strip().lstrip("#") for t in data.get("topics", []) if t.strip()][:8]
    pages = data.get("pages") or []
    if not pages or pages[0].get("type") != "cover":
        pages.insert(0, {"type": "cover", "line1": data["title"][:10],
                         "line2": topic, "badge": "干货"})
    data["pages"] = pages[:n_pages]
    log(f"文案完成：《{data['title']}》 正文 {len(data['body'])} 字，"
        f"{len(data['topics'])} 个话题，{len(data['pages'])} 张图")
    return data

def render_images(data, topic, theme_name, out_dir):
    """HTML 模板渲染成 3:4 PNG。返回图片路径列表。"""
    from playwright.sync_api import sync_playwright
    theme = THEMES[theme_name]
    pages = data["pages"]
    total = len(pages)
    paths = []
    log(f"生成图片（主题 {theme_name}，共 {total} 张）…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page(viewport={"width": IMG_W, "height": IMG_H})
        for i, card in enumerate(pages):
            common = dict(W=IMG_W, H=IMG_H, BG=theme["bg"], CARD=theme["card"],
                          MAIN=theme["main"], ACCENT=theme["accent"],
                          TEXT=theme["text"], SUB=theme["sub"])
            if card.get("type") == "cover":
                raw1 = clip(card.get("line1", data["title"]), 12)
                # 主标题按字数自动缩字号，避免溢出
                n = len(raw1)
                t1size = 150 if n <= 6 else 132 if n <= 8 else 112 if n <= 10 else 98
                line1 = esc(raw1)
                if n > 4:                       # 后半句加高亮色
                    cut = n // 2
                    line1 = f"{esc(raw1[:cut])}<em>{esc(raw1[cut:])}</em>"
                body_html = fill(COVER_BODY, dict(common,
                                 BADGE=esc(clip(card.get("badge", "干货"), 6)),
                                 KICKER=esc(clip(topic, 14)),
                                 LINE1=line1,
                                 LINE2=esc(clip(card.get("line2", topic), 18))))
                css = fill(COVER_CSS, dict(common, T1SIZE=t1size))
            else:
                raw_items = [clip(it, 26) for it in card.get("items", [])[:5]]
                isize = 52 if all(len(it) <= 16 for it in raw_items) else 46
                items = "".join(
                    f'<div class="item"><div class="mark"></div><div class="t">{esc(it)}</div></div>'
                    for it in raw_items)
                heading = clip(card.get("heading", ""), 12)
                hsize = 74 if len(heading) <= 8 else 62
                body_html = fill(CONTENT_BODY, dict(common,
                                 NUM=f"{i:02d}", HEADING=esc(heading),
                                 ITEMS=items, TOPIC=esc(clip(topic, 14)),
                                 PAGE=i + 1, TOTAL=total))
                css = fill(CONTENT_CSS, dict(common, HSIZE=hsize, ISIZE=isize))
            html = fill(HTML_BASE, dict(common, EXTRA_CSS=css, BODY=body_html))
            pg.set_content(html)
            pg.wait_for_timeout(150)
            path = os.path.join(out_dir, f"{i+1:02d}_{'cover' if card.get('type')=='cover' else 'card'}.png")
            pg.screenshot(path=path)
            paths.append(path)
            log(f"  生成 {os.path.basename(path)}")
        browser.close()
    return paths

def save_copy_text(data, out_dir):
    """文案落盘，自动化失败时也能手动复制粘贴。"""
    txt = (f"【标题】\n{data['title']}\n\n【正文】\n{data['body']}\n\n"
           f"【话题】\n" + " ".join("#" + t for t in data["topics"]) + "\n")
    path = os.path.join(out_dir, "文案.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"文案已保存：{path}")

def _cursor_to_end(page):
    """把光标强制移到编辑器全文末尾，防止话题标签插进正文中间。"""
    for combo in ("Meta+ArrowDown", "Control+End"):
        try:
            page.keyboard.press(combo)
        except Exception:
            pass

def _first_visible(page, selectors, timeout_each=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_each)
            return loc
        except Exception:
            continue
    return None

def _clean_profile_locks():
    """清掉上次浏览器异常退出残留的单实例锁文件，否则 Chrome 拒绝启动。"""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = os.path.join(PROFILE_DIR, name)
        try:
            if os.path.lexists(path):
                os.remove(path)
        except OSError:
            pass

def _launch_browser(p):
    """带锁清理和重试的浏览器启动：优先系统 Chrome，失败退回 Chromium。"""
    kwargs = dict(headless=False, viewport={"width": 1440, "height": 900},
                  args=["--disable-blink-features=AutomationControlled"])
    last_err = None
    for channel in ("chrome", None):
        _clean_profile_locks()
        try:
            if channel:
                return p.chromium.launch_persistent_context(PROFILE_DIR, channel=channel, **kwargs)
            return p.chromium.launch_persistent_context(PROFILE_DIR, **kwargs)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"浏览器启动失败（如果有上次没关的自动化浏览器窗口，请先关掉再试）：{last_err}")

def publish_to_xhs(data, image_paths, out_dir, auto_yes=False):
    """打开创作者中心，上传图片并自动填入标题/正文/话题。"""
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    log("启动浏览器，打开小红书创作者中心…")
    with sync_playwright() as p:
        ctx = _launch_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(XHS_PUBLISH_URL, wait_until="domcontentloaded")

        # ---- 等登录：未登录会跳到 login 页 ----
        deadline = time.time() + LOGIN_TIMEOUT_S
        warned = False
        while time.time() < deadline:
            if "login" in page.url or page.locator("text=扫码登录").count() > 0:
                if not warned:
                    log(">>> 请在弹出的浏览器里用小红书 App 扫码登录（只需一次，之后会记住）")
                    warned = True
                time.sleep(2)
                continue
            tab = _first_visible(page, ['div:has-text("上传图文")', 'span:has-text("上传图文")'], 4000)
            if tab:
                break
            time.sleep(2)
        else:
            raise RuntimeError("等待登录超时")
        if warned:
            log("登录成功，登录态已保存")
            page.goto(XHS_PUBLISH_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

        # ---- 切到"上传图文"标签，并找到真正的图片上传框 ----
        # 注意：页面默认在"上传视频"，第一个 input[type=file] 是视频框（accept 全是视频格式），
        # 必须找 accept 含图片格式（或 multiple）的那个 input
        log(f"上传 {len(image_paths)} 张图片…")
        file_input = None
        deadline2 = time.time() + 30
        while time.time() < deadline2 and file_input is None:
            # JS 精确点击文本为"上传图文"的叶子元素（类名改版也不受影响）
            try:
                page.evaluate("""() => {
                    for (const el of document.querySelectorAll('div,span,a,li')) {
                        if (el.children.length === 0 && el.textContent.trim() === '上传图文') {
                            el.click(); return true;
                        }
                    }
                    return false;
                }""")
            except Exception:
                pass
            page.wait_for_timeout(1200)
            for el in page.locator('input[type="file"]').all():
                acc = (el.get_attribute("accept") or "").lower()
                if any(k in acc for k in ("image", "png", "jpg", "jpeg", "webp")) \
                        or el.get_attribute("multiple") is not None:
                    file_input = el
                    break
        if file_input is None:
            page.screenshot(path=os.path.join(out_dir, "上传入口未找到.png"))
            raise RuntimeError("没找到图片上传入口（已截图，页面可能改版）")
        file_input.set_input_files(image_paths)
        # 等上传完成：预览图数量达到 or 兜底等待
        try:
            page.wait_for_function(
                f'document.querySelectorAll(".img-container, .pr, .preview-item").length >= {len(image_paths)}',
                timeout=30000)
        except Exception:
            page.wait_for_timeout(3000 * len(image_paths))
        log("图片上传完成")

        # ---- 填标题 ----
        title_box = _first_visible(page, [
            'input[placeholder*="标题"]', 'div.d-input input', 'input.d-text'])
        if title_box:
            title_box.click()
            title_box.fill(data["title"])
            log(f"已填标题：{data['title']}")
        else:
            log("!! 没找到标题输入框，请手动粘贴（文案.txt 里有）")

        # ---- 填正文 ----
        editor = _first_visible(page, [
            'div.ql-editor', '#post-textarea', 'div[contenteditable="true"]'])
        if editor:
            editor.click()
            page.keyboard.insert_text(data["body"])
            log("已填正文")
            page.wait_for_timeout(800)        # 等编辑器处理完长文本
            # ---- 打话题标签：输入 #词 后点联想下拉，转成真正的话题 ----
            # 每次都先把光标跳到全文末尾，否则标签会插进正文中间
            _cursor_to_end(page)
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            for t in data["topics"]:
                _cursor_to_end(page)
                page.keyboard.type("#" + t, delay=50)
                page.wait_for_timeout(1500)
                sug = _first_visible(page, [
                    "#creator-editor-topic-container .item",
                    ".publish-topic-item",
                    'div[class*="topic"] .item',
                    'ul[class*="topic"] li'], 2500)
                if sug:
                    sug.click()
                    page.wait_for_timeout(400)
                    _cursor_to_end(page)      # 点完下拉焦点会跳，重新归位
                else:
                    page.keyboard.type(" ")   # 没联想就留纯文本标签
            log(f"已打 {len(data['topics'])} 个话题标签")
        else:
            log("!! 没找到正文编辑器，请手动粘贴")

        page.screenshot(path=os.path.join(out_dir, "发布前预览.png"))

        # ---- 发布 ----
        if not auto_yes:
            pause_for_user(">>> 内容已全部填好，去浏览器检查一下。回车=点击发布，Ctrl+C=取消：")
        # 必须精确匹配"发布"二字：侧边栏有"发布笔记"菜单、表单里有"定时发布"，都不能误点
        btn = None
        try:
            loc = page.get_by_role("button", name="发布", exact=True).first
            loc.wait_for(state="visible", timeout=5000)
            btn = loc
        except Exception:
            btn = _first_visible(page, [
                'button.publishBtn',
                'button:has-text("发布"):not(:has-text("笔记")):not(:has-text("定时"))'])
        if btn:
            btn.click()
            try:
                page.wait_for_selector("text=发布成功", timeout=15000)
                log("发布成功 ✅")
            except Exception:
                page.wait_for_timeout(5000)
                log("已点击发布，但没检测到'发布成功'提示，请看截图确认")
            page.screenshot(path=os.path.join(out_dir, "发布结果.png"))
        else:
            log("!! 没找到发布按钮，请在浏览器里手动点击发布")
            pause_for_user(">>> 手动发布完成后按回车关闭浏览器：", fallback_wait=60)
        ctx.close()

# ============================================================
# 6. 主流程
# ============================================================
def run_repost(out_dir, auto_yes=False):
    """直接发布之前生成好的目录（data.json + png），不重新生成。"""
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    data_path = os.path.join(out_dir, "data.json")
    if not os.path.isfile(data_path):
        sys.exit(f"目录里没有 data.json：{out_dir}")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    # 只认 01_cover.png / 02_card.png 这类编号图，排除调试截图
    image_paths = sorted(
        os.path.join(out_dir, fn) for fn in os.listdir(out_dir)
        if re.match(r"^\d{2}_.+\.png$", fn))
    if not image_paths:
        sys.exit(f"目录里没有图片：{out_dir}")
    log(f"重新发布：《{data['title']}》，{len(image_paths)} 张图")
    publish_to_xhs(data, image_paths, out_dir, auto_yes=auto_yes)


def run(topic, pages=4, theme=None, research=True, publish=True, auto_yes=False):
    theme = theme or random.choice(list(THEMES.keys()))
    stamp = datetime.datetime.now().strftime("%m%d_%H%M")
    safe_topic = re.sub(r'[\\/:*?"<>|\s]+', "_", topic)[:20]
    out_dir = os.path.join(OUT_ROOT, f"{stamp}_{safe_topic}")
    os.makedirs(out_dir, exist_ok=True)
    log(f"输出目录：{out_dir}")

    reference = search_reference(topic) if research else ""
    data = ai_generate_copy(topic, reference, pages)
    save_copy_text(data, out_dir)
    image_paths = render_images(data, topic, theme, out_dir)

    if publish:
        try:
            publish_to_xhs(data, image_paths, out_dir, auto_yes=auto_yes)
        except KeyboardInterrupt:
            log("已取消发布。图片和文案都在输出目录里，可手动发。")
        except Exception as e:
            log(f"!! 自动发布失败：{e}")
            log("图片和文案都已生成在输出目录，可手动发布。")
    log(f"完成。所有产物在：{out_dir}")
    return out_dir

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="小红书图文笔记自动生成+发布")
    ap.add_argument("topic", nargs="?", help="话题，如：秋冬护肤思路（不传则交互式询问）")
    ap.add_argument("--pages", type=int, default=4, help="图片张数（含封面），默认 4")
    ap.add_argument("--theme", choices=list(THEMES.keys()), help="配色主题，默认随机")
    ap.add_argument("--no-research", action="store_true", help="跳过联网搜参考")
    ap.add_argument("--no-publish", action="store_true", help="只生成图片文案，不发布")
    ap.add_argument("--yes", action="store_true", help="发布前不再人工确认")
    ap.add_argument("--repost", metavar="DIR", help="跳过生成，直接发布之前生成的输出目录")
    a = ap.parse_args()
    if a.repost:
        run_repost(a.repost, auto_yes=a.yes)
        sys.exit(0)
    while not a.topic or not a.topic.strip():
        try:
            a.topic = input("请输入话题（如：秋冬护肤思路）：").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("未输入话题，退出。")
    run(a.topic, pages=max(2, min(a.pages, 9)), theme=a.theme,
        research=not a.no_research, publish=not a.no_publish, auto_yes=a.yes)
