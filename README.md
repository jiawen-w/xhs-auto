# xhs-auto 小红书图文笔记自动生成 + 发布

输入一个话题，自动完成整条小红书图文笔记的生产链路：

```
话题 → 联网搜参考素材 → AI 生成文案（去 AI 味）→ 生成 3:4 图片卡片 → 自动上传发布
```

- **联网参考**：自动抓取必应搜索结果和头部网页正文，作为 AI 写作素材（失败自动跳过）
- **去 AI 味文案**：标题（≤20 字带钩子）、正文（350~550 字）、5~8 个话题标签；Prompt 内置反 AI 腔规则（禁套话、要具体细节、短句多换行）
- **图片生成**：HTML 模板 + Playwright 截图，封面图 + 内容卡，5 套配色主题，1242×1656（3:4）
- **自动发布**：Playwright 驱动浏览器打开小红书创作者中心，自动上传图片、填标题、正文、逐个打话题标签；首次扫码登录后记住登录态

## 安装

```bash
pip install -r requirements.txt
playwright install chromium   # 如果本机没有 Chrome
```

## 配置 AI 接口

脚本默认走火山方舟（豆包）的 Anthropic 兼容接口，任选一种方式配置：

**方式一：环境变量**

```bash
export XHS_AI_API_KEY="你的key"
export XHS_AI_BASE_URL="https://ark.cn-beijing.volces.com/api/coding"   # 可选
export XHS_AI_MODEL="doubao-seed-2.0-pro"                               # 可选
```

**方式二：本地配置文件**（脚本同目录新建 `config_local.py`，已被 .gitignore 排除）

```python
AI_API_KEY  = "你的key"
AI_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding"
AI_MODEL    = "doubao-seed-2.0-pro"
```

任何兼容 Anthropic Messages API 的服务都可以用（包括 Claude 官方 API，把 `AI_BASE_URL` 留空逻辑改为官方地址、`AI_MODEL` 换成对应模型即可）。

## 用法

```bash
# 交互式：运行后输入话题
python xhs_auto.py

# 直接传话题
python xhs_auto.py "秋冬护肤思路"

# 常用参数
python xhs_auto.py "话题" --pages 5          # 图片张数（含封面），默认 4
python xhs_auto.py "话题" --theme cream      # 配色：cream/tea/green/blue/pink，默认随机
python xhs_auto.py "话题" --no-publish       # 只生成图片和文案，不发布
python xhs_auto.py "话题" --yes              # 发布前不再人工确认
python xhs_auto.py --repost <输出目录>       # 重新发布之前生成过的笔记
```

所有产物输出到 `~/Downloads/xhs_auto/<时间_话题>/`：编号图片、`文案.txt`、`data.json`、发布过程截图。即使自动发布失败，图和文案也都在，可手动发布。

## 发布流程说明

- 首次发布会弹出浏览器，需要用小红书 App **扫码登录一次**，登录态保存在 `~/Downloads/xhs_auto/browser_profile/`
- 默认填完所有内容后暂停，等你在浏览器里检查、回车确认才点发布；`--yes` 跳过确认
- 话题标签会逐个输入 `#词` 并点击联想下拉，转成真正的话题标签

## 免责声明

- 本项目仅供学习交流，使用页面自动化模拟人工操作，**请遵守小红书社区规范与服务条款**
- 建议控制发布频率（一天一两篇以内），批量高频发布可能触发平台风控，风险自负
- AI 生成内容请人工审核后再发布，对发布内容负责的始终是账号持有者

## License

MIT
