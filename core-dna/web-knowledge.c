/**
 * 互联网知识获取模块实现
 * 使用 curl 进行 HTTP 请求
 */

#include "web-knowledge.h"
#include <unistd.h>
#include <sys/wait.h>
#include <errno.h>

static KnowledgeEngine global_engine;

void knowledge_engine_init(KnowledgeEngine *engine) {
    memset(engine, 0, sizeof(KnowledgeEngine));
    engine->max_items = 100;
    engine->item_count = 0;
    engine->last_update = 0;
}

static int run_curl(const char *url, char *buffer, size_t buf_size) {
    char cmd[2048];
    snprintf(cmd, sizeof(cmd), "curl -s -m 30 '%s' 2>/dev/null", url);
    
    FILE *fp = popen(cmd, "r");
    if (!fp) return -1;
    
    size_t n = fread(buffer, 1, buf_size - 1, fp);
    buffer[n] = '\0';
    pclose(fp);
    
    return n > 0 ? 0 : -1;
}

static void add_knowledge(KnowledgeEngine *engine, const char *title, const char *summary, 
                          const char *url, KnowledgeSource source, double relevance) {
    if (engine->item_count >= engine->max_items) {
        // 移除最老的未应用知识
        for (int i = 0; i < engine->item_count - 1; i++) {
            if (!engine->items[i].applied) {
                memmove(&engine->items[i], &engine->items[i+1], 
                        sizeof(KnowledgeItem) * (engine->item_count - i - 1));
                engine->item_count--;
                break;
            }
        }
        if (engine->item_count >= engine->max_items) return;
    }
    
    KnowledgeItem *item = &engine->items[engine->item_count++];
    snprintf(item->id, sizeof(item->id), "k-%ld-%d", (long)time(NULL), engine->item_count);
    strncpy(item->title, title, sizeof(item->title) - 1);
    strncpy(item->summary, summary, sizeof(item->summary) - 1);
    strncpy(item->url, url, sizeof(item->url) - 1);
    item->source = source;
    item->relevance = relevance;
    item->retrieved = time(NULL);
    item->applied = 0;
}

int knowledge_search_arxiv(KnowledgeEngine *engine, const char *query, int max_results) {
    char url[512];
    snprintf(url, sizeof(url), "https://export.arxiv.org/api/query?search_query=%s&max_results=%d", query, max_results);
    
    char buffer[8192];
    if (run_curl(url, buffer, sizeof(buffer)) < 0) {
        return 0;
    }
    
    // 简单解析 XML
    char *ptr = buffer;
    int found = 0;
    
    while (ptr && found < max_results) {
        char *title_start = strstr(ptr, "<title>");
        char *title_end = title_start ? strstr(title_start, "</title>") : NULL;
        char *summary_start = title_end ? strstr(title_end, "<summary>") : NULL;
        char *summary_end = summary_start ? strstr(summary_start, "</summary>") : NULL;
        char *id_start = summary_end ? strstr(summary_end, "<id>") : NULL;
        char *id_end = id_start ? strstr(id_start, "</id>") : NULL;
        
        if (title_start && title_end && summary_start && summary_end && id_start && id_end) {
            char title[512], summary[4096], url[512];
            
            *title_end = '\0';
            strncpy(title, title_start + 7, sizeof(title) - 1);
            
            *summary_end = '\0';
            strncpy(summary, summary_start + 9, sizeof(summary) - 1);
            
            *id_end = '\0';
            strncpy(url, id_start + 4, sizeof(url) - 1);
            
            add_knowledge(engine, title, summary, url, KNOWLEDGE_ARXIV, 0.8);
            found++;
        }
        
        ptr = id_end ? id_end + 5 : NULL;
    }
    
    return found;
}

int knowledge_search_github(KnowledgeEngine *engine, const char *query, int max_results) {
    // GitHub API 搜索（简化版）
    char url[512];
    snprintf(url, sizeof(url), "https://api.github.com/search/repositories?q=%s&per_page=%d", query, max_results);
    
    char buffer[8192];
    if (run_curl(url, buffer, sizeof(buffer)) < 0) {
        return 0;
    }
    
    // 简单解析 JSON
    char *ptr = buffer;
    int found = 0;
    
    while (ptr && found < max_results) {
        char *name_start = strstr(ptr, "\"name\":\"");
        char *name_end = name_start ? strstr(name_start, "\",") : NULL;
        char *desc_start = name_end ? strstr(name_end, "\"description\":\"") : NULL;
        char *desc_end = desc_start ? strstr(desc_start, "\",") : NULL;
        char *url_start = desc_end ? strstr(desc_end, "\"html_url\":\"") : NULL;
        char *url_end = url_start ? strstr(url_start, "\",") : NULL;
        
        if (name_start && name_end && url_start && url_end) {
            char title[512], summary[4096], url[512];
            
            *name_end = '\0';
            strncpy(title, name_start + 8, sizeof(title) - 1);
            
            if (desc_start && desc_end) {
                *desc_end = '\0';
                strncpy(summary, desc_start + 15, sizeof(summary) - 1);
            } else {
                strcpy(summary, "No description");
            }
            
            *url_end = '\0';
            strncpy(url, url_start + 12, sizeof(url) - 1);
            
            add_knowledge(engine, title, summary, url, KNOWLEDGE_GITHUB, 0.7);
            found++;
        }
        
        ptr = url_end ? url_end + 2 : NULL;
    }
    
    return found;
}

int knowledge_search_stackoverflow(KnowledgeEngine *engine, const char *query, int max_results) {
    char url[512];
    snprintf(url, sizeof(url), "https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=votes&q=%s&site=stackoverflow&pagesize=%d", query, max_results);
    
    char buffer[8192];
    if (run_curl(url, buffer, sizeof(buffer)) < 0) {
        return 0;
    }
    
    char *ptr = buffer;
    int found = 0;
    
    while (ptr && found < max_results) {
        char *title_start = strstr(ptr, "\"title\":\"");
        char *title_end = title_start ? strstr(title_start, "\",") : NULL;
        char *link_start = title_end ? strstr(title_end, "\"link\":\"") : NULL;
        char *link_end = link_start ? strstr(link_start, "\",") : NULL;
        
        if (title_start && title_end && link_start && link_end) {
            char title[512], summary[4096], url[512];
            
            *title_end = '\0';
            strncpy(title, title_start + 9, sizeof(title) - 1);
            
            snprintf(summary, sizeof(summary), "Stack Overflow question: %s", title);
            
            *link_end = '\0';
            strncpy(url, link_start + 7, sizeof(url) - 1);
            
            add_knowledge(engine, title, summary, url, KNOWLEDGE_STACKOVERFLOW, 0.6);
            found++;
        }
        
        ptr = link_end ? link_end + 2 : NULL;
    }
    
    return found;
}

KnowledgeItem* knowledge_get_relevant(KnowledgeEngine *engine, const char *topic, int count) {
    static KnowledgeItem results[10];
    int found = 0;
    
    for (int i = 0; i < engine->item_count && found < count; i++) {
        KnowledgeItem *item = &engine->items[i];
        if (!item->applied && 
            (strstr(item->title, topic) || strstr(item->summary, topic) || strstr(item->keywords, topic))) {
            results[found++] = *item;
        }
    }
    
    return found > 0 ? results : NULL;
}

int knowledge_apply(KnowledgeEngine *engine, const char *item_id, double *fitness_gain) {
    for (int i = 0; i < engine->item_count; i++) {
        if (strcmp(engine->items[i].id, item_id) == 0) {
            engine->items[i].applied = 1;
            *fitness_gain = engine->items[i].relevance * 0.1;
            return 0;
        }
    }
    return -1;
}

void knowledge_report(KnowledgeEngine *engine) {
    printf("=== 知识引擎报告 ===\n");
    printf("知识条目数: %d\n", engine->item_count);
    printf("上次更新: %ld\n", engine->last_update);
    
    for (int i = 0; i < engine->item_count; i++) {
        KnowledgeItem *item = &engine->items[i];
        const char *source_names[] = {"arXiv", "GitHub", "StackOverflow", "Blog", "Paper"};
        printf("  [%s] %.2f %s\n", source_names[item->source], item->relevance, item->title);
    }
}

// 全局知识引擎
void global_knowledge_init(void) {
    knowledge_engine_init(&global_engine);
}

KnowledgeEngine* get_global_knowledge_engine(void) {
    return &global_engine;
}