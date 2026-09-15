---
name: imgref
description: Search multiple image sources from natural-language reference needs and return one numbered composite grid plus a candidate table. Use when the user wants visual references (painting/drawing reference, "找参考图", "各角度图片"), when you need to show image candidates for the user to pick by number, or when you must download specific images by ID. Stateless: one search = one source + one query = one numbered grid; callers fan out in parallel themselves.
agent_created: true
---

# imgref —— 编号拼图式参考图搜索

给"找参考图"这件事提供三个动词：搜、看、拿。**没有任何 API key，没有会话状态。**

一次 `search` = 一个图源 + 一个查询串 → **一张编号拼图 + 一张候选表**。
要几个角度、几个图源，**你自己并发发几条命令**；脚本只做机械活，编排归你。

## 怎么跑（按平台照抄，别自己发挥）

```bash
# Linux / macOS
python3 scripts/imgref.py search wikimedia "M1911 pistol"

# Windows
python scripts\imgref.py search wikimedia "M1911 pistol"
```

首次运行会自动在技能目录下建 `.venv` 并装依赖（只需一次，之后是毫秒级快路径）。
它只动技能目录自己的 `.venv`，**不污染全局环境**。venv / pip 的路径差异、
并发加锁、stamp 判断都已处理好，你不需要管。

## 标准流程

```
1. 用户说人话（"M1911 各个角度的图，要正面、侧面、正侧面"）
2. 你自己把这句话拆成几个查询串  ← 工具不做任何 NLP，这是你的活
3. 并发发几条 search（每个角度一条，必要时换图源再来一条）
4. 你**看 grid.jpg**（你有视觉），挑出合适的编号
5. 从候选表里抄同一行的完整 ID → download
6. 把图用 MEDIA: 发给用户；或者先发拼图让用户挑编号
```

**第 4 步是关键**：你只需要看那张拼图，不需要读 JSON。候选表已经把
"图上数字 → 完整 ID"的对应关系印在 stdout 里了。

### 例子：多角度并发

```bash
python3 scripts/imgref.py search wikimedia "M1911 pistol front view"   --label front --out .out/m1911
python3 scripts/imgref.py search wikimedia "M1911 pistol left side"    --label side  --out .out/m1911
python3 scripts/imgref.py search wikimedia "M1911 pistol three quarter" --label 3q   --out .out/m1911
```

三条一起发（并发）。如果想让用户看**一张**合并图：

```bash
python3 scripts/imgref.py montage .out/m1911/*/results.json --out .out/m1911/merged.jpg --title "M1911 aspects"
```

### 例子：按编号取图

```bash
# 用户说"要第 3 张和第 7 张"
python3 scripts/imgref.py download --pick 3,7 --from .out/m1911/<run>/results.json --out ./refs
# 落盘是 03-…、07-…，跟图上的数字对得上

# 或者直接给完整 ID（ID 是自包含的，可以脱离上下文单独传递）
python3 scripts/imgref.py download 'wikimedia|https://upload.wikimedia.org/.../M1911A1.png' --out ./refs

# 拼图格子太小、想看细节
python3 scripts/imgref.py preview --pick 3 --from <manifest> --max 1024
```

**落盘文件名的数字前缀 = 拼图上的编号**（只要你带了 `--from`）。不带 `--from` 就
不知道编号，此时文件名不加数字前缀——不会拿"第几个下载的"冒充编号。

`preview` 和 `download` **都支持多选**（位置参数给多个、`--ids-file` 给一文件一行、
或 `--pick 1,3,7 --from <manifest>`）。

### 例子：迭代重搜（不要重复给同一张图）

```bash
python3 scripts/imgref.py search wikimedia "M1911 pistol" --exclude .out/prev/results.json
```

`--exclude` 按 **aHash 指纹**排除，不是按 ID——同一张图换 URL、换缩略图尺寸、
换图源都能认出来。支持重复给多个 `--exclude` 累积。

## 必须记住的规则

1. **ID 是自包含的**：`<provider>|<image_url>`。拼图上的数字**不是** ID，
   它只是给你眼睛用的抓手；从候选表里抄同一行的完整 ID 来用。
2. **永远不要自己拼 URL 去下载**，一律走 `download` / `preview`。
3. **查询串由你写**：一条 search 一个查询串。要多样性就多发几条不同的查询串，
   而不是指望一次查询给你所有东西。
4. **看图上的数字之前，先看那张图是什么**：拼图顶栏写着 `provider | label | query`。
5. **候选表的 `size` 列**：`2632x1868` 是图源声明的**原图**尺寸；带 `~` 的（例如 bing）
   是**缩略图**实测尺寸，因为那个图源没声明原图宽高。要确认原图够不够大就 `preview`
   或直接 `download`，别拿 `~` 那个数当原图尺寸。
6. 退出码：`0` 成功（部分图源/部分缩略图失败也算成功，会有 `warn:`）；
   `1` 用法错误或抓取失败；`2` 一个结果都没有。

## 图源选择

| provider | 适合 | 查询串怎么写 | 说明 |
|---|---|---|---|
| `yandere` | **插画 / 背景 / 角色参考（画师主场）** | booru 标签，空格分隔（`landscape cat_ears`） | 官方 API；原图尺寸、原作者页齐全；默认只出 safe |
| `safebooru` | 同类插画参考，站点只收 SFW | booru 标签 | Gelbooru API，稳定 |
| `duitang` | 中文图片站（照片 / 壁纸 / 素材） | 中文自然语言 | 非官方 napi，无需 key |
| `wikimedia` | 公共领域照片、技术图纸、军械 / 历史资料 | 自然语言 | 官方 API，带版权信息 |
| `openverse` | CC 授权图库 | 自然语言 | 匿名可用 |
| `bing` | 通用网络图片 | 自然语言 | 抓取型；可能被反爬（返回与查询无关的内容） |
| `ddg` | 通用网络图片 | 自然语言 | 抓取型；`i.js` 容易 403 |
| `serper` | 谷歌图片 | 自然语言 | 需要 `SERPER_API_KEY` |

**抓取型图源失效时不会报错，只会给错的图**——你看拼图时必须真的核对内容。不对就换 provider。

### booru 类图源（`yandere` / `safebooru`）的标签词表

这两个的查询串是**站内标签**，不是自然语言，而且各家词表**不通用**：同一个概念在
danbooru 叫 `no_humans`，在 yande.re 可能根本不存在。标签不存在时接口返回
「200 + 空数组」，于是表现为**退出码 2、一条都没有**，看起来像"没搜到"。

- 先用一个确凿存在的单标签试水（`landscape`、`cat_ears`、`swimsuit` 这类），再往上叠。
- 一次堆五个标签很容易全落空；宁可多搜几次。
- 想看原作者：读 manifest 的 `source` 字段（实测给的是 pixiv / x 的原帖链接）。
- `yandere` 默认加 `rating:s`；要放宽用 `--rating all`（可能含露骨内容）。

用 `python3 scripts/imgref.py search --help` 看图源列表，
`python3 scripts/imgref.py search <provider> --help` 看某个图源的**专属参数**
（例如 `wikimedia --mime jpeg`、`openverse --license-type commercial`、`bing --safe strict`）。

**验证结果相关性**：抓取型图源在被反爬时会返回"看上去正常但与查询无关"的结果
（比如查 M1911 返回一堆猫图）。你看拼图时必须真的核对内容，不要只看"有 12 张图"
就认为成功；不对就换 provider 或换查询串。

## 环境

- **不需要任何 API key。**
- 网络受限时走代理：`export HTTPS_PROXY=http://127.0.0.1:7890`（`NO_PROXY` 也认）。
- **PyPI 不通的环境**：首次运行要装依赖，如果 `pypi.org` 不可达，设个镜像就行
  （pip 原生认这个环境变量，不需要改代码）：
  `export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`
- 默认 User-Agent 是诚实的 `imgref/1.0 (...)`——维基媒体这类站点会对浏览器 UA
  返回 403。需要时用 `IMGREF_USER_AGENT` 覆盖。
- 图片缓存（默认落系统缓存目录）**不是状态**：删掉只会变慢，不改变任何结果。
  用 `--no-cache` 关掉。

## 排错

| 症状 | 处理 |
|---|---|
| `HTTP 403 ... 目标站拒绝` | 该图源在这个网络下被反爬；换 `--provider` 或稍后重试 |
| `网络不可达：... HTTPS_PROXY` | 配代理后重试 |
| `没有返回结果` （退出码 2） | 换查询串（更具体/更通用的英文词）或换 provider |
| 结果与查询无关 | 抓取型图源被反代；换 `wikimedia` 或换查询串 |
| `候选全部被排除` | `--exclude` 把这一轮的图全排掉了；去掉 `--exclude` 或换查询串 |
