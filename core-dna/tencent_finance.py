"""
腾讯金融 API 模块 — SuperClaw
获取 A股、港股、美股实时数据
"""

import requests

def get_stock_info(market: str, code: str) -> dict:
    """
    获取股票信息
    
    Args:
        market: 市场类型 (sh=上海, sz=深圳, hk=香港, us=美股)
        code: 股票代码
        
    Returns:
        格式化后的股票信息字典
    """
    base_url = "https://qt.gtimg.cn/q="
    
    if market == "hk":
        request_code = f"hk{code}"
    elif market == "us":
        request_code = f"us{code}"
    else:
        request_code = f"{market}{code}"
    
    url = f"{base_url}{request_code}"
    
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        
        text = response.text
        if '=' in text:
            json_str = text.split('=')[1].strip().rstrip(';')
            parts = json_str.replace('"', '').split('~')
            
            if len(parts) > 32:
                name = parts[1]
                current_price = parts[3]
                change_pct = parts[32]
                
                return {
                    "name": name,
                    "code": parts[2],
                    "price": current_price,
                    "change": change_pct
                }
        
        return {"error": "数据解析失败"}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print("A股:", get_stock_info("sh", "600000"))
    print("港股:", get_stock_info("hk", "00700"))
    print("美股:", get_stock_info("us", "TSLA"))
