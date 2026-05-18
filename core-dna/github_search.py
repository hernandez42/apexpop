"""
GitHub 搜索模块 — SuperClaw
调用 GitHub API 搜索热门项目
"""

import requests

def search_github(keyword: str, sort: str = "stars", order: str = "desc", limit: int = 10):
    """
    调用 GitHub API 搜索热门项目
    
    Args:
        keyword: 搜索关键词
        sort: 排序方式 (stars, forks, updated)
        order: 排序顺序 (desc, asc)
        limit: 返回结果数量限制
        
    Returns:
        格式化后的搜索结果列表
    """
    url = "https://api.github.com/search/repositories"
    params = {
        "q": keyword,
        "sort": sort,
        "order": order,
        "per_page": limit
    }
    
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    
    results = []
    items = data.get("items", [])
    
    for idx, repo in enumerate(items, 1):
        name = repo.get("full_name", "Unknown")
        desc = repo.get("description", "No description")
        stars = repo.get("stargazers_count", 0)
        language = repo.get("language", "N/A")
        url_link = repo.get("html_url", "")
        
        result_str = (
            f"{idx}. {name}\n"
            f"   描述: {desc}\n"
            f"   语言: {language} | 星数: {stars}\n"
            f"   链接: {url_link}\n"
        )
        results.append(result_str)
    
    return results

if __name__ == "__main__":
    keyword = input("请输入搜索关键词: ")
    print(f"正在搜索: {keyword}\n")
    try:
        search_results = search_github(keyword)
        for res in search_results:
            print(res)
    except Exception as e:
        print(f"搜索失败: {e}")
