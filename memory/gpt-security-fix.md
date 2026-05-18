# GPT 安全+磁盘+审计 修复方案

生成时间: 2026-05-18T12:21:16.601866

## 安全隔离
用 systemd + 最小权限用户 + 网络白名单：

```bash
useradd -r -s /usr/sbin/nologin agent
install -d -o agent -g agent /srv/agent

cat >/etc/systemd/system/agent.service <<'EOF'
[Service]
User=agent;Group=agent;WorkingDirectory=/srv/agent
ExecStart=/usr/bin/python3 /srv/agent/app.py
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/srv/agent/logs
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
IPAddressDeny=any
IPAddressAllow=10.0.0.10
MemoryMax=512M
EOF

systemctl daemon-reload && systemctl enable --now agent
iptables -A OUTPUT -m owner --uid-owner agent -d 10.0.0.10 -j ACCEPT
iptables -A OUTPUT -m owner --uid-owner agent -j DROP
```

可再配合容器/namespace/seccomp 进一步增强隔离。

## 磁盘清理
```bash
# 目标：40G盘从34G(83%)降到≈28G(70%)，先看大文件再清理
df -h /
sudo du -xh / --max-depth=1 2>/dev/null | sort -h
sudo find /var/log -type f -name "*.log" -size +100M -exec ls -lh {} \;

# 安全清理
sudo journalctl --vacuum-time=7d
sudo apt-get clean && sudo apt-get autoremove -y
sudo rm -rf /tmp/* /var/tmp/*
sudo find /var/log -type f -name "*.log" -exec truncate -s 0 {} \;
sudo find / -xdev -type f -size +500M 2>/dev/null | sort

# Docker可选
docker system df
docker system prune -a --volumes -f

df -h /
```
先执行 `du/find` 确认大户；预计可释放 3–8GB。谨慎删除业务数据目录。

## 审计追踪
建议建立统一审计日志，至少记录：  
1. **谁**：用户ID、角色、租户、客户端IP、设备/会话ID、Agent实例ID。  
2. **什么时间**：请求时间、开始/结束时间、时区、耗时。  
3. **做了什么**：操作类型（登录、调用工具、读写数据、审批、策略变更）、目标对象、输入摘要、关联请求ID/任务ID。  
4. **结果如何**：成功/失败、状态码、错误码/异常摘要、输出摘要、影响范围。  
5. **安全字段**：权限校验结果、敏感级别、脱敏标记、审计签名/哈希防篡改。  
要求：JSON结构化、全链路唯一ID、集中存储，支持检索告警，日志最少保留180天。
