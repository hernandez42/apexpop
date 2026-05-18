# MiMoClaw 双 Agent 循环进化

> 2 个 Agent 后台自动滚动循环进化全模态

## Agent 1: mimoclaw-evolver（进化 Agent）
- 评估 14 维度 → 找瓶颈 → 搜索最佳实践 → 写入日志
- 每次被 cron 唤醒执行

## Agent 2: mimoclaw-devourer（吞噬 Agent）
- 搜索最新 AI 框架 → 提取 insight → 融入 EVOLUTION.md
- 每次被 cron 唤醒执行

## 循环流程
```
cron 触发 → evolver 评估 → devourer 吞噬 → 结果写入 MD → 等待下一轮
```

## 日志位置
- 进化日志: memory/mimoclaw-evolution-log.jsonl
- 吞噬日志: memory/devour-log.jsonl
- 融合结果: EVOLUTION.md
