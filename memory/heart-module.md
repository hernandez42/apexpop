# ❤️ 心脏模块（T：时间常数）

## 定义
T = 时间常数 = 系统响应速度

## 映射
- T 低 = 快速响应 → 效率高
- T 高 = 响应慢 → 稳定性好
- T 过高 → 响应太慢 → 用户体验差

## 实现
def calc_time_constant(response_time, target=1.0):
    return response_time / target

## 应用
- T < 1.0 → 快速响应，效率优先
- T > 2.0 → 响应太慢，需要优化
- T ≈ 1.0 → 最佳平衡
