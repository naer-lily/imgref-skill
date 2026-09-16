# TODO

已知缺口与待办。每条都写清**为什么**、**验证过的接口形态**和**怎么验收**，
免得变成愿望清单。

---

## 1. `imgref tags` 命令（可选，锦上添花）

`yandere` / `safebooru` 现在会在 `search` 里**被动校验**标签（不存在就报错 + 给相近候选）。
还缺的是**主动发现**：调用方想知道"这个站上有哪些和 X 有关的标签"时没有入口。

接口已经验证过，直接可用：

| 站 | 接口 | 返回 |
|---|---|---|
| yande.re | `GET /tag.json?name=<子串>&limit=&order=count` | JSON `[{id,name,count,type,ambiguous}]`；`name` 是**子串**匹配，`order=count` 出热门 |
| safebooru | `GET index.php?page=dapi&s=tag&q=index&name_pattern=<x>%&limit=` | **XML**（`json=1` 被忽略），标准库 `xml.etree` 解 |

做法就是加个动词：`imgref tags <provider> [--like land] [--popular 20]`。
**不要**给 `Provider` 主协议加方法——把现有校验里的查询逻辑抽成模块级函数
（像 `parse_tag_xml` 那样）复用即可，不要新增抽象层。

验收：`imgref tags yandere --like scenery` 能列出 `winter_scenery` 之类；
     `imgref tags safebooru --popular 5` 能按帖子数列出最热门的几个标签。

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
- **标签校验没有跨进程缓存**：每次搜索都要重查一遍标签（每个约 0.5s），
  迭代时同一个查询串反复出现会重复付这个钱。要优化的话 `BlobCache` 是按 URL 缓存字节的，
  把标签查询的响应也丢进去即可——但要先想清楚"标签消失"造成的假阴性（实际上几乎不会发生）。
