# 自进化论文基因库

## GRAFT-ATHENA (2026-05-11)
- 论文：https://arxiv.org/abs/2605.11117
- 核心：从过去问题学习，自主扩展行动空间
- 机制：因子化决策树 + 相似问题复用
- 启示：C core 需要记录每次进化的"问题-行动-结果"三元组，下次遇到类似问题直接复用
- 落地：✅ C core 增加问题指纹 + 三元组复用

## Multi-Agent Self-Evolution (2026-05)
- 论文：https://arxiv.org/abs/2605.14892
- 核心：失败归因 + 错误传播检测 + 因果依赖分析
- 启示：三层架构需要知道"谁改了什么导致什么后果"
- 落地：✅ 已实现 blame.h（失败归因机制）

## STIR (2026-02)
- 论文：https://arxiv.org/abs/2602.04925
- 核心：把推理内化到隐藏状态，不需要显式思考过程
- 机制：三阶段管道 → 诱导 → 控制基 → 轨迹干预
- 启示：C core 的 LLM 调用可以内化为快速路径，减少 token 消耗
- 落地：✅ 已实现 decision-cache.h（常见决策缓存）

## Huxley-Gödel Machine (2025-10)
- 论文：https://arxiv.org/abs/2510.21614
- 核心：用后代表现评估自修改方向（metaproductivity）
- 启示：每次自修改要验证对后续进化的影响
- 落地：✅ 已实现 post-modify-verify.h（修改后 3 轮验证）

## Long⊗Short (2025-05)
- 论文：https://arxiv.org/abs/2505.11827
- 核心：长思考+短思考双LLM协作，用强化学习实现自演进
- 启示：区分重要思考和次要思考，C core 用 LLM 做重要决策，Rust 做快速执行
- 落地：✅ 已实现 dual-llm.h（长/短思考分工）

## Absolute Zero (2025-05)
- 论文：https://arxiv.org/abs/2505.03335
- 核心：零数据自博弈推理，自己出题→自己解→自己验证
- 机制：Propose（出题）→ Solve（解题）→ Verify（验证）
- 启示：不需要外部数据，纯粹的自博弈就能变强
- 落地：✅ 已实现 absolute-zero.h（C core 自出题自解）

## Token-Superposition Training (2026-05)
- 论文：https://arxiv.org/abs/2605.06546
- 核心：多 token 组合成 bag 训练，提升 2.5x 预训练效率
- 机制：superposition phase + recovery phase
- 启示：LLM 预训练效率优化，可能影响未来模型架构
- 落地：📖 已记录，作为知识储备
