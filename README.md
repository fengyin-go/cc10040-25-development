# 多维分析查询与指标平台

支撑多维分析查询、指标计算、口径管理和数据服务发布的数据应用平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8015/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5195/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8015`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_definition` | 指标 | 指标名称、计算口径、统计粒度 |
| 维度建模 | `dimension_model` | 维度模型 | 模型名称、事实表、维度列表 |
| 查询构建器 | `query_builder` | 查询模板 | 模板名称、关联指标、维度组合 |
| 数据服务 | `data_service` | 服务接口 | 接口名称、关联指标或模型、请求方式 |
| 口径版本 | `caliber_version` | 口径版本 | 所属指标、版本号、口径描述 |
| 查询日志 | `query_journal` | 查询日志 | 查询时间、操作人、涉及指标 |
| 下钻分析 | `drill_down` | 下钻路径 | 路径名称、起始指标、下钻维度序列 |
| 报告模板 | `report_template` | 报告模板 | 模板名称、包含指标集、报告周期 |
| 实验分群 | `experiment_group` | 分群规则 | 规则名称、筛选条件、群组大小 |
| 数据看板配置 | `data_dashboard` | 看板配置 | 看板名称、包含图表、刷新间隔 |
| 用户分群 | `user_cohort` | 分群结果 | 分群名称、分群条件、群组大小 |
| 异常检测器 | `anomaly_detector` | 检测规则 | 规则名称、目标指标、检测算法 |
| 数据导出服务 | `data_export_service` | 导出任务 | 任务名称、数据范围、目标格式 |
| 分群对比分析 | `segment_compare` | 对比任务 | 对比名称、分群A、分群B |
| 透视表配置 | `pivot_table` | 透视配置 | 配置名称、数据源指标、行维度 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询缓存层 | `cache_layer` | 缓存策略 | 策略名称、关联查询模板、缓存时长 |
| 数据追溯系统 | `trace_system` | 追溯链条 | 追溯ID、起点指标、中间步骤 |
| 口径重算引擎 | `recalc_engine` | 重算任务 | 关联指标、新口径版本、历史时间范围 |
| 字段血缘 | `field_lineage` | 字段血缘 | 目标字段、来源字段列表、转换逻辑 |

## 分群对照重叠与实验评估

链3 的成员级分析（`segment_compare` → `experiment_group`）：

- **分群对比分析**执行时读取 A/B 两组成员快照，产出「对照重叠视图」：
  交集成员单独列出，并给出两组各自的独立人数和去重后的可观测人数。
  交集只算一次观测，不会把共用成员当成两次独立观测。
- 每次执行都会绑定两份不可变快照版本；重算并发发布新快照（含空名单）
  不会改写已在进行的对比和评估。
- **实验分群**读取对照重叠视图，按实际可比成员计算对照比例，
  评估明细里同时展示两组样本数量、交集和各自的快照版本。
  评估分两步（`/start` 锁定版本、`/complete` 按锁定版本计算），
  所以「评估中」期间发布的空名单只会进入之后的新对比。
- **跨分群洞察**只展示同一对比最近一次完成分析的失衡；
  旧失衡可按对比任务 + 快照版本通过 `/anomalies?snapshot_id=` 追查。

相关接口：

| 接口 | 说明 |
| --- | --- |
| `POST /api/cross/user-cohort/{id}/snapshot` | 发布名单快照，`{"member_ids": []}` 即空名单 |
| `POST /api/cross/segment-compare/{id}/execute` | 执行对比并生成对照重叠视图 |
| `GET /api/cross/segment-compare/{id}/overlap` | 读取重叠视图（可带 `analysis_id` 复核旧分析） |
| `POST /api/cross/experiment-group/evaluate-overlap/{id}[/start]` | 沿视图发起评估（`/start` 只发起不完成） |
| `POST /api/cross/experiment-group/evaluations/{id}/complete` | 按发起时锁定的快照完成评估 |
| `GET /api/cross/segment-compare/{id}/insights` | 跨分群洞察（最新分析的失衡） |
| `GET /api/cross/segment-compare/{id}/anomalies?snapshot_id=` | 按快照版本追查旧告警 |

前端：分群对比分析列表每行有「对照重叠」入口，打开重叠视图面板；
用户分群列表每行有「发布名单快照」入口，可发布指定名单或空名单。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
