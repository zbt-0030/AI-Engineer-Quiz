# 提交说明

被测项目：`Qiyin-Tech/AI-Engineer-Quiz`（基于 Keepstack 的素材管理应用，FastAPI + SQLite/​FTS5 + 原生 JS）。

本次改造在**原有页面**上补齐了题目要求的四件事：组合筛选、排序与分页、全量 CSV 导出、修改后结果即时生效，并补了覆盖关键行为的自动化测试与一份可复现的演示数据。

---

## 需求对照

| 题目要求 | 实现位置 | 验证 |
|---|---|---|
| 关键词沿用现有 FTS 字段与分词；空白不限制 | `search._fts_query` + `build_query`（仍查 `asset_fts`，`porter unicode61`） | `test_blank_and_punctuation_*` |
| 集合筛选 / 不限制集合 | `build_query` 的 `collection_assets` 子查询 | `test_combined_filters_intersect` |
| 包含标签多个，必须全含 | `GROUP BY … HAVING COUNT(DISTINCT t.name) = n` | `test_include_tags_require_all_of_them` |
| 排除标签多个，命中任一即排除 | `NOT IN (SELECT …)` | `test_exclude_tag_rejects_any_match` |
| 各条件取交集、只返回 `active` | WHERE 全部 AND 拼接 + `a.status = ?` | `test_deleted_assets_never_surface` |
| 标签完整名称匹配、沿用现有大小写处理、重复标签不影响 | 归一化到 `strip().lower()` 后去重 | `test_tags_are_case_insensitive`、`test_duplicate_tags_*` |
| 同一标签同时包含和排除 → 空结果 | 两个子查询天然互斥 | `test_same_tag_included_and_excluded_is_empty` |
| 页面展示生效条件、可修改可清除 | `#active-conditions` chip（单个删除）+ Clear all | 浏览器端到端 |
| 保留素材类型筛选并应用于导出 | 侧栏 facet → `media_type` 参数，导出同一构造器 | `test_export_honours_media_type_filter` |
| 按创建时间升/降序，有关键词时也遵守 | `SORTS` + `order_by()`，不再强制 `bm25` | `test_sort_is_honoured_with_a_keyword` |
| 上一页/下一页 + 匹配总数 + 自定每页条数 | `#page-prev/#page-next/#page-info/#page-size` | 端到端（2 页 / 14 条） |
| 相同 `created_at` 不重不漏 | `ORDER BY created_at, a.id` | `test_pagination_is_stable_*` |
| 改筛选或排序回到第 1 页 | 所有控件变更前置 `state.page = 1` | 端到端 |
| 快速切换时结果对应最新条件 | `requestSeq` 自增序号，过期响应丢弃 | 代码审查 |
| 空结果 / 加载失败 / 非法分页 / 特殊字符 | 空态文案、error banner、422/400、输入清洗 | `test_api_rejects_invalid_parameters` 等 |
| 页面内 CSV 导出入口 | `#btn-export` | 端到端（下载 14 行） |
| 导出全部匹配、筛选排序与列表一致、不受当前页限制 | `export_csv()` 复用 `build_query`，无 LIMIT | `test_export_contains_every_match_in_list_order`、端到端（`page=2` 仍导 14 行） |
| 至少 7 个字段；多标签格式自定并说明 | `EXPORT_COLUMNS`，多标签用 `\|` 分隔并排序 | `test_export_*` |
| 中文、逗号、引号、换行；零结果仍出表头 | `csv.writer` + `utf-8-sig`；先写表头 | `test_export_quotes_*`、`test_export_writes_header_only_*` |
| 沿用登录鉴权、页面可下载、失败有提示 | `require_user` + Blob 下载 + `toast(..., "err")` | `test_export_endpoint_requires_auth` |
| 仅导出信息清单，不含素材文件 | 只查 `assets` 表字段，不读 blob | `test_export_*` |
| 通过已有入口改标题/描述/标签后查询与导出同步 | 既有 `PATCH /api/assets/{uuid}`、`POST …/tags` 已调用 `reindex_fts` | `test_edit_then_query_and_export_*` |
| 保留应用与技术栈；新增组件说明收益成本 | 见下文「新增组件」 | — |
| 只验收关键词搜索，可隐藏语义入口 | 页面不再暴露 `mode=semantic`，接口能力保留 | 端到端 |
| facet 数字不联动，但不得误标为当前匹配数 | 侧栏标题标注 `all assets`，另有一行 `N matching assets` | 端到端截图 |
| 补自动化测试 + 可复现数据 | `tests/test_search_export.py`、`scripts/seed_demo_catalog.py` | `pytest -q` |
| 提交 fork 链接 / 分支 / commit SHA | 交付邮件中提供（fork 链接、分支 `feat/search-filter-export`、最终 commit SHA） | — |

---

## 1. 运行与验证

环境：Python 3.10+（本次实测 3.12.4）。依赖只有 fastapi / uvicorn / python-multipart / Pillow 与 pytest / httpx，无模型下载。

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'

export KEEPSTACK_AI_ENABLED=false      # 题目只验收关键词搜索，关掉 embedding 相关路径
export KEEPSTACK_OCR_ENABLED=false

uv run --no-sync python -m keepstack seed          # 原有 24 个素材 / 3 个集合
uv run --no-sync python scripts/seed_demo_catalog.py   # 题目场景用的演示数据（新增脚本）
uv run --no-sync python -m keepstack
```

打开 <http://localhost:8000>，用 `admin` / `admin` 登录。

跑测试：

```bash
uv run --no-sync python -m pytest -q
```

不用 `uv` 也可以：`python -m venv .venv && .venv/bin/pip install -e '.[dev]'`，然后 `python -m keepstack` / `python -m pytest -q`。

### 复现题目场景（页面上 5 步）

1. 顶部搜索框输入 `harbor`；
2. 筛选区 Collection 选 `Harbor Campaign 2026`；
3. `Must have all tags` 依次输入 `approved`、`campaign`（回车加入，条件以 chip 形式回显）；
4. `Must not have tags` 输入 `internal`；
5. 结果右侧排序选 `Created · oldest first`，翻页确认，再点 **Export CSV** 下载。

演示数据下这一步的结果是 **14 条**：`Harbor Campaign 2026` 共 30 个素材，其中带 `approved`+`campaign` 的 18 条，扣掉 4 条带 `internal` 的即 14 条。每页 12 条时正好 2 页。

---

## 2. 实测结果

**自动化测试**：`43 passed, 2 skipped`（2 个 skip 是仓库原有的、与本题无关的用例）。其中新增 21 个用例在 `tests/test_search_export.py`。

**页面端到端**（Playwright 驱动本机 Chrome，1440×900，无代理直连 localhost）：

```
login ok, cards on first paint: 24
pager        : Page 1 / 2 · 14 matching assets
conditions   : Matching: "harbor" × collection: Harbor Campaign 2026 × + approved × + campaign × − internal × Clear all
page 1 titles: Harbor aerial view / Harbor night shift / Harbor bridge survey / ...
after next   : Page 2 / 2 · 14 matching assets
page 2 titles: Harbor at dusk / Harbor at dawn
overlap p1/p2: none
download     : assets-20261102T080629.csv bom: True rows: 14
header       : uuid,title,description,filename,media_type,tags,created_at
first row    : 40466041-...,Harbor aerial view,,harbor_aerial_view.jpg,image,approved|campaign,2026-03-07T12:00:00Z
page errors  : none
```

即：关键词 + 集合 + 标签全含 + 标签排除同时生效，总数正确；两页之间无重复无遗漏；导出 14 行与列表总数一致、顺序与列表一致、带 UTF-8 BOM；浏览器控制台无报错。

---

## 3. 实现要点与取舍

**① 一个查询构造器，两个出口（核心决定）**

`keepstack/search.py` 里抽出 `build_query()`，返回 `(joins, where, params)`；列表接口与 CSV 导出都从它拼 SQL，只有列表额外加 `LIMIT/OFFSET`。这样"列表和导出对不上"这件事在结构上就不可能发生，而不是靠两处代码写得一样。总数统计也用同一份 WHERE 包一层 `COUNT(*)`，避免"总数"和"这一页"来自两套条件。

**② 排序必须带唯一兜底键**

`ORDER BY a.created_at DESC, a.id DESC`。只按 `created_at` 排时，同一时间戳的素材在两页之间顺序不确定，会重复或漏项。`SORTS` 里每一项都配了 tie-breaker，`name`/`largest` 同样处理。关键词存在时不再强制 `bm25()`——这是原实现的问题，也是题目明确要求"有关键词时也遵守所选排序"。

**③ 多标签 = AND，用子查询一次算完**

包含标签：`id IN (SELECT asset_id ... WHERE name IN (?,?) GROUP BY asset_id HAVING COUNT(DISTINCT name) = 2)`；排除标签：`id NOT IN (SELECT asset_id ... WHERE name IN (?))`。没有用多个 JOIN，避免"标签重复导致行数翻倍"和"标签越多 JOIN 越多"两个坑。同一标签既包含又排除时，两个条件天然互斥，结果为空。标签名统一走写入路径已有的 `strip().lower()`，与现有大小写处理一致；重复标签先去重再算 `n`，不影响结果。

**④ 关键词安全化**

沿用现有 FTS5 表与分词方式（`porter unicode61`，含 title/description/alt_text/filename/tags/metadata），把非字母数字字符一律转空格再包成 `"term"*` 前缀查询，FTS5 的 `"` `*` `-` `(` 等永远不会被当作语法。全部是符号的输入（如 `!!!`）按"不限制关键词"处理，而不是把 `MATCH ''` 丢给 SQLite 触发 500。

**⑤ CSV：全量、可读、不挑工具**

- 导出不带 `LIMIT`，筛选与排序和列表完全一致，不受当前页影响；分页参数即便传了也被忽略（`page=2` 导出仍是全部 14 行，有测试守着）。
- `csv.writer` 负责逗号/引号/换行的转义；文件用 `utf-8-sig` 编码，Excel 直接双击不乱码；零条结果仍输出表头。
- 列：`uuid,title,description,filename,media_type,tags,created_at`；**多标签用 `|` 分隔并按名称排序**（如 `approved|campaign`）。
- 鉴权沿用现有 `require_user`，并用 `Content-Disposition: attachment` 触发下载；前端用 `fetch` + Blob，失败会 `toast` 报错而不是静默。
- 导出行为写入审计日志（`asset.export`，含文件名与行数）。

**⑥ 前端**

原生 JS，没有引入构建步骤或新依赖。筛选区三个控件（集合下拉、包含标签、排除标签，标签支持 datalist 补全 + 回车成 chip），生效条件以可删除 chip 回显并有 Clear all；排序下拉、上一页/下一页 + `Page X / Y · N matching assets` + 每页条数；改筛选或排序一律回到第 1 页；请求带自增序号，过期响应直接丢弃（快速切换条件时最终结果对应最新条件）；加载失败有 error banner，不残留旧结果；翻到超出范围时自动收敛到最后一页。

**⑦ 新增组件（收益 / 成本）**

| 新增 | 收益 | 成本 |
|---|---|---|
| `GET /api/assets/export.csv` 路由 | 导出与列表共用一个构造器，一致性由结构保证；前端只多一个按钮 | 多一条路由（且必须声明在 `/api/assets/{uuid}` 之前）；导出为同步内存拼接 |
| `build_query()` / `export_csv()` 两个函数 | 筛选语义只有一处定义，改条件不会两边漂移 | 参数组装比原来多一层间接，读代码要跟着跳一次 |
| `scripts/seed_demo_catalog.py` | 验收场景可复现（30 个素材、4 种标签组合、含相同时间戳） | 仅开发期使用，运行时不加载；不进 `python -m keepstack` 的启动路径 |
| 无新增第三方依赖、无新表、无迁移 | 干净环境按原 README 一条命令即可起 | — |

**⑧ 未做的取舍**

- 分页用 `LIMIT/OFFSET` 而不是 keyset：数据量与交互方式决定了 offset 足够，且实现更直观；数据量级上去以后应换 keyset（`created_at + id` 正好可以做游标）。
- 语义搜索入口保留在后端（`mode=semantic`，现在也遵守新筛选条件），但页面上不再暴露——题目只验收关键词搜索。
- 侧栏 facet 计数仍是全库统计（题目允许不联动），因此标题明确写成"Media type · all assets / Top tags · all assets"，页面上另有一行"N matching assets"表示当前匹配数，两者不会混淆。

---

## 4. 顺带修复的既有缺陷

- **登录在 Chrome 下完全不可用**（影响本题，必须修）：`web/app.js` 的 `api()` 在只传 `json` 时没有设置 method，`fetch` 收到带 body 的 GET 直接抛 `TypeError: Request with GET/HEAD method cannot have body`，页面表现是"Invalid credentials"，请求根本发不出去。改为带 JSON body 时默认 `POST`。
- **关键词搜索时排序失效**：原 `search.py` 在有关键词时把 ORDER BY 固定成 `bm25(asset_fts)`，`sort` 参数被忽略；现在统一使用所选排序。
- **无 tie-breaker 的分页**：原实现 `ORDER BY created_at DESC`，同秒创建的素材跨页会重复/遗漏。
- **特殊字符导致 500**：`_fts_query` 清洗后可能为空串，`MATCH ''` 是 SQLite 语法错误；现在按"不限制关键词"处理。
- **`status` 参数直接透传**：现在白名单校验（`active` / `deleted`），并对 `sort`、`mode` 做同样校验，非法分页参数由 FastAPI 返回 422。

未修、只记录（范围外）：

- `pyproject.toml` 的 `Pillow>=10.0` 下限偏松。Pillow 10.3 下 `test_xmp_fields_seed_catalog_and_search` 会红（JPEG 里 XMP 段读不出来），Pillow 13 下正常，属依赖版本问题而非代码问题。若要长期稳定，建议把下限提到实际验证过的版本。
- 导出是一次性内存拼接，几十万行级别会吃内存；需要时应改成流式 `StreamingResponse`。

---

## 5. 测试覆盖

`tests/test_search_export.py`（21 个用例，独立构造 15 个素材 + 3 个集合，互不污染）：

- 组合筛选取交集：关键词 + 集合 + 双标签 + 排除标签 + 仅 `active`；
- 包含标签必须全含、排除标签命中任一即排除、重复标签不影响结果、同一标签同时包含与排除返回空；
- 关键词为空/纯符号/混合特殊字符不报错；
- 关键词下排序仍遵所选方向（防止 bm25 回归）；
- 7 个 `created_at` 完全相同的素材按每页 3 条翻页：无重复、无遗漏，且升/降序互为逆序；
- 越界页返回空列表但总数不变，`limit/offset` 越界被夹到合法区间；
- 导出与列表逐行一致（含跨页拼接）、零结果只有表头、逗号/引号/换行/中文往返校验、`media_type` 筛选对导出同样生效；
- HTTP 层：分页参数、401 未登录导出、`text/csv` + `attachment` + BOM、非法 `status/sort/mode` 返回 400；
- 修改标题/描述后新的关键词能查到、加标签后组合筛选命中，且导出反映最新值（对应需求 4）。

数据构造方式：测试里直接写 `assets` + `asset_fts` + 标签行（与 `ingest.reindex_fts` 写入的字段一致），这样能把 `created_at` 精确设成相同值；手工验收则用 `scripts/seed_demo_catalog.py` 生成带真实图片与缩略图的数据。

---

## 6. 已知限制 / 未完成项

- 导出为同步生成，超大结果集没有分批或后台任务（题目不要求异步导出）。
- 前端没有做筛选条件写进 URL，刷新页面后条件丢失（未在需求内，未实现）。
- 语义搜索的排序仍按相似度，不参与 `sort` 选择；如需要可以再加。
- 未向上游 Keepstack 提 PR（题目说明不需要）。
