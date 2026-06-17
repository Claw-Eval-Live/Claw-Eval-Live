# Market-Driven Task Seed Bank v0.1

这份 seed bank 不是 runnable task 列表，而是 market signal 推出来的 task 种子层。

- total seed budget: `24`
- source of truth: `benchmark/signals/workflow_patterns.csv` + `benchmark/signals/family_weights.csv`
- principle: 先由市场 signal 决定要在哪些 workflow 上发散，再让 LLM / 人工把 seed 扩成 candidate tasks
- non-goal: 不把 legacy benchmark 题库当成 seed 来源

## Workspace-Repair-Config

- market weight: `27.4%`
- family seed budget: `7`

| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |
|---|---:|---:|---|---|---|
| Shell & Terminal Operations | 572227 | 3 | local_workspace | 从一组终端输出和日志中定位失败根因，并写出修复说明 | 检查命令执行结果与目录状态，修复环境后重新验证 |
| Coding & Development Tools | 299970 | 2 | local_workspace | 修复脚本或测试失败问题，并总结哪些改动是必要的 | 在不改业务目标的前提下让工具链重新跑通 |
| File System & Backup | 145573 | 2 | local_workspace | 围绕 File System & Backup 设计一条复合工作流任务 | 要求包含明确 artifact 和可验证输出 |

## Document-Transform

- market weight: `22.3%`
- family seed budget: `5`

| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |
|---|---:|---:|---|---|---|
| Media Processing | 321725 | 2 | attachments_or_documents_service | 从多媒体素材提炼重点，并转成可审阅的文字交付物 | 围绕图像 / 音视频内容做加工后摘要 |
| PDF Processing | 304196 | 2 | attachments_or_documents_service | 从 PDF 中提取关键信息并整理成结构化输出 | 比较多份 PDF 的差异并写出统一结论 |
| Document Summarization | 219679 | 1 | attachments_or_documents_service | 把长文档压缩成可执行摘要，并保留关键约束 | 从多页资料里提炼决策者真正需要看的信息 |

## Cross-Tool-Orchestration

- market weight: `19.6%`
- family seed budget: `5`

| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |
|---|---:|---:|---|---|---|
| Workflow Automation & Orchestration | 536599 | 2 | mock_services | 跨多个业务系统完成一条完整事务链，并做最终汇总 | 根据输入约束决定先后顺序，自动推进多个动作 |
| Calendar & Scheduling | 180880 | 2 | mock_services | 结合日历和上下文安排会议，并说明为何这样安排 | 围绕即将发生的会议，产出准备材料或协调动作 |
| Notes & Knowledge Management | 174638 | 1 | mock_services | 从历史笔记和知识材料中抽取背景，服务当前任务 | 把分散知识整理成 onboarding / prep / recall 文档 |

## Research-to-Artifact

- market weight: `11.8%`
- family seed budget: `3`

| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |
|---|---:|---:|---|---|---|
| Browser Automation & Scraping | 263093 | 1 | mock_services_or_web | 围绕网页信息收集与整理，产出可验证的结构化结果 | 从网页流程中提取多步证据，再输出报告 |
| News & Feed Monitoring | 176816 | 1 | mock_services_or_web | 监控一组来源，抽取对业务有影响的更新并汇总 | 按重要度筛选新闻流，形成 briefing |
| Web Research & Reports | 86504 | 1 | mock_services_or_web | 对多个外部方案做结构化调研并形成推荐建议 | 从多源资料中提炼可执行结论，而不是只堆信息 |

## Data-Analysis-Reporting

- market weight: `10.5%`
- family seed budget: `2`

| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |
|---|---:|---:|---|---|---|
| Spreadsheet & Database Operations | 247510 | 1 | attachments_or_mock_services | 从表格 / 数据库风格资料中抽取、对齐并核对字段 | 做跨表合并或对账，输出结构化结论 |
| Financial & Investment Analysis | 141263 | 1 | attachments_or_mock_services | 围绕预算、收益或成本做分析并给出决策建议 | 对多项财务信息做汇总和优先级判断 |

## Communication-Drafting

- market weight: `7.0%`
- family seed budget: `2`

| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |
|---|---:|---:|---|---|---|
| Messaging & Chat Platforms | 220795 | 1 | mock_services | 根据聊天上下文做团队同步或决策通知草稿 | 从碎片消息中整理关键事项并形成正式输出 |
| Email Management & Drafting | 58228 | 1 | mock_services | 基于邮件上下文起草高质量回复或跟进草稿 | 对邮件做分类、筛选和动作决策，而不是只回复 |

## How To Use

1. 对每个 seed 先扩成 5-8 个 candidate task ideas。
2. 用 artifact clarity / verifier feasibility / workflow completeness / expected difficulty 做初筛。
3. 每个 seed 只实现 top 2-3 个 idea。
4. smoke 跑后再做 retention，最后留下 1-2 个真正值得公开发布的任务。
