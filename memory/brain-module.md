# 🧠 大脑模块（H：认知负荷）

## 定义
H = 认知负荷 = 当前处理任务的认知压力

## 映射
- H 低 = 轻松处理 → 可以做更多任务
- H 高 = 压力大 → 应该减少任务
- H 过高 → 系统崩溃 → 需要休息

## 实现
def calc_cognitive_load(active_tasks, max_capacity=10):
    return active_tasks / max_capacity

## 应用
- H > 0.8 → 停止接受新任务
- H > 0.6 → 降低任务优先级
- H < 0.3 → 可以增加任务
