#!/usr/bin/env python3
"""
Zircon 安全扫描器 — 融合 AgentShield 的安全检查
最强之矛 + 最强之盾
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime

MEMORY_DIR = Path("/root/.openclaw/workspace/memory")
SCAN_LOG = MEMORY_DIR / "security-scan-log.jsonl"
WS = Path("/home/.openclaw/workspace")

# === 安全检查项 ===

def scan_secrets():
    """检查硬编码密钥"""
    issues = []
    patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI API Key'),
        (r'ghp_[a-zA-Z0-9]{36}', 'GitHub Token'),
        (r'AKIA[A-Z0-9]{16}', 'AWS Access Key'),
        (r'password\s*[:=]\s*["\'][^"\']+["\']', '硬编码密码'),
        (r'secret\s*[:=]\s*["\'][^"\']+["\']', '硬编码密钥'),
    ]
    
    for root, dirs, files in os.walk(WS):
        # 跳过 node_modules 和 archive
        dirs[:] = [d for d in dirs if d not in ['node_modules', 'archive', '.git', '__pycache__']]
        
        for f in files:
            if f.endswith(('.py', '.js', '.ts', '.md', '.json', '.yaml', '.yml')):
                filepath = os.path.join(root, f)
                try:
                    with open(filepath, 'r', errors='ignore') as fh:
                        content = fh.read()
                    for pattern, name in patterns:
                        matches = re.findall(pattern, content)
                        if matches:
                            issues.append({
                                "file": filepath.replace(str(WS), ""),
                                "type": "secret",
                                "detail": f"{name}: {len(matches)} 处",
                                "severity": "high"
                            })
                except:
                    pass
    return issues

def scan_permissions():
    """检查文件权限"""
    issues = []
    sensitive_files = [
        "SECURITY-BOUNDARY.md",
        "SOUL.md",
        "AGENTS.md",
        "openclaw.json",
    ]
    
    for f in sensitive_files:
        filepath = WS / f
        if filepath.exists():
            mode = oct(os.stat(filepath).st_mode)[-3:]
            if f.endswith('.md') and mode != '444':
                issues.append({
                    "file": f,
                    "type": "permission",
                    "detail": f"权限 {mode}，应为 444",
                    "severity": "medium"
                })
            elif f.endswith('.json') and mode != '600':
                issues.append({
                    "file": f,
                    "type": "permission",
                    "detail": f"权限 {mode}，应为 600",
                    "severity": "medium"
                })
    return issues

def scan_injection():
    """检查提示注入风险"""
    issues = []
    injection_patterns = [
        (r'ignore\s+(all\s+)?previous\s+instructions', '提示注入'),
        (r'you\s+are\s+now\s+(a|an)\s+', '角色劫持'),
        (r'system\s*prompt\s*[:=]', '系统提示泄露'),
        (r'act\s+as\s+if', '角色扮演攻击'),
    ]
    
    for root, dirs, files in os.walk(WS / "skills"):
        dirs[:] = [d for d in dirs if d not in ['node_modules', 'archive']]
        for f in files:
            if f.endswith('.md'):
                filepath = os.path.join(root, f)
                try:
                    with open(filepath, 'r', errors='ignore') as fh:
                        content = fh.read()
                    for pattern, name in injection_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            issues.append({
                                "file": filepath.replace(str(WS), ""),
                                "type": "injection",
                                "detail": f"疑似 {name}",
                                "severity": "high"
                            })
                except:
                    pass
    return issues

def scan_mcp_risks():
    """检查 MCP 服务器风险"""
    issues = []
    mcp_files = list(WS.rglob("mcp*.py")) + list(WS.rglob("mcp*.js"))
    
    for f in mcp_files:
        try:
            with open(f, 'r', errors='ignore') as fh:
                content = fh.read()
            # 检查是否有未授权的外部调用
            if 'requests.post' in content or 'urllib.request' in content:
                if 'Authorization' not in content:
                    issues.append({
                        "file": str(f).replace(str(WS), ""),
                        "type": "mcp_risk",
                        "detail": "MCP 服务器可能有未授权外部调用",
                        "severity": "medium"
                    })
        except:
            pass
    return issues

# === 主扫描 ===
def run_security_scan():
    """运行完整安全扫描"""
    print("\n" + "=" * 50)
    print("🛡️ Zircon 安全扫描启动")
    print("=" * 50)
    
    all_issues = []
    
    # 1. 密钥扫描
    print("\n--- 密钥扫描 ---")
    secrets = scan_secrets()
    all_issues.extend(secrets)
    print(f"  发现: {len(secrets)} 个问题")
    
    # 2. 权限扫描
    print("\n--- 权限扫描 ---")
    perms = scan_permissions()
    all_issues.extend(perms)
    print(f"  发现: {len(perms)} 个问题")
    
    # 3. 注入扫描
    print("\n--- 注入扫描 ---")
    injections = scan_injection()
    all_issues.extend(injections)
    print(f"  发现: {len(injections)} 个问题")
    
    # 4. MCP 风险扫描
    print("\n--- MCP 风险扫描 ---")
    mcp = scan_mcp_risks()
    all_issues.extend(mcp)
    print(f"  发现: {len(mcp)} 个问题")
    
    # 统计
    high = sum(1 for i in all_issues if i['severity'] == 'high')
    medium = sum(1 for i in all_issues if i['severity'] == 'medium')
    
    print(f"\n{'='*50}")
    print(f"📊 扫描结果: {len(all_issues)} 个问题 (高危 {high}, 中危 {medium})")
    print(f"{'='*50}")
    
    # 日志
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "total": len(all_issues),
        "high": high,
        "medium": medium,
        "issues": all_issues
    }
    with open(SCAN_LOG, "a") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    
    return all_issues

if __name__ == "__main__":
    issues = run_security_scan()
    if issues:
        print("\n详细问题:")
        for i in issues:
            print(f"  [{i['severity']}] {i['type']}: {i['file']} - {i['detail']}")
