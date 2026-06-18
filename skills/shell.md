# Shell Skill — 系统命令执行

- 触发词: 执行, 运行, 命令, shell, 执行命令

## 能力描述

使用 shell 工具在系统中执行命令:

`<tool shell><cmd>你的 shell 命令</cmd></tool>`

例如:
- `ls -la` 列目录
- `cat file.txt` 读文件内容
- `git status` 检查 git 状态
- `find . -name "*.py"` 查找文件

## 安全提示

- 不要执行会破坏系统的命令（rm -rf / 等）
- 大任务先 think，再分步执行，每次检查结果
- 对用户请求先理解意图，再行动

## 常见用法

| 需求 | 命令 |
| --- | --- |
| 列出目录 | ls -la |
| 读取文件 | cat 文件路径 |
| 统计代码 | wc -l *.py |
| 搜索内容 | grep -rn "关键词" 路径 |
| 查看进程 | ps aux |
| 磁盘空间 | df -h |
