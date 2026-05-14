# Clipy 来源标题与内容处理说明

这份文档记录 Clipy 现在如何为每个链接生成标题，以及后续加入更多社交媒体、正文抓取、跟帖保存、音视频理解时应该怎么扩展。

## 产品目标

Clipy 的目标不是做批量爬虫，而是做一个低摩擦的个人收藏和知识上下文入口。用户主动提供自己本来就能访问的一条链接或一个文件，Clipy 负责把标题、正文、图片和本地知识库文件保存好，让后续个人 AI 助理可以基于这些用户选择过的材料进行理解、分析和定制建议。

因此内容读取流程遵守几个约束：

- 用户主动添加，一次处理一个链接或一组本地文件。
- 默认只读取用户可访问的页面内容；遇到登录墙或安全验证时，不伪装成功，而是提示用户在 Clipy 管理的浏览器 profile 里登录或验证。
- 保存结果面向两个用途：`content.md` 给 LLM 理解，`snapshot.html` 给人离线查看。

## 当前处理分层

Clipy 现在把“添加收藏”分成两层处理。2026-05-10 起，纯链接的后台处理优先走 Clipy 自己维护的浏览器 profile，而不是先走普通 HTTP metadata。这样后续可以在同一个 profile 里保留用户登录态，尽量按“用户自己能访问的一条内容”来保存。

1. 前端来源处理器：`sources/*.js`
   - 负责识别链接属于哪个来源。
   - 负责从用户复制进来的分享文本里提取标题。
   - 负责决定没有标题时显示什么兜底文案。
   - 预留了 `crawl()` 方法，后续可以放每个平台的抓取、清洗、知识库转换逻辑。

2. 本地后端：`server.py`
   - 负责把收藏保存到本地磁盘。
   - 负责只有链接、没有分享文本时，先调用 Clipy 浏览器 profile 读取页面标题、正文、图片和 HTML。
   - 如果浏览器 profile 无法启动，才降级到旧的 HTTP metadata/平台接口逻辑，保证本地 app 仍可用。
   - 负责把已有旧数据重新识别来源并补标题。
   - 负责调用 `content_pipeline.py`，把能抓到的帖子、文章正文和图片保存成本地 Markdown。

3. 浏览器 profile 验证层：`browser_profile_fetch.js` + `browser_profile.py`
   - `browser_profile_fetch.js` 会启动或复用一个带 DevTools 端口的 Chrome。
   - 用户状态保存在 `clipy_library/browser_profile/`。
   - 当前默认用 headless Chrome 做后台读取；后续如果某个来源需要登录，再打开同一个 profile 的可见浏览器窗口让用户登录。
   - 读取结果包括 `document.title`、`og:title`、正文文本、页面 HTML、图片 URL、作者、发布时间、最终 URL。
   - 后台会把简要结果写进 entry 的 `browserCapture` 字段，便于调试和后续重抓取。

4. 内容保存管线：`content_pipeline.py`
   - 视频源先跳过，后续接入字幕/转录流程。
   - X、微博走帖子级接口，尽量保存正文、作者、发布时间和图片。
   - 公众号、知乎、普通网页走文章正文抽取。
   - 小红书、大众点评会先保存复制分享文本；如果短链页面能打开，也会尝试抽取正文和图片。
   - 这是链接正文抓取层，保留旧版中间产物：`clipy_library/markdown/` 和 `clipy_library/markdown_assets/`。

5. 知识库输出层：`item_pipeline.py`
   - 负责把每个 entry 统一整理成 `clipy_library/items/<entry-id>/`。
   - 负责把链接正文、图片资源、本地文件解析文本写成 LLM 可读的 `content.md`。
   - 负责生成 `metadata.json`，给后续知识库导入、向量化、重抓取、状态检查使用。
   - 负责生成 `snapshot.html`，给人离线打开阅读。
   - 新增或改名 entry 时会自动重建对应 item；删除 entry 时会删除对应 item。

## 标题生成优先级

每个链接的标题按这个顺序决定：

1. 复制分享文本里的标题
   - 例如小红书、大众点评经常复制出来就是“文案 + 短链接”。
   - Clipy 会先去掉链接，再取前面可读的文本作为标题。
   - 所以小红书和大众点评看起来更容易拿到原文标题，主要是因为它们的分享格式本身带了标题或文案。

2. Clipy 浏览器 profile
   - 后端用 `clipy_library/browser_profile/` 里的独立 Chrome profile 打开链接。
   - 优先读取 `og:title`、`twitter:title`、`document.title`，同时保存页面正文、图片和 HTML。
   - 如果页面要求登录，entry 会显示“需要在 Clipy 浏览器登录来源”，后续用同一个 profile 登录后再继续读取。

3. 平台专用接口或规则
   - 当前只作为浏览器 profile 无法启动时的兼容降级。
   - B站：用 BV 号请求 B站公开视频接口，拿 `title`。
   - X：如果是 `/status/` 链接，尝试用公开 tweet syndication/fxtwitter 接口拿用户和正文前几个字。
   - 微博：尝试用微博公开状态接口拿发帖用户和正文前几个字。

4. 通用网页 metadata
   - 读取页面 HTML。
   - 依次尝试 `og:title`、`twitter:title`、`itemprop=name`、`<title>`。
   - 会清理常见后缀，比如 `- YouTube`、`| 微信公众平台`、B站后缀等。

5. URL slug / 来源兜底标题
   - 对 Bloomberg 这类文章链接，会尝试从最后一段 URL slug 生成可读标题。
   - 例如 `YouTube 视频`、`公众号文章`、`@用户名 的帖子`、`网页 / 路径`。
   - 这是最后兜底，不代表已经成功抓到了内容。

## 当前来源矩阵

| 来源 | 前端处理文件 | 当前标题方法 | 可靠性 | 后续可扩展内容 |
| --- | --- | --- | --- | --- |
| 小红书 | `sources/xiaohongshu.js` | 优先用复制分享文本；否则显示笔记 ID 或兜底 | 分享文本可靠，纯链接受平台限制 | 笔记正文、图片描述、评论、作者、发布时间 |
| 大众点评 | `sources/dianping.js` | 优先用复制分享文本；否则用短链或路径兜底 | 分享文本可靠，短链需要后续解析 | 店铺/笔记正文、地点、评分、评论、图片信息 |
| 知乎 | `sources/zhihu.js` | 优先用分享文本；否则用问题/文章 ID 兜底 | 中等，纯链接可继续补网页标题 | 问题、回答、文章正文、作者、评论 |
| X | `sources/x.js` | 分享文本优先；状态链接尝试接口拿 `@用户：正文开头` | 受公开接口和访问限制影响 | 推文全文、引用、线程、回复、媒体 alt text |
| YouTube | `sources/youtube.js` | 分享文本优先；否则 oEmbed / 通用网页 metadata | 通常较好 | 字幕、章节、评论、画面关键帧 |
| B站 | `sources/bilibili.js` | 分享文本优先；否则用 BV 号请求 B站视频接口 | BV 链接较可靠 | 字幕、弹幕、评论、画面关键帧 |
| 公众号 | `sources/wechat.js` | 分享文本优先；否则读取网页 metadata | 通常较好，但可能被访问限制 | 文章正文、作者、公众号名、发布时间、图片 |
| 微博 | `sources/weibo.js` | 分享文本优先；状态链接尝试接口拿 `用户：正文开头` | 受登录墙和接口限制影响 | 微博正文、用户、图片/视频、转评赞、评论 |
| GitHub | `sources/github.js` | 仓库链接优先显示 `owner/repo：描述`；issue/PR 显示仓库和编号 | 公开仓库可靠，私有仓库需要 Clipy 浏览器 profile 登录 | README、仓库元数据、issue/PR 正文、release、代码结构摘要 |
| 文件 | `sources/file.js` | 文件名 | 可靠 | OCR、音频转录、视频转录、PDF/文档正文、图片描述 |
| 普通网页 | `sources/web.js` | 分享文本优先；否则 metadata；最后 host/path | 取决于网页开放程度 | 正文抽取、摘要、作者、发布时间 |

## GitHub 项目保存策略

GitHub 不应该只当普通网页处理。普通网页抽取会混入导航、文件列表、按钮文案和页面噪音，而用户真正想保存的是“这个项目是什么、为什么有用、后续 AI 助手应该如何理解它”。

当前 GitHub 处理逻辑：

1. 前端识别 `github.com/<owner>/<repo>` 为 `sourceKey = github`。
2. 标题优先使用复制分享文本；没有分享标题时使用浏览器/metadata；如果能通过 GitHub API 读取仓库，则使用 `owner/repo：仓库描述`。
3. 本地内容优先通过 GitHub 公开 API 保存结构化信息：
   - 仓库名、owner、描述、默认分支、主要语言、license、stars、forks、open issues、topics、创建/更新时间、homepage。
   - README 原文，直接进入 `content.md` 的 `## README`。
4. 如果用户保存的是 issue 或 PR 链接，保存 issue/PR 标题、编号、状态、作者、标签、正文和评论数。
5. 如果是私有仓库或需要登录的内容，公开 API 读不到时保留浏览器 profile 路线：用户在 Clipy 自己的浏览器 profile 登录 GitHub 后，Clipy 再按用户可访问的页面读取可见内容。

后续建议扩展：

- 对 repo 首页：加 `tree` 文件结构摘要，但限制深度和文件数量，避免把整个仓库当成批量爬取对象。
- 对 README：保留原始 Markdown，同时可额外生成一份“项目卡片”摘要：用途、安装方式、核心 API、适合场景、潜在风险。
- 对 issue/PR：后续可在用户明确点击“保存讨论”时再拉取 comments，而不是默认保存全部历史讨论。
- 对 release：保存 release note、tag、发布时间和下载资产名称。
- 对代码文件链接：只保存用户给出的单个文件内容，不递归抓仓库。

## 为什么有的平台能直接读到标题

小红书和大众点评经常能读到标题，是因为用户复制出来的文本通常已经包含“标题/文案 + 链接”。Clipy 不一定真的打开平台抓取了原文，而是先利用了分享文本。

公众号、YouTube、B站这类网页或视频页，如果纯链接里没有分享文本，Clipy 会尝试读取网页 metadata 或平台接口。B站现在有专门接口，所以 BV 视频链接更容易拿到真实标题。

X 和微博最不稳定。它们经常有登录墙、反爬策略、区域限制或接口变化。现在 Clipy 会尽量抓到“发帖用户 + 正文前几个字”，但如果用户复制的是主页链接，不是具体帖子链接，就只能显示主页标题。

## 新增一个来源时怎么做

新增来源建议按这个顺序：

1. 新建来源文件
   - 在 `sources/` 下新建一个文件，例如 `sources/douyin.js`。
   - 定义 `id`、`name`、`type`、`label`、`className`、`logoDomain`、`logoUrls`、`match()`、`buildTitle()`、`crawl()`。
   - 常用来源的 logo 优先加到 `sources/logos.js`，并把图片放进 `assets/logos/`。
   - 不在常用列表里的链接，会走 `/api/logo?url=...`，由本地服务读取并缓存网站自己的 favicon/icon。还失败时才显示来源字母或内置兜底图形。

2. 注册来源
   - 在 `sources/registry.js` 里 import 新来源。
   - 把它加入 `sourceHandlers`。

3. 同步后端识别
   - 在 `server.py` 的 `SOURCE_NAMES` 加来源中文名。
   - 在 `detect_source_key()` 里加域名识别。

4. 加平台专用标题抓取
   - 如果平台有稳定公开接口，在 `fetch_platform_title()` 里接入。
   - 如果没有接口，先用网页 metadata。
   - 如果平台复制分享文本稳定，优先增强前端 `buildTitle()` 和 `extractSharedTitle()`。

5. 为未来知识库预留 crawl
   - 先让 `crawl()` 返回结构化占位结果。
   - 后面逐步实现正文、评论、媒体、转录、OCR 等抓取。

## 建议的未来内容数据结构

后续不只是保存标题时，可以把每个 entry 扩成类似这样：

```json
{
  "id": "entry-id",
  "kind": "link",
  "sourceKey": "weibo",
  "sourceName": "微博",
  "url": "https://weibo.com/...",
  "title": "用户：正文开头...",
  "createdAt": 1778155200000,
  "content": {
    "status": "ready",
    "format": "markdown",
    "text": "清洗后的正文或转录文本",
    "summary": "可选摘要",
    "author": "作者名",
    "publishedAt": "原帖发布时间",
    "capturedAt": 1778155200000,
    "language": "zh",
    "attachments": [
      {
        "type": "image",
        "path": "files/entry-id/image-1.jpg",
        "alt": "图片描述"
      }
    ],
    "comments": [
      {
        "author": "评论用户",
        "text": "评论内容",
        "createdAt": "评论时间"
      }
    ],
    "references": [
      {
        "type": "source",
        "url": "https://weibo.com/..."
      }
    ]
  }
}
```

## 知识库目录格式

现在每条收藏都会生成一个独立 item 目录：

```text
clipy_library/items/<entry-id>/
  metadata.json
  content.md
  snapshot.html
  assets/
```

各文件用途：

- `metadata.json`：机器读的元数据。包含标题、来源、URL、本地文件路径、保存时间、解析状态、解析方法、内容路径。
- `content.md`：给 LLM 用的清洗文本。包含 YAML front matter、基础信息、正文、图片引用或文件解析文本。
- `snapshot.html`：给人离线浏览用的 HTML 快照。当前由 `content.md` 渲染而来，后续可以升级为原网页 DOM 快照。
- `assets/`：该 entry 的图片和网页资源。链接内容里的图片会复制到这里，Markdown 图片路径会改成 `assets/...`。

总索引文件：

- `clipy_library/items/index.json`
- 里面列出每个 entry 的 `id`、`kind`、`sourceKey`、`title`、`createdAt`、`contentPath`、`metadataPath`、`snapshotPath`、`status`。
- 知识库导入时建议先读这个索引，再逐条读 `content.md` 和 `metadata.json`。

## 文件解析方法

文件上传后会先复制到 `clipy_library/files/<entry-id>/`，知识库层再从本地副本解析，不依赖用户原始路径。

| 文件类型 | 当前方法 | 输出到 `content.md` 的内容 | 状态说明 |
| --- | --- | --- | --- |
| PDF | PyMuPDF / `fitz` | 按页提取文本 | 当前已接入 |
| Word `.docx` | 直接读取 docx ZIP 内的 WordprocessingML | 段落文本 | 当前已接入 |
| PowerPoint `.pptx` | 直接读取 pptx ZIP 内的 slide XML | 按 slide 提取文本 | 当前已接入 |
| Excel `.xlsx` | 直接读取 xlsx ZIP 内的 workbook、sheet、sharedStrings XML | 按 sheet 输出表格行 | 当前已接入，默认每个 sheet 最多 500 行 |
| 图片 | metadata only | 文件名、本地路径、类型说明 | OCR 和图片理解后续接入 |
| 其他文件 | unsupported | 文件基础信息 | 后续按类型扩展 |

当前解析状态：

- `ready`：已生成可用 `content.md`。
- `partial`：多文件 entry 里有一部分文件成功解析。
- `metadata_only`：只保存了文件和元信息，正文还没解析，例如图片。
- `skipped_video`：视频源先跳过，后续接字幕、转录或画面理解。
- `needs_login`：浏览器 profile 打开后看到登录墙，需要用户在 Clipy 管理的浏览器 profile 里登录来源。
- `blocked`：浏览器 profile 打开后看到安全验证、机器人检测或访问限制页面；会保存这次 HTML 快照用于诊断。
- `empty`：不是具体帖子/文章，或没有抓到可保存内容。
- `unsupported`：文件类型暂未接入解析。
- `failed`：解析或抓取过程中失败。

浏览器 profile 当前验证结果：

- 普通文章页，例如 NVIDIA Technical Blog，可以读取真实标题、正文和图片，并生成 `content.md`、`snapshot.html`、`metadata.json`。
- Bloomberg 这类站点在 headless Chrome 下会返回机器人检测页，因此状态会显示为 `blocked`；这不是标题解析失败，而是页面本身没有给这个 profile 返回正文。
- 登录态/验证流程使用同一个 `clipy_library/browser_profile/` 打开可见浏览器窗口，让用户登录或通过验证，再用这个 profile 继续读取。

## 需要登录或验证时的流程

当后台读取链接返回 `blocked` 或 `needs_login` 时，entry 会保留在时间线里并显示“需要验证来源”。这时不会丢弃收藏，也不会把验证页当成正文保存。

用户点击 entry 上的“验证”或“继续验证”按钮后：

1. 前端调用 `POST /api/entries/<entry-id>/verify`。
2. 后端用同一个 `clipy_library/browser_profile/` 打开可见 Chrome 到原链接。
3. 如果旧的 headless Chrome 正占用这个 profile，后端只关闭 Clipy 的 headless Chrome，不碰用户日常 Chrome。
4. entry 写入 `verification.status = waiting`，并记录 `startedAt`、`expiresAt`、`attempt`、`lastCheckedAt`。
5. 后台 verification worker 默认轮询 5 分钟，每 3 秒用同一个 profile 试读一次原链接。
6. 如果读到真实正文，entry 自动转成 `content.status = ready`，并生成 `content.md`、`snapshot.html`、`assets/`、`metadata.json`。
7. 如果用户关掉验证窗口或 5 分钟内未完成，entry 显示“验证未完成，可继续”。再次点击“继续验证”会重新打开同一个 profile、同一个链接，不会新建收藏。

Clipy 确认验证完成的方式不是只靠用户点按钮，而是重新读取页面并确认：

- 页面不再是机器人检测、访问限制或登录页。
- 能读到正式标题、正文段落、图片、作者、发布时间等内容特征。
- `browserCapture.status` 从 `blocked` / `login_required` 变为 `ready`。

## 登录状态面板判断口径

Clipy 的登录状态面板管理的是 `clipy_library/browser_profile/`，不是用户日常浏览器。它只显示在收藏内容读取过程中真实遇到“需要登录”或“需要验证”的链接来源，不再列出所有有 cookie 的网站。

因此普通浏览器 cookie、访问记录、广告 cookie、访客 cookie 都不会让某个站点出现在列表里。某个来源只有当 entry 的 `content.status` / `browserCapture.status` / `verification.status` 记录了 `needs_login`、`blocked`、`login_required`、`waiting`、`expired` 等状态时，才会进入这个面板。

当前状态只显示两类：

- `已登录`：检测到该站明确的登录 cookie 或登录凭据特征，例如 GitHub 的 `user_session` / `logged_in`、X 的 `auth_token`、B站的 `SESSDATA`、微博的 `SUB`、LinkedIn 的 `li_at` 等。
- `未登录`：没有检测到明确登录凭据，或者用户主动退出过。普通访问 cookie、广告 cookie、访客 cookie、页面偏好 cookie 不算登录。

为了避免误导用户，Clipy 不再显示“可能已登录”。如果某个站点只有普通 cookie 但没有明确登录凭据，状态会保守显示为“未登录”。

后续如果要更准确确认，可以逐步加三类确认方法：

1. 站点级轻量确认探针
   - 例如 GitHub 访问 `/settings/profile` 或 API `/user`，B站访问个人 nav 接口，X 访问当前用户相关页面。
   - 只有返回明确用户名或用户 ID 才标记为已登录。

2. 可见页面确认
   - 用 Clipy profile 打开该站首页或个人页。
   - 页面里出现“头像、用户名、退出登录”等明确 UI 信号时才标记为已登录。

3. 保存成功反推
   - 如果某个需要登录的链接在 Clipy profile 中成功读取了正文，就把该来源标记为本次可访问。
   - 这不等同于长期登录，只作为“最近一次可访问”状态记录。

这些确认方法都必须保持 Clipy 的产品边界：用户主动保存自己可访问的一条内容，Clipy 只在自己的 profile 里验证访问状态，不做批量抓取。

重新构建所有 item：

```bash
python3 rebuild_items.py
```

这个脚本会读取 `clipy_library/bookmarks.json`，为所有已有 entry 重建 `items/<entry-id>/`，并把 `item.contentPath`、`item.metadataPath`、`item.snapshotPath` 写回 bookmarks。

## LLM 知识库方向

当前给知识库负责人的推荐导入流程：

1. 读取 `clipy_library/items/index.json`。
2. 对每条记录读取 `metadata.json`，判断 `parse.status`、来源、URL、本地文件列表。
3. 读取 `content.md` 作为 LLM context 的原文。
4. 对 `content.md` 做 chunking、embedding、全文索引。
5. 将 `metadata.json` 中的 `sourceKey`、`createdAt`、`url`、`files`、`parse.method` 作为检索过滤字段。

后续每个来源的 `crawl()` 可以逐步扩展成同一种 LLM 友好记录：

```json
{
  "entryId": "entry-id",
  "sourceKey": "youtube",
  "title": "视频标题",
  "documentType": "video_transcript",
  "markdown": "# 视频标题\n\n## 摘要\n...\n\n## 正文\n...",
  "metadata": {
    "author": "频道或作者",
    "publishedAt": "发布时间",
    "url": "原始链接",
    "capturedAt": "抓取时间"
  },
  "chunks": [
    {
      "text": "适合向量化的一小段内容",
      "startTime": 12.3,
      "endTime": 45.6
    }
  ]
}
```

这样后续可以逐步加入：

- 全文搜索
- 向量检索
- 自动摘要
- 标签和主题聚类
- “我收藏过什么相关内容”的问答
- 按来源、时间、作者、主题回看

## 当前要注意的限制

- 复制分享文本是最稳定的标题来源。
- 纯链接标题依赖平台开放程度，X、微博、小红书、大众点评这类社交平台可能随时被登录墙或反爬限制。
- 短链接需要解析跳转后才能知道真实内容，后续可以为每个平台做短链展开。
- 文件现在已经接入 PDF、docx、pptx、xlsx 文本解析；图片、音频、视频还没有做 OCR、转录或画面理解。
- 旧版二进制 Office 格式如 `.doc`、`.ppt`、`.xls` 暂未接入，建议后续用 LibreOffice 转成 OpenXML 后再解析。
- 视频现在已接入第一阶段 metadata-only 保存：YouTube / B站可以生成 `content.md`、封面和“字幕 / 转录”占位；还没有做字幕、音频转录或画面理解。
- 跟帖/评论还没有保存，后面需要按来源分别设计评论抓取和去噪策略。
