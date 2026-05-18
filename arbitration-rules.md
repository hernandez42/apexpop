# 仲裁规则 - 人工标记如何转为规则

## 消费链路

```
human-marks.json (你标记)
       ↓
verify-tasks.py (下次运行时读取)
       ↓
标记作为该任务的最终判定 (覆盖自动验证)
       ↓
arbitration-rules.md (累积有效规则)
       ↓
龙虾 HEARTBEAT.md (读取规则,调整行为)
```

## human-marks.json 格式

单条标记:
```json
{"task_id": "xxx", "is_real": true, "note": "其实是完成了，只是产物路径写错了"}
```

批量标记:
```json
[
  {"task_id": "xxx", "is_real": true, "note": "..."},
  {"task_id": "yyy", "is_real": false, "note": "产物是 placeholder"}
]
```

## 自动规则生成

verify-tasks.py 每次运行时:
1. 读取 human-marks.json
2. 将人工标记作为该任务的最终判定
3. 统计: 哪些自动验证判错了 → 写入仲裁日志
4. 如果某类误判反复出现 → 提示需要调整验证逻辑

## 规则持久化

arbitration-rules.md 记录累积的验证经验:
- 哪些类型的任务容易假完成
- 哪些检查项需要调整权重
- 阈值是否需要根据数据调整

## 观察态退出机制

- 进入条件: 假完成率 >= 15%
- 退出条件: 连续 3 次报告假完成率 < 15%
- 人工加速退出: 在 human-marks.json 中标记 "force_exit": true
