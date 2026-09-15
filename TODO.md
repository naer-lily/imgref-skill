# TODO

已知缺口与待办。每条都写清**为什么**、**验证过的接口形态**和**怎么验收**，
免得变成愿望清单。

---

## 1. booru 类图源的标签体系（`yandere` / `safebooru`）

**问题**：这两个图源的查询串是站内标签，而各站词表**不通用**。标签不存在时接口返回
「200 + 空数组」，在本工具里表现为**退出码 2、一条都没有**——调用方（AI）看到的是
"没搜到"，于是会去改关键词，其实是标签写错了。这是目前最容易误导人的失败模式。

实测（2026-09，两个站的 tag 接口各查一次）：

| 标签 | yande.re | safebooru |
|---|---|---|
| `landscape` | 6763 | 9774 |
| `cat_ears` | 存在 | — |
| `scenery` | **不存在** | 64812 |
| `no_humans` | 不存在 | 不存在 |

同一个概念一边有一边没有，这就是问题所在。

### 已经验证可用的标签接口（动手前先看这里）

yande.re —— JSON：

```
GET https://yande.re/tag.json?name=<子串>&limit=<n>&order=count
→ [{"id":2341,"name":"landscape","count":6763,"type":0,"ambiguous":false}]
```

`name` 是**子串**匹配（`name=land` 会返回 `a's_wonderland`、`adel_roland`…），
所以拿它做"相近标签"推荐正合适；`order=count` 用来列热门标签。

safebooru —— Gelbooru tag API，**只返回 XML**：

```
GET https://safebooru.org/index.php?page=dapi&s=tag&q=index&name_pattern=<x>%&limit=<n>
→ <?xml version="1.0" encoding="UTF-8"?>
  <tags type="array"><tag type="0" count="9774" name="landscape" ambiguous="false" id="631"/></tags>
```

⚠️ 这个端点**忽略 `json=1`**（加了也还是 XML）。用标准库 `xml.etree.ElementTree` 解就行，
不要为此加依赖。

### 建议的做法（按性价比排序）

1. **只在 0 结果时解释原因**（首选）：`search` 拿到空结果时，如果该图源支持标签查询，
   就逐个标签查一次，输出类似
   `warn: yande.re 没有 no_humans 这个标签；相近的有 no_bra(207925)、…`。
   常态路径零额外开销，只把最难受的失败模式讲清楚。
2. **独立命令** `imgref tags <provider> [--like land] [--popular 20]`：
   搜索前先确认标签存不存在、大概多少张。调用方 AI 可以先跑一次再决定查询串。
3. 静态 cheat-sheet 写进 SKILL.md：最便宜，但词表有几万个、会腐烂，
   只适合放几条最常用的（`landscape`、`cat_ears`、`swimsuit` 这类）。

### 实现形态

与现有设计保持一致：新增一个**可选小 Protocol**（例如 `SuggestsTags`），
只有支持的图源实现 `async def suggest_tags(ctx, tag) -> list[TagInfo]`，
框架用 `isinstance` 探测。**不要**给 `Provider` 主协议加方法，
也不要在图源上加基类/模板方法（见 AGENTS.md 设计决策 6）。

### 验收标准

- 真机：`search yandere "scenery"` 必须明确说出"这个标签在 yande.re 不存在（相近：…）"，
  而不是只丢一个退出码 2。
- 真机：`search safebooru "scenery"` 是**有**结果的（64812 张），不能被误判成"标签不存在"。
- 离线：用 `mock_ctx` 断言 tag 请求的参数（`name` / `name_pattern`），
  以及"0 结果 + 标签不存在"时 warning 的确切文案。
- 存在的标签（`landscape`）**不能**触发任何多余请求或误报。

---

## 2. 其他已知缺口

- **`huaban` 图源**：非官方 `api.huaban.com/search/`，结构干净（`pins[].file.url`），
  但那个 URL 带 `auth_key` 签名且**会过期**（实测有效期约一天）。把它存进 ID 就直接违背
  "ID 必须自包含、可脱离上下文传递"这条契约——今天 `search`、明天 `download` 就会 403。
  真要做，ID 里只能放 `pin_id`，下载时重新解析一次；那等于在 `download` 路径上引入
  一次上游请求，得先想清楚值不值。
- **缺 `python3-venv` 时的报错路径没有实测过**：`scripts/imgref.py` 里那句
  "Debian/Ubuntu 需 `apt install python3-venv`" 的提示目前只是代码里的文案，
  没在真实环境验证过（当时那个容器测试被打断了）。有空补一个 Debian 容器验证。
- **图片源内容分级**：目前只有 `yandere` 有 `--rating`（默认 safe）。
  `safebooru` 靠站点自身只收 SFW，`duitang` / 抓取型图源没有分级信息——
  拼图直接给用户看之前，调用方需要自己过一眼（SKILL.md 已经提醒"必须真的看图"）。
