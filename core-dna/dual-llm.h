// dual-llm.h — 双 LLM 分工（Long⊗Short 机制）
// 重要决策 → 长思考（问 LLM）
// 简单检查 → 短思考（直接执行）

#ifndef DUAL_LLM_H
#define DUAL_LLM_H

// 判断是否需要长思考
int needs_deep_think(const char *action) {
    // 长思考：涉及代码修改、配置变更、外部调用
    if (strstr(action, "write") || strstr(action, "modify") ||
        strstr(action, "config") || strstr(action, "deploy") ||
        strstr(action, "restart") || strstr(action, "delete")) {
        return 1;
    }
    // 短思考：检查文件、检查进程、读取状态
    return 0;
}

#endif
