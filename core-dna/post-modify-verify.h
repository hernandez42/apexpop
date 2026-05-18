// post-modify-verify.h — 自修改后验证（Huxley-Gödel Machine）
// 每次自修改后跑 3 轮进化，看是否持续提升

#ifndef POST_MODIFY_VERIFY_H
#define POST_MODIFY_VERIFY_H

static double pre_modify_fitness = 0.0;
static int post_modify_rounds = 0;

// 记录修改前状态
void pre_modify_check(void) {
    pre_modify_fitness = identity.fitness;
    post_modify_rounds = 0;
}

// 修改后验证：每轮检查适应度是否提升
int post_modify_verify(void) {
    post_modify_rounds++;
    if (post_modify_rounds >= 3) {
        // 3 轮后检查
        if (identity.fitness >= pre_modify_fitness) {
            return 1;  // 验证通过
        } else {
            return -1; // 需要回滚
        }
    }
    return 0;  // 还在验证中
}

#endif
