"""
天气服务模块 — SuperClaw
调用 wttr.in API 获取天气信息
"""

import requests
from typing import Optional

def get_weather(city: str) -> Optional[str]:
    """
    调用 wttr.in API 获取指定城市的天气信息
    
    Args:
        city: 城市名称（支持中文或英文）
        
    Returns:
        格式化后的天气字符串
    """
    url = f"https://wttr.in/{city}?format=j1"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        current = data['current_condition'][0]
        temp = current['temp_C']
        desc = current['lang_zh'][0]['value'] if 'lang_zh' in current else current['weatherDesc'][0]['value']
        humidity = current['humidity']
        wind_speed = current['windspeedKmph']
        
        return f"【{city}】当前天气：{desc}\n气温：{temp}°C\n湿度：{humidity}%\n风速：{wind_speed} km/h"
    except Exception as e:
        print(f"获取天气失败: {e}")
        return None

if __name__ == "__main__":
    city = input("请输入城市名称: ")
    weather_info = get_weather(city)
    if weather_info:
        print(weather_info)
    else:
        print("未能获取天气信息")
