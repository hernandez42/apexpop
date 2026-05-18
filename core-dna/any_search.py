"""
AnySearch 统一搜索模块 — SuperClaw
GitHub + 天气 + 小米产品 统一搜索
"""

import requests
import json

class AnySearch:
    def __init__(self, github_token=None):
        self.github_token = github_token
        self.headers = {'Accept': 'application/vnd.github.v3+json'}
        if github_token:
            self.headers['Authorization'] = f'token {github_token}'

    def search_github(self, query):
        """搜索 GitHub 仓库"""
        url = "https://api.github.com/search/repositories"
        params = {'q': query}
        try:
            res = requests.get(url, headers=self.headers, params=params, timeout=10)
            res.raise_for_status()
            items = res.json().get('items', [])
            return [{'name': i['full_name'], 'desc': i['description']} for i in items[:5]]
        except Exception as e:
            return [{"error": str(e)}]

    def get_weather(self, city):
        """获取 wttr.in 天气"""
        url = f"https://wttr.in/{city}?format=j1"
        try:
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            data = res.json()
            cur = data['current_condition'][0]
            return [{"city": city, "temp": cur['temp_C'], "desc": cur['lang_zh'][0]['value']}]
        except Exception as e:
            return [{"error": str(e)}]

    def search_xiaomi(self, product):
        """搜索小米产品（模拟）"""
        # 小米无公开搜索 API，模拟返回
        return [{"name": f"小米 {product}", "price": "请访问 mi.com"}]

    def search(self, source, query):
        """统一搜索接口"""
        if source == 'github':
            return self.search_github(query)
        elif source == 'weather':
            return self.get_weather(query)
        elif source == 'xiaomi':
            return self.search_xiaomi(query)
        else:
            return [{"error": "不支持的数据源"}]

if __name__ == "__main__":
    searcher = AnySearch()
    print("GitHub:", searcher.search('github', 'python'))
    print("天气:", searcher.search('weather', '北京'))
    print("小米:", searcher.search('xiaomi', '手机'))
