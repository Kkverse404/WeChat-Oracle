# WeChat Oracle GUI 桌面图形界面

本地优先的微信群归档与 LLM 助手的图形界面层。本文档是 GUI 的设计说明与开发蓝图（README），
描述目标、布局、五入口功能、用户工作流、安全约束、集成方式与技术选型。

---

## 1. 项目简介

`wechat-oracle` 目前是「三个常驻进程 + 命令行」的形态：`ingest live`（采集）与 `dispatcher`（回复）
同时常驻，其余能力（导入、成员建档、摘要、raw 授权）通过 CLI 子命令触发。
现有 Textual TUI（`run`）只做进程状态与日志渲染，配置要靠手工改 `.env` 或 `setup` 向导。

本 GUI 的目标：

1. 用图形界面覆盖日常操作：选数据源、授权群、看状态、配模型、发画像/摘要。
2. 把最手工、最容易出错的环节（raw 原库授权、群清单、发送二次确认）做成向导化流程。
3. 保持现有进程拓扑与 CLI 能力不变——GUI 是**控制层 + 展示层**，不接管子进程。

**与现有 TUI 的关系**：两者并存。`run` 仍走 Textual TUI；GUI 是独立入口。进程拓扑保持
`ingest live` + `dispatcher`，GUI 只读写同一份 SQLite / `.env` / 进程状态，不改变生产路径。

---

## 2. 设计原则

| 原则 | 说明 |
|---|---|
| 数据本地优先 | 聊天原文、媒体、记忆、密钥一律留在本机；只有用户显式发送时才出网 |
| 显式授权 | 所有真实微信发送（画像、广播、摘要）必须精确群授权 + 二次确认，语义等同 CLI `--yes` |
| 只读优先 | 非编辑态默认只读展示；编辑态才可改，保存统一走 `config_store` 原子写 `.env` |
| 密钥只写不回显 | API key 输入框只接收、不加载回 UI；修改后才触发保存 |
| 失败即失败 | 配置校验失败原地报错不落盘，不做静默兜底 |
| 可审计 | 发送、建档、游标、证据均保留审计轨迹，GUI 只读展示 |

---

## 3. 总体布局

```
┌─────────────────────────────────────────────────────────────┐
│  Header 状态条：采集 / dispatcher / worker / 发送器 四灯       │
├────────┬────────────────────────────────────────────────────┤
│ 首页    │                                                    │
│ 采集与群管理 │                页面内容区                        │
│ 自动回复管理 │          每个入口内部再分 tab / 分区              │
│ 模型管理  │                                                    │
│ 知识库管理 │                                                    │
│ 设置     │                                                    │
└────────┴────────────────────────────────────────────────────┘
```

- 左侧栏固定 6 项：**首页 / 采集与群管理 / 自动回复管理 / 模型管理 / 知识库管理 / 设置**。
- Header 四灯复用 `run_tui.py` 的存活检测逻辑（只读展示进程是否活着）。
- 保存、发送、授权类动作的结果反馈集中在页面底部状态区，不弹窗刷屏。

---

## 4. 功能总览

| 入口 | 一句话职责 | 主要动作 |
|---|---|---|
| **首页** | 现在活着没有 | 看四灯、各群数据、最近审计 |
| **采集与群管理** | 数据从哪来 | 选通道、raw 授权、群清单 |
| **自动回复管理** | bot 怎么说话 | 回复策略、摘要调度、画像发送、lurk |
| **模型管理** | 谁来理解 | 配 LLM/视觉/后端，测试连接 |
| **知识库管理** | 记住了什么 | 群记忆、成员建档、证据 |
| **设置** | 全局与高级 | 路径、并发、预算、doctor |

---

## 5. 各入口详细说明

### 5.1 首页 Dashboard

只读状态总览，一屏回答「现在活着没有」：

- **进程灯**：采集（raw / weflow / ui-live 实际通道）、dispatcher、worker mm、发送器；
  灯色反映存活 + 最近心跳，来源与 `run_tui.py` 一致。
- **各群卡片**：群名、入库消息数（`status` 按群分布）、member-kb 完成度、最后同步时间。
- **最近发送审计**：`delivery_outbox` / `summary_runs` / `member_profile_broadcast_items`
  最新几条（只显示群、时间、状态，不显示正文）。
- **快捷操作**：进「采集与群管理」选群、进「自动回复管理」看调度、进「设置」跑 doctor。

### 5.2 采集与群管理

对应 `ingest_backend` 与 `raw_wechat_*`，是本项目最手工、最容易出错的环节，做成向导化流程。

- **数据源选择**：
  - `ingest_backend`（weflow / wx4py / raw-only）
  - `weflow_base_url`、`weflow_token`
  - 说明当前生产推荐路径（raw 本机只读同步为准，WeFlow 官方可用时走 SSE，wx4py 为可见 UI 回退）。
- **raw 原库授权**（向导式）：
  - `raw_wechat_enabled`、`raw_wechat_account`、`raw_wechat_install_root`、`raw_wechat_workspace`
  - 动作按钮：**扫描账号 / 列出群 / 授权 / 撤销**（对应 CLI `raw scan / groups / authorize / revoke`）
  - `raw_wechat_sync_interval_seconds`、`raw_wechat_reply_fallback_enabled` /
    `raw_wechat_reply_fallback_max_age_seconds`（回退开关在自动回复管理也有联动展示）
- **群清单**：
  - `WO_GROUPS` 增删改，每行显示 canonical `group_id`（`@chatroom`）与显示名；
  - raw 已授权群用「已授权」标记（对应 `raw_wechat/sessions`）；
  - 校验：群名找不到对应 wxid 时提示用 `weflow find / sessions` 诊断。
- **同步状态**：最近一次 raw 同步的 `shards / attempted / inserted / reply_candidates` 计数
  （对应 `raw status`），以及媒体目录占用（`data/media/<group_id>/<kind>/`）。

### 5.3 自动回复管理

dispatcher 的全部策略与调度，是本 GUI 最重的入口，内部再分 tab。

- **回复主开关**：`reply`、`reply_backend`（uia-direct / wx4py / stdout）、`reply_fail_closed`、
  `reply_allowed_groups`（精确显示名白名单）；uia-direct 需显示「无鼠标、精确白名单、fail-closed」提示。
- **Bot 身份**：`bot_name`、`bot_wxid`。
- **触发策略**：`reply_mention_policy`（always / explicit / never）、`agent_base_probability`、
  `agent_proactive_mode`、`agent_cooldown_seconds`。
- **回复行为预算**：`agent_max_steps`、`agent_recent_context_chat`、`agent_continuation_enabled` /
  `max_followups` / `delay_seconds`；标注「回复优先」说明（同步 Phase B 默认关闭）。
- **raw @ 回退**：`raw_wechat_reply_fallback_enabled` / `max_age_seconds`，与采集入口联动。
- **定时摘要**：`hourly_summary_enabled` / `min_messages`、`daily_summary_enabled` /
  `min_messages`、`summary_timezone`、`summary_sync_grace_seconds`、
  `daily_summary_send_delay_seconds`、`daily_summary_chunk_chars`；
  动作：**预览 / 发送**（对应 `summary send-once`，需精确群授权 + 二次确认）。
- **成员画像发送**：按群显示建档完成度；动作 **发送随机 / 发送指定 / 全员广播** +
  **广播进度**（对应 `member-kb send-random / send / broadcast-all / broadcast-status`）；
  只允许已完成建档的非 UNKNOWN 成员，成功/未知项永不重发。
- **lurk 后台学习**：`agent_lurk_enabled`、`agent_lurk_interval_seconds`、
  `agent_lurk_min_new_messages`、`agent_lurk_recent_msgs`。

### 5.4 模型管理

所有调用外部模型的配置，附连接测试。

- **对话 LLM**：`llm_provider`、`llm_endpoint`、`llm_model`、`llm_api_key`、`llm_json_mode`、
  `llm_max_tokens` 及 chat / sum / short / write 分项。
- **视觉模型**：`vision_provider`、`vision_endpoint`、`vision_model`、`vision_api_key`、
  `vision_max_images`、`vision_max_tokens`。
- **Agent 后端**：`agent_backend`（native / openclaw）；OpenClaw 项 `openclaw_gateway_url`、
  `openclaw_token`、`openclaw_agent_id`、`openclaw_timeout_seconds`；本地 OCR/VAD 资产路径提示。
- **连接测试**：LLM / 视觉各一个「测试连接」按钮，复用 `doctor` 的检查逻辑；结果不落日志。

### 5.5 知识库管理

agent 记忆与分群成员知识。

- **群记忆**：按群查看 / 编辑 / 清空 `group_memory` 与 `persona_drift`
  （对应 `agent show / wipe`）；展示字符上限 `agent_memory_max_chars`；
  提示「成员个人事实请走 member-kb，不要重复写入群记忆」。
- **成员知识库**：建档进度条（pending / 处理中 / 已完成 / `__unknown__` 桶计数）、
  画像查看（`member-kb show`）、锁定栏目、`delete` / `rebuild` 动作；
  发送动作复用「自动回复管理」的通道。
- **游标与审计**：`member_update_state` 游标、`member_claims` 证据引用（不展示原话），
  失败不推进游标的说明。

### 5.6 设置

全局与高级项。

- **全局**：`data_dir`、`db_path`、`media_dir`、`log_level`、`wx4py_log_level`。
- **调度与并发**：`dispatcher_poll_interval`、`dispatcher_worker_threads`、
  `member_kb_interval_seconds`、`member_kb_chunk_chars`、`member_kb_max_concurrency`、
  `member_kb_retries`。
- **高级 agent 预算**：`agent_reflection_enabled`、`agent_reflect_max_steps`、
  `agent_memory_max_chars`、`agent_max_tool_calls_per_run` / `per_step`、
  `agent_max_image_reads_per_run`、`agent_max_voice_reads_per_run`。
- **关于**：版本、`doctor` 一键自检、许可证（wx4py AGPL-3.0-or-later）、数据隐私边界说明。

---

## 6. 典型用户工作流

1. **首次接入**：设置 → doctor 自检 → 采集与群管理（选通道 → raw 扫描账号 → 列出群 →
   授权目标群 → 加入群清单）→ 模型管理（填 LLM，测试连接）。
2. **日常运行**：首页看四灯 → 有红灯进对应入口排查 → 看各群消息量与建档进度。
3. **发摘要**：自动回复管理 → 定时摘要 tab → 选群预览 → 确认发送。
4. **发成员画像**：知识库管理看建档完成 → 自动回复管理 → 成员画像发送 → 随机/指定/全员广播
   （全员广播需精确群授权 + 确认，进度走广播状态）。
5. **排查**：设置 → doctor；诊断群名解析问题用「采集与群管理」内置的 weflow 诊断。

---

## 7. 安全与隐私

- 聊天原文、媒体、解密原库、密钥不进日志、不进 Git、不上传；GUI 只在本机展示。
- 密钥只写不回显；`doctor` / 连接测试结果不落日志。
- 发送类动作全部二次确认，且只允许精确授权的 canonical `@chatroom` 群。
- `uia-direct` 发送器遵守无鼠标 UIA 约束（绝不调用 `Click` / `DoubleClick` / `send_to`）。
- GUI 自身无发送能力以外的越权：仍由 dispatcher 串行、白名单约束地执行。

---

## 8. 与现有系统集成

| 集成点 | 方式 |
|---|---|
| 配置 | `config_store` 原子写 `.env`；运行时 `Settings` 只读加载 |
| 数据库 | 复用 `db.get_conn` / `init_db` / 只读查询；`messages` 是唯一原文存储 |
| 进程 | GUI 不接管子进程；状态灯读进程心跳与 DB 计数 |
| 授权 | 复用 `_resolve_summary_send_group` 的精确群解析 + 显示名白名单 |
| 发送 | 复用 `daily_summary.deliver_manual_text` outbox 幂等状态机 |
| 诊断 | 复用 `doctor` / `verify` / `weflow find` 逻辑 |

---

## 9. 技术选型（待定）

候选与取舍：

- **Textual（延续）**：与现有 TUI 同栈、跨平台、纯终端；但用户明确要「图形界面」，终端式可能不满足预期。
- **PySide6（Qt）**：原生桌面观感，Windows 上体验最好，打包复杂度中等，许可证 LGPL 需注意静态链接说明。
- **Tkinter**：Python 自带、零依赖，但控件与现代观感较弱。
- **Web-based（本地服务 + 浏览器）**：界面自由度最高，但要多跑一个本地 HTTP 服务与安全边界。

> 决策待定：若追求 Windows 桌面一致性与后续打包（配合现有 PyInstaller onedir），**PySide6 是推荐项**；
> 若想复用现有 Textual 组件与渲染逻辑，**Textual 是低成本项**。

---

## 10. 开发规划

- **目录规划**（建议）：`src/wechat_oracle/gui/`，页面按入口分模块；只读查询与动作封装在
  `gui/actions/`，复用现有业务函数，不做重复逻辑。
- **测试**：沿用 pytest；页面动作层跑单元测试（授权、发送确认、config 保存校验），
  控件层做轻量 smoke；新增逻辑同样受 `.githooks/pre-commit` 与 doc-sync 约束。
- **里程碑**：
  1. 框架与布局（左侧栏 + 首页四灯）
  2. 采集与群管理（raw 授权向导 + 群清单）
  3. 模型管理（含测试连接）与设置
  4. 自动回复管理（策略 + 摘要 + 画像发送）
  5. 知识库管理（群记忆 + member-kb 只读/编辑）
  6. 打磨：确认弹窗、只读态、审计展示

---

## 11. 已定决策与后续规划

已定：

1. 技术栈：**PySide6**（`uv sync --extra gui`；`wechat-oracle gui` 启动）。
2. 进程控制：**GUI 内嵌 supervisor**。`supervisor.ProcessSupervisor` 为唯一实现，
   TUI `run` 与 GUI 共用；GUI 提供全部启动/停止/重启按钮，关闭窗口时确认并停止子进程。
3. 摘要 + 成员画像发送：留在「自动回复管理」内做 tab（策略 / 定时摘要发送 / 成员画像发送）。
4. raw 授权向导：在「采集与群管理」页内完成 scan → 勾选授权/撤销。

P1 已交付：supervisor 一站式启停、真实进程状态灯 + `events.jsonl` 心跳、运行日志流、
raw 授权向导、摘要 send-once、成员画像单发/随机/全员广播（均带二次确认，走 outbox 幂等）。

后续（P2/P3）：

- 群记忆编辑器（group_memory / persona_drift 可编辑保存）
- member-kb 画像详情查看、锁定栏目、delete / rebuild
- 审计增强：delivery_outbox 明细、广播 campaign 逐项进度
- 日志流增强与 dispatcher 队列深度 / queue_wait_ms 趋势图
- Local Ask 图形版入口
- PyInstaller spec 纳入 PySide6 打包便携版