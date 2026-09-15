# imgref

> 自用工具：画师找绘画参考图。AI 编码代理请先读 [AGENTS.md](AGENTS.md)；
> 给 AI 调用方的用法说明在 [SKILL.md](SKILL.md)。

无状态的多图源参考图搜索：**一个图源 + 一个查询串 → 一张编号拼图 + 一张候选表**。

```bash
python3 scripts/imgref.py search wikimedia "M1911 pistol left side" --label side
```

```
[wikimedia] 'M1911 pistol left side' | 候选 12 → 12 格 | 去重排除 0 | 2.2s
grid: /abs/path/.out/20260915-143556-wikimedia-m1911-pistol/grid.jpg
data: /abs/path/.out/20260915-143556-wikimedia-m1911-pistol/results.json

  #  size       source                 id
  1  2632x1868  commons.wikimedia.org  wikimedia|https://upload.wikimedia.org/.../1911A1-JH02.jpg
  2  3648x2736  commons.wikimedia.org  wikimedia|https://upload.wikimedia.org/.../460Rowland_M1911.jpg
  …
```

调用方拿到的就是**两个绝对路径**：看 `grid.jpg`（图上有编号），从表里抄 ID 用。

---

## 为什么是这个形态

它原本是一个 MCP 服务器，存在的理由是"给纯文本模型外包一双眼睛"。那个理由消失了：
调用方的模型自己就有视觉、自己就是语言模型。于是 LLM 层、会话状态机、
关键词解析、视觉筛选**全部删掉**——不是搬家，是不需要了。

剩下的是真正有价值的机械管线：多图源异步搜索、编号拼图、aHash 去重、按 ID 取图。
这些都是一次性的无状态处理，一个脚本 + 一份说明就够了。

| | 旧 MCP | 现在 |
|---|---|---|
| 形态 | Node 常驻进程 + config 段 + 改一次要重启 | 一个 Python 脚本 + SKILL.md |
| 配置 | `.env` + API key + 自定义供应商端点 | **零 key 零配置** |
| 谁做编排 | 服务端的会话状态机（轮次 a/b/c、TTL、分组） | **调用方 AI**，自己并发自己挑 |
| 依赖 | Node + sharp + pi-ai | Python 3.10+ + httpx + Pillow |

## 命令

```
imgref search <provider> "<query>" [options]   搜索 → 编号拼图 + 候选表
imgref preview <id>...                         按 ID 取图到缓存目录（可降采样）
imgref download <id>... --out DIR              按 ID 取全分辨率原图
imgref montage <results.json>... --out FILE    把多轮拼图合并成一张（helper）
```

- `preview` / `download` **都是多选**：位置参数给多个、`--ids-file`、或 `--pick 1,3,7 --from <manifest>`。
- `--exclude <旧 manifest>` 按 **aHash** 排除上一轮出现过的图（迭代重搜用）。
- `imgref search --help` 看图源索引；`imgref search <provider> --help` 看该图源的**专属参数**。

帮助文本刻意分成三段，回答"哪些参数所有图源共用、哪些是某个图源专属"：

```
全局参数（所有命令共用）:
  --cache-dir / --no-cache / --timeout / --proxy / --concurrency / -v / -q
搜索参数（所有图源共用）:
  --out / --keep / --limit / --label / --cols / --rows / --cell / --max-edge / --exclude / --no-json
bing 专属参数（来自 Bing）:
  --safe {off,moderate,strict}      成人内容过滤级别（默认 moderate）
```

## 设计要点

**ID 自包含**：`<provider>|<image_url>`。拼图上的数字只是给眼睛的抓手，
不是 ID——数字离开那张图就没有意义，而 ID 可以单独复制、单独传递、单独解析。
`preview` / `download` 因此**完全不需要 manifest**。

**无状态**：没有会话、没有轮次、没有 TTL。跨轮去重靠调用方显式传 `--exclude`。
**缓存不是状态**：删掉缓存只影响快慢，不改变任何结果（跟热着的 TCP 连接同级）。

**编排归调用方**：一次调用只做一件事。要 3 个角度就发 3 条命令，
要合并就显式 `montage`。脚本里没有"批量"这个概念。

**拼图按视觉模型的预算做**：默认 4×3、每格 375px → 画布 1536×1310，
正好顶满 1536 长边。再大只会被降采样，白扔算力。编号画在每格底部的黑条上
（不盖主体、字号开到 0.78×条高），JPEG 用 `subsampling=0` 保证文字不糊。

**图上只画 ASCII**：Pillow 默认字体渲染不了中文，为了中文标签去背一个 CJK 字体
又会毁掉"零字体依赖"。所以分组/域名等文字只保留 ASCII，中文说明放在 stdout 表和 JSON 里。

**图源 = Protocol，不是继承**：一个图源就是一个普通类，
声明 `name / label / args / requires` + 实现 `async def search(...)` 即可，
连基类都不用继承（`Protocol` 在注册表那里做静态检查）。
可选能力（`headers_for` 决定 referer/UA、`parse_options` 解析私有参数）
用**独立的小 Protocol** 表达——框架完全不知道 referer 是什么东西。

**分页是不透明游标**：谁家按 page、谁家按 offset、谁家给 continuation token
（wikimedia 给的就是个 JSON 对象）、ddg 甚至直接把 `next` URL 塞进游标，
全被适配器吞掉；框架只做 `cursor = page.next_cursor`。

## 加一个图源

```python
# imgref/providers/example.py —— 没有 class 继承，没有 super()
class Example:
    name: str = "example"
    label: str = "示例图源（无需 key）"
    args: Sequence[Arg] = (Arg("--mode", choices=("fast", "full"), default="fast", help="……"),)
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options: ...      # 可选
    def headers_for(self, url: str) -> Mapping[str, str]: ...    # 可选：referer/cookie/UA

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        payload = await ctx.get_json("https://api.example.com/search", params={"q": query, "n": limit})
        return Page([...], next_cursor=None)
```

然后把实例加进 `imgref/providers/__init__.py` 的 `_ALL` 元组——那一行就是静态检查点，
mypy 会核对它是否满足 `Provider` 协议。帮助文本会自动出现（含"专属参数"分组）。

## 环境管理

`scripts/imgref.py` 是个**自举入口**：它只在标准库上运行，检测到 `.venv` 缺失或
`requirements.txt` 变了，就建环境 + `pip install --only-binary=:all:`，然后
POSIX 用 `os.execv` 替换当前进程（内存不叠加）、Windows 用子进程带原样退出码。
带 `IMGREF_BOOTSTRAPPED=1` 护栏防止无限自举，建环境时用 `O_CREAT|O_EXCL` 锁防止并发撞坏。

依赖全落在技能自己的 `.venv` 里，全局零污染；日常调用就是
`python3 scripts/imgref.py ...`，不需要任何前缀。

自举入口在下面这些环境实测过（全局环境都保持干净）：

| 环境 | 首次运行 | 之后每次 |
|---|---|---|
| Windows 11 + Python 3.10.17（最低版本） | 14.3s（建 venv + 装依赖） | 0.23s |
| Windows 11 + Python 3.13.3 | 11.5s | 0.22s |
| `python:3.13-slim`（Debian/glibc，容器） | 30.6s | 0.77s |
| `python:3.13-alpine`（musl，容器） | 32.0s | — |

另外验证过：3 个进程同时首次自举只会建一次环境（锁生效）；Python 3.8 会被护栏
挡住并给出可操作提示，而不是抛出语法错误。

装依赖用 `--only-binary=:all:`（不在小内存机器上触发本地编译）；两个依赖在
glibc 和 musl 上都有现成 wheel。若将来引入只有源码包的依赖，这一条会拦下它。

## 开发

```bash
uv venv --python 3.10 --seed .venv        # 按最低支持版本建环境，能挡住用高版本语法
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt   # POSIX 用 .venv/bin/python

.venv/Scripts/python.exe -m mypy      # strict，48 个文件
.venv/Scripts/python.exe -m pytest    # 273 个用例，全部离线
.venv/Scripts/python.exe scripts/smoke.py wikimedia "M1911 pistol" --download   # 真机 smoke
```

语言级别 **Python 3.10+**（`mypy` 也按 3.10 校验），`mypy --strict`，全部公开函数
带完整类型标注与 docstring。测试用 `httpx.MockTransport` 注入假网络、用确定性生成的
图片做夹具，**不联网**。

## 本机验证状态（2026-09）

- `wikimedia` 全链路实测通过：真实搜索 12 张 M1911 → 编号拼图 → 目视校对无误 →
  `preview` 降采样 → `download` 拿到 2220×1488 原图 → 三条不同角度的搜索 `montage` 合并成 12 格。
- `--exclude` 实测：用上一轮 manifest 重搜同一查询 → 12 张全被 aHash 命中排除（退出码 2）。
- 本机网络：`bing` 与 `ddg` 目前返回反爬内容（bing 给出与查询无关的图，ddg 的 i.js 直接 403），
  `openverse` 超时——都是本机出网环境问题，不是代码问题；`wikimedia` 稳定。
- `mypy --strict` 全绿；`pytest` 273 passed（Python 3.10 与 3.13 各跑一遍）。
