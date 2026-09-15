# AGENTS.md

本文为 AI 编码代理（Claude Code、Cursor、pi 等）提供本仓库的背景与约定。
**项目是自用工具**：用户是画师，用它为绘画创作搜集参考图。

## 项目意图（改动前先读）

- 用户说人话（"M1911 各角度图片，要正面、侧面、正侧面"），**调用方 AI** 把这句话
  拆成几个查询串 → 一个图源一条命令 → 每个查询得到一张**编号拼图** + 一张候选表 →
  调用方自己看拼图挑编号 → 按**自包含 ID** 取图。
- **这个工具里没有任何 LLM 调用，这是刻意的。** 调用方自己就是语言模型、自己就有视觉。
  在脚本里再放一个 LLM 层是重复付费 + 多一条失败路径。
- **编排权归调用方**：脚本不提供"批量/多角度/会话/轮次"的概念。
  一次调用 = 一个图源 + 一个查询串。要并发，调用方自己发多条。
- 自用项目：优先实用、可跑、少抽象。不要引入框架、不要为了"完整性"加功能。

## 常用命令

```bash
uv venv --python 3.10 --seed .venv     # 按最低支持版本建，能挡住高版本语法
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt   # POSIX: .venv/bin/python

.venv/Scripts/python.exe -m mypy       # strict；改完必须全绿
.venv/Scripts/python.exe -m pytest     # 273 个用例，全部离线，不许联网
.venv/Scripts/python.exe scripts/imgref.py --help
.venv/Scripts/python.exe scripts/smoke.py wikimedia "M1911 pistol" --download   # 真机 smoke（会联网）
```

入口是 `scripts/imgref.py`（自举）→ `imgref/cli.py:main`。

## 架构地图

```
scripts/
  imgref.py     自举入口：stamp + 锁 + flush + 护栏；POSIX execv / Windows 子进程
  smoke.py      真机 smoke：search → preview → download 一条龙
imgref/
  cli.py        两段式解析（先认图源，再建带专属参数的解析器）+ 分组 help + 退出码
  run.py        search 编排：搜索 → 下载缩略图 → aHash 去重 → 拼图 → manifest
  grab.py       preview / download（共用一份实现）
  montage.py    helper：合并多轮拼图（不发网络）
  grid.py       拼图渲染 + 版式数学（GridSpec）+ ASCII 约束
  manifest.py   results.json 读写、--exclude 集合、--pick 编号翻译
  providers/    base.py（Protocol + collect 分页驱动）+ 5 个图源适配器
  ctx.py        网络通道：超时/重试/退避/并发/体积上限/代理/缓存
  cache.py      按 URL 的内容缓存（不是状态）
  imaging.py    aHash / 尺寸探测 / 扩展名嗅探
  types.py      ImageResult / Page / Arg / 自包含 ID
  urlutil.py    URL 归一化（只用于比较，不能拿去发请求）
  fsutil.py     硬链接优先、目录轮转、文件名去重
  errors.py     异常层次 + 退出码约定（0/1/2）
tests/          pytest：全部离线，MockTransport 注入假网络
```

## 关键设计决策（改动前必读）

1. **ID 必须自包含**：`<provider>|<image_url>`。拼图上的序号只是视觉抓手，**不是 ID**。
   一旦让 ID 依赖 manifest，`preview`/`download` 就得携带状态，"无状态"就是假的。
2. **manifest 是副产品，不是必需品**：`preview`/`download` 不读它；它只服务溯源、
   `--pick --from`、`montage`、`--exclude`。任何"必须有 manifest 才能工作"的改动都是回退。
3. **`--exclude` 按 aHash 而不是 ID**：同一张图换 URL/缩略图尺寸/图源，按 ID 根本排不掉。
   manifest 里必须存 `ahash`，否则这个功能会悄悄失效。
4. **拼图默认 4×3 / cell 375 → 1536×1310**，正好卡在视觉模型 1536 长边预算内。
   调大只会被降采样，白扔算力。编号画在底部黑条（不盖主体、字号 0.78×条高），
   JPEG 用 `subsampling=0`。
5. **图上只画 ASCII**：不引入 CJK 字体（`ImageFont.load_default(size=...)` 就够）。
   中文只在 stdout 表和 JSON 里。`ascii_title()` 会**丢掉**非 ASCII 字符，
   所以 "M1911各角度图片" 在图上留下 "M1911"——这正是想要的效果。
6. **图源是 Protocol，不是继承**：`Provider` 只用于静态检查，
   可选能力用独立小 Protocol（`ProvidesHeaders` / `ParsesOptions`）表达。
   **不要**给图源加基类或模板方法。
7. **分页游标不透明**：框架只做 `cursor = page.next_cursor`，绝不解释内容。
   wikimedia 塞 JSON 字符串、ddg 塞整个 `next` URL、bing 塞数字 offset——都合法。
8. **默认 UA 必须是诚实的**（`imgref/1.0 (...)`）：维基媒体对浏览器 UA 直接 403
   （实测 "Please respect our robot policy"）。需要伪装浏览器的抓取型图源
   在自己的请求里覆盖（见 bing/ddg 的 `_BROWSER_HEADERS`）。改 UA 前先想清楚这一点。
9. **缓存可以删，状态不能**：`BlobCache` 删掉只影响快慢；`--exclude` 那类由调用方
   显式传入的才算状态。往磁盘上加"隐式记忆"（全局 seen 库之类）是被否决过的方案。
10. **退出码**：0 成功（部分失败也算成功，打 `warn:`）；1 用法/抓取失败；2 一个结果都没有。
    结果与警告都走 **stdout**（调用方只读一个流就不会漏看失败）；只有 `-v` 调试走 stderr。

## 类型与风格约定

- **Python ≥ 3.10**（最低版本是硬约束，为了能在老一点的服务器上跑）。可用的新东西：
  `match`、`@dataclass(frozen=True, slots=True)`、`X | Y` 注解、`zip(strict=True)`。
- **不要用 3.11+ / 3.12+ 的语法和标准库**，它们在 3.10 上会直接语法错误或 ImportError：
  - ✗ PEP 695：`type X = ...`、`def f[T](...)`、`class C[T]` → 用 `X: TypeAlias = ...` + 模块级 `TypeVar`
  - ✗ `datetime.UTC`（3.11+）→ 用 `datetime.timezone.utc`
  - ✗ `StrEnum` / `asyncio.TaskGroup` / `ExceptionGroup` / `except*` / `typing.Self` / `@override`
  - 需要时再逐个确认；`mypy` 配了 `python_version = "3.10"` 能挡住大部分，但它挡不住运行时的东西，
    所以**改完务必在 3.10 解释器上跑一遍 `pytest`**。
- **`mypy --strict` 必须全绿**（`files = ["imgref", "scripts", "tests"]`）。
  因为 `scripts/imgref.py` 与包 `imgref/` 同名，配置里开了 `explicit_package_bases`。
- **所有公开函数/类/模块都要 docstring**；类型标注完整。docstring 与注释用中文。
- 不用 `Any` 除非是刻意的协议逃逸口（`Provider.search` 的 `opts: Any` 有注释解释原因）。
- 第三方调用统一走 `ctx`：**图源不许直接 import httpx**，否则超时/重试/缓存口径会漂。

## 测试约定（改代码必须同步）

- 全部离线。网络用 `tests/helpers.mock_ctx(handler)` 注入 `httpx.MockTransport`；
  不该联网的用例用 `unused_ctx()`（一发请求就炸）。
- 图片夹具用 `tests/helpers.make_image(seed)`：4×4 黑白大色块，**结构性差异**。
  **不要用纯色或整体亮度偏移**——那对 aHash 是退化输入/不变量（有测试专门锁住这个事实）。
- CLI 端到端用例必须是**同步函数**：`cli.main()` 内部自己 `asyncio.run()`，
  在 async 测试里调用会炸 "asyncio.run() cannot be called from a running event loop"。
- 图源适配器的测试模式：用 `mock_ctx` 断言**请求参数**（params/headers/body）+ 用真实形态的
  响应夹具断言解析结果。夹具要照着真实页面形状写（例如 bing 的 `m="{&quot;murl&quot;...}"`）。
- 改完跑：`mypy` → `pytest` →（涉及真实行为时）`scripts/smoke.py`。

## 踩过的坑（别重踩）

- **`scripts/imgref.py` 会影子掉 `imgref` 包**：从 `scripts/` 目录跑任何脚本时，
  `sys.path[0]` 是 `scripts/`，`import imgref` 会拿到那个入口文件。其它脚本要在开头
  `sys.path.insert(0, <repo root>)`。入口本身也是这么做的。
- **`asyncio.run` 不能嵌套**：见上面的测试约定。
- **httpx 的 `trust_env=True` 原生认 `HTTPS_PROXY`/`NO_PROXY`**，不需要自己写 ProxyAgent
  （不要退回手写代理选择逻辑）。
- **`Image.getdata()` 已废弃**：aHash 用 `Image.convert("L").resize((8,8)).tobytes()`。
- **`ImageFont.load_default(size=N)` 需要 Pillow ≥ 10.1**，好处是零字体文件依赖。
- **`--only-binary=:all:` 在 Alpine/musl 上实测可用**（Pillow 有 musllinux wheel，httpx 是纯 Python）：
  容器里 `python:3.13-alpine` 自举成功。但这条约束仍然要留着——将来加进一个只有 sdist 的依赖，
  它就会在小内存机器上触发本地编译并失败；那时要么换依赖，要么改这里的策略。
- **pip 安装要给 `--quiet`**，否则首次运行会把 pip 的输出混进 CLI 的 stdout。
- **裸 `mock_ctx` 客户端的默认 UA 是 `python-httpx/x`**：测"图源有没有伪装 UA"时，
  要断言 `!= BROWSER_UA`，不能断言"没有 user-agent 头"。
- **抓取型图源被反爬时不会报错，而是返回无关内容**（实测 bing 查 M1911 返回猫图和乌克兰国旗）。
  所以"有 12 张图"不等于成功——验收必须真的看图。SKILL.md 里已写明这条。

## 本机验证状态（2026-09）

- `wikimedia` 端到端实测通过（真实搜索 → 拼图目视校对 → preview → download 原图 → montage）。
- `bing` / `ddg` 在本机返回反爬内容，`openverse` 超时——环境问题，与本仓库代码无关。
- 自举入口实测环境：Windows 11 + Python **3.10.17**（最低版本）与 3.13.3、
  `python:3.13-slim`（Debian/glibc）、`python:3.13-alpine`（musl）；全局环境均零污染。
  另验证：3 进程并发首次自举只建一次环境；Python 3.8 被护栏挡住并给出可操作提示。
- `mypy --strict` 全绿（按 `python_version = "3.10"` 校验）；
  `pytest` 273 passed **在 3.10 与 3.13 两个解释器上都跑过**。
