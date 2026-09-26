# MovieDB 类封装与代码精简计划

## 结论

保持当前函数式分层，不从 `Movie` Active Record 或 Movie/Series/Episode 继承体系开始。首批只在媒体导入管线引入小型 dataclass，解决重复参数与隐式 dict 契约；页面查询和 Jinja 输出继续使用 dict。

## 原则与边界

- 路由继续只做请求解析、调用 query/service、构建 ViewModel 和返回响应。
- `queries/` 只读数据库，`models/library.py` 负责写库，`services/` 编排流程，`connectors/` 负责外部系统。
- 不引入 ORM、Repository、抽象基类、工厂或新依赖。
- 不修改 schema、`library.db`、`imdb.db` 和现有页面结构。
- 保留现有公共函数签名及 Jinja 使用的数据形状，分阶段内部迁移。
- 当前 5 个未提交详情页文件属于用户现有工作，实施时不得覆盖。

## 实施阶段

### Phase 0：锁定行为基线

**范围：** 测试与调用契约，不改业务逻辑。

1. 记录 `query_title()`、`build_title_viewmodel()`、`import_media()`、`import_media_batch()` 的输入输出形状和调用点。
2. 为拟修改的内部边界补最少量断言：movie、series、episode 的 root/target 解析结果；批量复用剧集上下文；写入失败回滚。
3. 保存当前 52 个测试通过的基线，确认测试只使用临时数据库。

**完成条件：** 无功能改动；现有 52 个测试仍通过；新测试能在错误的 root/target/relation 映射下失败。

### Phase 1：在媒体导入边界试点 dataclass

**主要文件：** `services/media_import.py`、`tests/test_library_app.py`。

1. 增加不可变的 `MediaImportRequest`，集中保存 `title_id`、`season_number`、`episode_number`、`alist_url`，统一单项与批量输入的标准化结果。
2. 增加不可变的 `ResolvedTitle`，替代 `_resolve_target()` 和 `_resolve_target_from_context()` 返回的 `{root, target, relation}` 字典；当前下游从不读取 `relation`，因此新对象只保留 `root`、`target`。
3. 保留现有 `ImportBundle`、`SeriesImportContext`、`PreparedMediaImport`；只补 `slots=True` 和必要的类型标注，不把纯计算强行改成实例方法。
4. `import_media()`、`import_media_batch()` 的公开参数与返回 dict 不变，`app.py` 无需跟随改写。

**完成条件：** `services/media_import.py` 内不再通过字符串键访问 resolved/request；单项和批量导入、原子回滚、图片清理行为不变；测试全过。

## 类设计明细与行数估算

行数按“删除旧实现后减去新增类、兼容门面和调用改动”的净值估算；只统计有效 Python 代码，区间用于反映最终排版和测试调整。类化本身预计接近零净减行，其主要价值是固定数据契约。

### 1. `MediaImportRequest`（新增，推荐）

```python
@dataclass(frozen=True, slots=True)
class MediaImportRequest:
    title_id: str
    season_number: str = ""
    episode_number: str = ""
    alist_url: str = ""

    @classmethod
    def from_mapping(cls, item, index: int) -> "MediaImportRequest": ...

    def validated_title_id(self) -> str: ...

    def episode_position(self) -> tuple[int, int]: ...
```

`from_mapping()` 接管字符串/整数/null 标准化，`validated_title_id()` 接管 IMDb ID 格式校验，`episode_position()` 接管季集号校验。它对应 `app.py:317-341` 的表单/JSON 行构造，以及 `services/media_import.py:37-45,348-352,634-666,695-742` 的三个输入辅助函数和批量 `item.get()` 访问。

**预计净变化：-5～+5 行。** 删除三个零散校验函数和部分重复取值，但新增 dataclass 本身。收益主要是字段不再靠约定和字符串键传播。

### 2. `ResolvedTitle`（新增，推荐）

```python
@dataclass(frozen=True, slots=True)
class ResolvedTitle:
    root: dict
    target: dict
```

它替代 `services/media_import.py:48-92,323-345` 返回的 `{root, target, relation}`，并覆盖 `services/media_import.py:355-477,480-527,566-631,669-796` 中 11 处 `resolved["root"]` / `resolved["target"]` 访问。`relation` 只在解析函数内部用于校验，当前没有任何 `resolved["relation"]` 消费者，因此不进入新对象。数据库查询仍由原 resolver 函数执行，类不持有连接，也不增加简单字段代理属性。

**预计净变化：0～+4 行。** 删除未使用的 `relation` 契约基本抵消 dataclass 定义；它的收益仍是字段约束，而非减行。

### 3. `ImportBundle`（已有，保留并微调）

现有属性：`root`、`titles`、`relations`、`artwork`、`cast`、`rating_result`、`staged_files`。只建议加 `slots=True`；不增加方法，因为当前没有一段逻辑仅依赖这些字段且能明显消除重复。

它对应 `services/media_import.py:117-147,258-266,404-513` 中元数据 bundle 的组装和写入参数准备。

**预计净变化：0 行。** 现有设计已经合适，不应为了方法数量继续包装。

### 4. `SeriesImportContext`（已有，推荐增加两个方法）

现有继承属性加：`episodes_by_position`、`episodes_by_id`、`images_prepared`。

建议方法：

- `resolve(request) -> ResolvedTitle`：接管当前 `_resolve_target_from_context()`。
- `prepare_images(library_db, image_root, staging_root) -> None`：接管当前 `_prepare_series_images()`，因为该逻辑直接读写 `images_prepared`、`artwork`、`cast`、`staged_files`。

对应 `services/media_import.py:270-273,293-345,381-401,697-728`。

**预计净节省：4～8 行。** 主体代码只是从函数移动到类中，节省来自少传一个 context 参数和删除两层间接调用；主要收益是把真实可变状态收回对象内部。

### 5. `PreparedMediaImport`（已有，轻微调整）

现有属性：`index`、`input_title_id`、`target`、`alist_path`、`probe`、`bundle`。建议增加 `to_result(file_id) -> dict`，替代 `_result()`；不增加 `save()`，写事务仍由 service + model 控制。

对应 `services/media_import.py:276-283,404-477,480-563`。

**预计净变化：-1～+2 行。** 这是内聚性调整，不是减行项目。

### 6. MDBList 三个不可变数据类（已有，只迁移）

- `MDBListConfig`：`api_key`、`timeout_seconds`。
- `PlatformRating`：`platform_key`、`platform_name`、`score_display`、`score_10`、`score_100`、`votes`、`url`。
- `TitleRatingsSummary`：`imdb_id`、`status`、`latency_ms`、`title`、`year`、`media_type`、`score`、`score_average`、`platform_ratings`、`error_message`。

保持不可变、无业务方法；不增加 `save()` 或数据库字段转换。它们当前在 `scripts/fetch_multi_platform_ratings.py:59-85`，生产 connector 却从脚本导入；计划是原样移动到 `connectors/mdblist.py`，脚本反向导入 connector。

**预计净变化：约 0 行。** 只修正依赖方向。

### 7. `TitleRecord`（暂缓，不进入首批）

若后三阶段仍证明有必要，属性应统一覆盖 `title_basic` 的核心字段：`title_id`、`tmdb_id`、`title_type`、主标题/原始标题/显示标题、语言、年份、时长、类别、简介、标语、IMDb 评分和票数。只允许 `from_row()`、`is_series` 等数据级方法。

当前不同查询返回的是不同字段子集；立即引入会影响 `queries/title.py`、`queries/_shared.py`、多个 ViewModel、媒体导入和写库映射，并仍需转换为 Jinja dict。

**预计净增加：40～100 行。** 在出现至少三个消费者共享完整稳定字段集之前不做。

### 为什么不建立 `Movie` 类

一个看似完整的 `Movie` 需要标题字段、图片、演员、媒体源和进度；其方法很快会变成 `load()`、`save()`、`set_artwork()`、`play()`、`to_viewmodel()`。这些行为目前分别属于 `queries/title.py`、`models/library.py`、`connectors/player.py`、`services/media_import.py` 和 `viewmodels/title.py`。合并后会成为跨五层的 God Object。

**预计净增加：120～200 行，并改动约 8～12 个文件。** 如果只保留属性，它又只是一个比 dict 更重的贫血对象。因此首批不建立 Movie/Series/Episode 类。

## 总体行数预期

- 类封装部分：约 `-10～+15` 行，接近持平。
- `_format_ratings()` 规则表化：预计减少 `45～65` 行。
- manager 图片选项去重：预计减少 `15～25` 行。
- connector/script 依赖调整：约 `-5～+5` 行。
- 全部完成后的保守净减少：约 `55～90` 行。

因此验收不应要求“每个类都省行”，而应要求类消除隐式契约；总行数下降由重复函数逻辑承担。

### Phase 2：修正 connector 与 script 的依赖方向

**主要文件：** `connectors/mdblist.py`、`scripts/fetch_multi_platform_ratings.py`；随后独立处理 `connectors/tmdb.py` 与 `scripts/fetch_tmdb_metadata.py`。

1. 先把 MDBList 的 `Config`、`PlatformRating`、`TitleRatingsSummary` 和请求/解析逻辑移入 connector。
2. 脚本只保留命令行参数、报表和基准入口，并导入 connector；生产代码不得反向导入 `scripts/`。
3. MDBList 完成并验证后，再用同样方式处理 TMDB，避免一次搬动两个大模块。

**完成条件：** `connectors/` 不再导入 `scripts/`；评分同步与导入测试不变；命令行脚本仍可运行。此阶段是代码位置标准化，不新增类层次。

### Phase 3：用函数和声明式数据精简 ViewModel

**主要文件：** `viewmodels/title.py`、`viewmodels/manager.py`、`viewmodels/cards.py`。

1. 将 `_format_ratings()` 的七段重复分支改成小型规则表和少量特殊图标函数。
2. 把 manager 中 poster/backdrop/logo 三组重复映射收敛为一个私有函数。
3. 只在至少两个 ViewModel 真正共享时扩充 `viewmodels/cards.py`；不为了文件行数机械拆分模块。
4. Jinja-facing 输出继续是 dict，不创建 `TitleViewModelBuilder` 或页面对象树。

**完成条件：** 页面键名、URL、默认文案和模板输出不变；重复代码减少；标题详情、首页和管理页断言全过。

### Phase 4：复核是否还需要统一 Title 类

完成前三阶段后，再统计：重复字段映射、KeyError 风险、跨模块 title dict 传递次数及类化预计改动面。只有同时满足以下条件才增加统一 `TitleRecord`：

- 至少三个模块需要同一组稳定字段；
- 类能承载明确不变量或派生行为，而不只是把 `dict[key]` 改成 `obj.attr`；
- 不持有数据库连接，不提供 `save()`、`delete()`、`play()` 等跨层方法；
- 一个统一 `TitleRecord` 足够，不需要 Movie/Series/Episode 空壳子类。

条件不满足则停止，维持 dict + 小型 dataclass 的混合方案。

## 明确不做

- 不建立 Movie/Series/Episode 继承体系。
- 不建立 Active Record、Repository、BaseService 或依赖注入容器。
- 不把 query、write、playback、TMDB 操作塞进一个“电影对象”。
- 不一次性把所有查询结果和模板数据改成类。
- 不因单个函数较长而拆散以 SQL 和字段映射为主的连贯代码。

## 每阶段验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app.py connectors models queries services viewmodels scripts tests
node --check static/site.js
git diff --check
```

另外比较实施前后的 `library.db` 与 `imdb.db` SHA-256；纯重构阶段必须保持不变。每个 Phase 独立提交，任一阶段没有净收益即可停止，不要求完成后续阶段。

## 审计状态

- [x] 现状、类与调用链盘点
- [x] 类封装候选与反例评估
- [x] 实施顺序与验收条件制定
- [x] 52 个现有测试基线验证

**Status:** complete

## 审计中遇到的问题

| 问题 | 处理 |
|------|------|
| `.active_plan` 文本换行导致一次路径拼接失败 | 使用独立 planning 目录，不覆盖现有活动计划 |
| PowerShell 解析复杂 `rg` 正则失败 | 改用多个简单固定模式 |
| `.venv` 未安装 pytest | 使用项目已有的标准库 `unittest`，不新增依赖 |
| 一次 planning 补丁上下文不匹配 | 重新读取文件后按实际文本更新 |
