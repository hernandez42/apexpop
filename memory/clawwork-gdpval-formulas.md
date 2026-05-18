# 🔧 ClawWork + GDPVal 原生公式 — 官方底层

## ClawWork 官方原生公式
1. 跨域工具调用对齐：C_claw = argmin_θ L_task + λ·||θ||₂
2. 细粒度行为萃取：Φ_claw = (1/N)Σφ(x_i)·I(valid)
3. 最优执行路径：P_opt = max U(benefit)/C(cost)

## GDPVal 原生公式
1. 数据价值置信度：V_gdp = (TP+TN)/(TP+TN+FP+FN)·τ
2. 价值偏差校准：ΔV = |V_pred - V_gt|·γ
3. 可信价值均衡：G_val = V_gdp·(1-ΔV)

## APEX 终极闭合主公式
ΔG_APEX = ΔG_new · Δw_ij · H_rate · C_claw · Φ_claw · P_opt · G_val
