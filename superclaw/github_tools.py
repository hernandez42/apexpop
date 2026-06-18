"""superclaw GitHub 能力获取工具

让 superclaw 能从 GitHub 搜索、克隆、下载代码，并安装依赖。
这是自进化的能力获取环节 — 零依赖（仅用标准库）。

包含 4 个类：
- GitHubSearcher：搜索 GitHub 仓库 / 代码
- RepoCloner：浅克隆仓库（白名单 https://github.com/）
- FileDownloader：下载单个 raw 文件（限 1MB）
- DependencyInstaller：白名单内 pip 安装
"""
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Set
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

# GitHub 域名白名单
_GITHUB_HTTPS_PREFIX = "https://github.com/"
_RAW_GITHUBUSERCONTENT_PREFIX = "https://raw.githubusercontent.com/"
_GITHUB_RAW_PATTERN = re.compile(r"^https://github\.com/[^/]+/[^/]+/raw/")

# 包名合法字符（防注入）
_PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# 下载文件大小上限：1MB
_MAX_DOWNLOAD_BYTES = 1024 * 1024

# 默认允许安装的包白名单
DEFAULT_ALLOWED_PACKAGES: Set[str] = {
    "requests", "httpx", "aiohttp", "beautifulsoup4", "lxml", "pyyaml", "tomli",
}


class GitHubSearcher:
    """GitHub 搜索器 — 调 GitHub Search API

    无 token 时用匿名 API（限 10 次/分钟）；有 token 时 30 次/分钟。
    所有方法返回结构化结果，不抛异常给上层。
    """

    SEARCH_REPOS_URL = "https://api.github.com/search/repositories"
    SEARCH_CODE_URL = "https://api.github.com/search/code"
    TIMEOUT = 30

    def __init__(self, token: Optional[str] = None):
        # token 优先用显式传入的；为 None 时从环境变量读
        if token is None:
            token = os.environ.get("GITHUB_TOKEN") or None
        self.token = token

    def _build_headers(self) -> dict:
        headers = {"User-Agent": "superclaw/2.0", "Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    def _request(self, url: str) -> dict:
        """发起 GET 请求，返回解析后的 JSON dict。

        出错时返回 {"error": "..."}。
        """
        try:
            req = urllib_request.Request(url, headers=self._build_headers())
            with urllib_request.urlopen(req, timeout=self.TIMEOUT) as resp:  # nosec B310 - URL 由代码构造
                data = resp.read()
                return json.loads(data.decode("utf-8"))
        except HTTPError as e:
            # rate limit: 403 + X-RateLimit-Remaining: 0
            remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
            if e.code == 403 and remaining == "0":
                logger.warning("GitHub API rate limit exceeded")
                return {"error": "GitHub API rate limit exceeded (匿名限 10 次/分钟，请设置 GITHUB_TOKEN)"}
            logger.warning("GitHub API HTTP %s: %s", e.code, e.reason)
            return {"error": f"GitHub API HTTP {e.code}: {e.reason}"}
        except URLError as e:
            logger.warning("GitHub API 网络错误: %s", e)
            return {"error": f"网络错误: {e.reason}"}
        except json.JSONDecodeError as e:
            logger.warning("GitHub API JSON 解析失败: %s", e)
            return {"error": f"JSON 解析失败: {e}"}
        except Exception as e:  # noqa: BLE001 — 兜底，保证不抛异常给上层
            logger.warning("GitHub API 未知错误: %s", e)
            return {"error": f"未知错误: {e}"}

    def search_repos(self, query: str, language: str = "python",
                     limit: int = 5) -> List[dict]:
        """搜索仓库，返回 [{full_name, description, stargazers_count, html_url, clone_url, default_branch}]

        出错时返回 [{"error": "..."}]。
        """
        params = {
            "q": f"{query} language:{language}" if language else query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(limit, 100),
        }
        url = f"{self.SEARCH_REPOS_URL}?{urlencode(params)}"
        data = self._request(url)
        if "error" in data:
            return [data]

        items = data.get("items", [])
        results = []
        for item in items[:limit]:
            results.append({
                "full_name": item.get("full_name", ""),
                "description": item.get("description", "") or "",
                "stargazers_count": item.get("stargazers_count", 0),
                "html_url": item.get("html_url", ""),
                "clone_url": item.get("clone_url", ""),
                "default_branch": item.get("default_branch", "main"),
            })
        return results

    def search_code(self, query: str, language: str = "python",
                    limit: int = 5) -> List[dict]:
        """搜索代码，返回 [{name, path, repository, html_url, download_url}]

        出错时返回 [{"error": "..."}]。
        """
        params = {
            "q": f"{query} language:{language}" if language else query,
            "per_page": min(limit, 100),
        }
        url = f"{self.SEARCH_CODE_URL}?{urlencode(params)}"
        data = self._request(url)
        if "error" in data:
            return [data]

        items = data.get("items", [])
        results = []
        for item in items[:limit]:
            repo = item.get("repository", {})
            repo_full = repo.get("full_name", "") if isinstance(repo, dict) else ""
            html_url = item.get("html_url", "")
            # 构造 download_url：把 /blob/ 替换为 /raw/
            download_url = html_url.replace("/blob/", "/raw/", 1) if html_url else ""
            results.append({
                "name": item.get("name", ""),
                "path": item.get("path", ""),
                "repository": repo_full,
                "html_url": html_url,
                "download_url": download_url,
            })
        return results


class RepoCloner:
    """仓库克隆器 — 浅克隆 GitHub 仓库

    安全：URL 必须以 https://github.com/ 开头（白名单），拒绝其他域名。
    """

    TIMEOUT = 120

    def __init__(self, target_dir: Optional[Path] = None):
        self.target_dir = Path(target_dir) if target_dir else Path("skills/repos")

    def _is_allowed_url(self, url: str) -> bool:
        """URL 必须以 https://github.com/ 开头"""
        return url.startswith(_GITHUB_HTTPS_PREFIX)

    def _derive_name(self, url: str) -> str:
        """从 URL 推导仓库名：https://github.com/user/repo.git -> repo"""
        # 去掉 .git 后缀
        tail = url.rsplit("/", 1)[-1]
        if tail.endswith(".git"):
            tail = tail[:-4]
        return tail or "repo"

    def clone(self, url: str, name: Optional[str] = None) -> Optional[Path]:
        """浅克隆仓库，返回克隆后的目录路径。

        出错时返回 None（不抛异常）。
        """
        # 安全：URL 白名单校验
        if not self._is_allowed_url(url):
            logger.warning("拒绝克隆非 GitHub URL: %s", url)
            return None

        repo_name = name or self._derive_name(url)
        target = self.target_dir / repo_name

        # 目录已存在则不覆盖
        if target.exists():
            logger.warning("克隆目标已存在，跳过: %s", target)
            return None

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(  # nosec B603 — URL 已白名单校验
                ["git", "clone", "--depth", "1", url, str(target)],
                timeout=self.TIMEOUT,
                capture_output=True,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="ignore") if result.stderr else ""
                logger.warning("git clone 失败 (exit=%s): %s", result.returncode, stderr.strip())
                # 清理可能残留的空目录
                if target.exists() and not any(target.iterdir()):
                    target.rmdir()
                return None
            return target
        except subprocess.TimeoutExpired:
            logger.warning("git clone 超时 (%ss): %s", self.TIMEOUT, url)
            return None
        except FileNotFoundError:
            logger.warning("git 命令不存在，无法克隆")
            return None
        except Exception as e:  # noqa: BLE001 — 兜底
            logger.warning("git clone 未知错误: %s", e)
            return None


class FileDownloader:
    """文件下载器 — 从 GitHub raw URL 下载单个文件

    安全：URL 必须匹配 raw.githubusercontent.com 或 github.com/*/raw/ 格式。
    限制文件大小 1MB。
    """

    TIMEOUT = 30
    MAX_BYTES = _MAX_DOWNLOAD_BYTES

    def _is_allowed_url(self, url: str) -> bool:
        """URL 必须是 GitHub raw 格式"""
        if url.startswith(_RAW_GITHUBUSERCONTENT_PREFIX):
            return True
        if _GITHUB_RAW_PATTERN.match(url):
            return True
        return False

    def download_raw(self, url: str, target_path: Path) -> Optional[Path]:
        """下载单个文件到 target_path，返回该路径。

        出错时返回 None（不抛异常）。
        """
        # 安全：URL 格式校验
        if not self._is_allowed_url(url):
            logger.warning("拒绝下载非 GitHub raw URL: %s", url)
            return None

        target = Path(target_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            req = urllib_request.Request(url, headers={"User-Agent": "superclaw/2.0"})
            with urllib_request.urlopen(req, timeout=self.TIMEOUT) as resp:  # nosec B310 - URL 已白名单
                data = resp.read()
            # 文件大小限制
            if len(data) > self.MAX_BYTES:
                logger.warning("文件过大 (%d > %d bytes)，拒绝下载: %s",
                               len(data), self.MAX_BYTES, url)
                return None
            target.write_bytes(data)
            return target
        except URLError as e:
            logger.warning("下载网络错误: %s — %s", url, e)
            return None
        except HTTPError as e:
            logger.warning("下载 HTTP 错误 %s: %s", e.code, url)
            return None
        except Exception as e:  # noqa: BLE001 — 兜底
            logger.warning("下载未知错误: %s — %s", url, e)
            return None


class DependencyInstaller:
    """依赖安装器 — 白名单内 pip 安装

    安全：包名必须匹配 ^[a-zA-Z0-9_-]+$（防注入），必须在白名单内。
    """

    TIMEOUT = 120

    def __init__(self, allowed_packages: Optional[Set[str]] = None):
        if allowed_packages is None:
            self.allowed_packages = DEFAULT_ALLOWED_PACKAGES
        else:
            self.allowed_packages = set(allowed_packages)

    def _is_valid_package_name(self, package: str) -> bool:
        """包名必须匹配 ^[a-zA-Z0-9_-]+$"""
        return bool(_PACKAGE_NAME_RE.match(package))

    def _normalize_name(self, line: str) -> str:
        """从 requirements.txt 一行中提取包名（剥离版本说明符）"""
        # 去注释
        line = line.split("#", 1)[0].strip()
        if not line:
            return ""
        # 剥离版本说明符：requests>=2.0, httpx==0.24, lxml~=4.0 等
        for sep in [">=", "==", "~=", "<=", ">", "<", "!=", ";"]:
            if sep in line:
                line = line.split(sep, 1)[0].strip()
        return line

    def install(self, package: str) -> bool:
        """安装单个包，返回成功与否。

        包名必须合法且在白名单内。
        """
        # 安全：包名注入防护
        if not self._is_valid_package_name(package):
            logger.warning("拒绝安装非法包名: %r", package)
            return False
        # 安全：白名单检查
        if package not in self.allowed_packages:
            logger.warning("拒绝安装非白名单包: %s", package)
            return False

        try:
            result = subprocess.run(  # nosec B603 — 包名已校验
                [sys.executable, "-m", "pip", "install", package],
                timeout=self.TIMEOUT,
                capture_output=True,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="ignore") if result.stderr else ""
                logger.warning("pip install %s 失败 (exit=%s): %s",
                               package, result.returncode, stderr.strip())
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning("pip install %s 超时 (%ss)", package, self.TIMEOUT)
            return False
        except FileNotFoundError:
            logger.warning("pip 不可用")
            return False
        except Exception as e:  # noqa: BLE001 — 兜底
            logger.warning("pip install %s 未知错误: %s", package, e)
            return False

    def install_requirements(self, requirements_path: Path) -> bool:
        """读 requirements.txt，逐个检查白名单后安装。

        所有包都成功安装返回 True；有任一包被跳过或失败返回 False。
        """
        path = Path(requirements_path)
        if not path.exists():
            logger.warning("requirements.txt 不存在: %s", path)
            return False

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.warning("读取 requirements.txt 失败: %s", e)
            return False

        all_ok = True
        for line in content.splitlines():
            name = self._normalize_name(line)
            if not name:
                continue
            if not self.install(name):
                all_ok = False
        return all_ok
