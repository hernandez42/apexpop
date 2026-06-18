"""测试 superclaw.github_tools — GitHub 能力获取工具

覆盖：GitHubSearcher / RepoCloner / FileDownloader / DependencyInstaller
以及 build_default_tools(github=True) 集成。
全部 mock，不真连 GitHub。
"""
import io
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.github_tools import (
    DEFAULT_ALLOWED_PACKAGES,
    DependencyInstaller,
    FileDownloader,
    GitHubSearcher,
    RepoCloner,
)
from superclaw.tools import build_default_tools


# ============================================================
# 辅助：Fake HTTP 响应 / Fake subprocess 结果
# ============================================================

class _FakeResponse:
    """模拟 urllib 的 HTTP 响应（context manager）"""

    def __init__(self, data, status=200, headers=None):
        self._data = data if isinstance(data, bytes) else data.encode("utf-8")
        self.status = status
        self.code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data

    def getcode(self):
        return self.status

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


def _make_http_error(code, reason, headers, body=b""):
    """构造一个 urllib HTTPError 实例"""
    return HTTPError(
        url="https://api.github.com",
        code=code,
        msg=reason,
        hdrs=headers,
        fp=io.BytesIO(body),
    )


class _FakeCompletedProcess:
    """模拟 subprocess.run 的返回值"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ============================================================
# GitHubSearcher
# ============================================================

class TestGitHubSearcherInit:
    def test_init_reads_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "env-token-123")
        s = GitHubSearcher()
        assert s.token == "env-token-123"

    def test_init_explicit_token_overrides_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        s = GitHubSearcher(token="explicit-token")
        assert s.token == "explicit-token"

    def test_init_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        s = GitHubSearcher()
        assert s.token is None

    def test_init_explicit_none_uses_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "from-env")
        s = GitHubSearcher(token=None)
        assert s.token == "from-env"


class TestSearchRepos:
    def test_search_repos_success(self, monkeypatch):
        """成功搜索仓库：验证请求 URL/headers 与响应解析"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        captured = {}

        payload = {
            "total_count": 1,
            "items": [
                {
                    "full_name": "octocat/Hello-World",
                    "description": "My first repo",
                    "stargazers_count": 100,
                    "html_url": "https://github.com/octocat/Hello-World",
                    "clone_url": "https://github.com/octocat/Hello-World.git",
                    "default_branch": "main",
                }
            ],
        }

        def _fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            captured["headers"] = req.headers
            captured["timeout"] = timeout
            return _FakeResponse(__import__("json").dumps(payload))

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_repos("hello world", language="python", limit=5)

        assert len(results) == 1
        r = results[0]
        assert r["full_name"] == "octocat/Hello-World"
        assert r["description"] == "My first repo"
        assert r["stargazers_count"] == 100
        assert r["html_url"] == "https://github.com/octocat/Hello-World"
        assert r["clone_url"] == "https://github.com/octocat/Hello-World.git"
        assert r["default_branch"] == "main"
        # 验证请求 URL 指向 search/repositories
        assert "api.github.com/search/repositories" in captured["url"]
        assert "hello+world" in captured["url"] or "hello%20world" in captured["url"]
        assert captured["timeout"] == 30

    def test_search_repos_with_token_sends_auth_header(self, monkeypatch):
        """有 token 时应在请求头加 Authorization"""
        captured = {}

        def _fake_urlopen(req, timeout=30):
            captured["auth"] = req.get_header("Authorization")
            captured["ua"] = req.get_header("User-agent")
            return _FakeResponse('{"total_count": 0, "items": []}')

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher(token="ghp_secret_token")
        results = s.search_repos("test")

        assert captured["auth"] == "token ghp_secret_token"
        assert results == []

    def test_search_repos_no_token_no_auth_header(self, monkeypatch):
        """无 token 时不应发送 Authorization 头"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        captured = {}

        def _fake_urlopen(req, timeout=30):
            captured["auth"] = req.get_header("Authorization")
            return _FakeResponse('{"total_count": 0, "items": []}')

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        s.search_repos("test")
        assert captured["auth"] is None

    def test_search_repos_rate_limit(self, monkeypatch):
        """403 + X-RateLimit-Remaining:0 应返回 rate limit 错误"""
        def _fake_urlopen(req, timeout=30):
            raise _make_http_error(
                403, "Forbidden",
                {"X-RateLimit-Remaining": "0"},
                body=b'{"message": "API rate limit exceeded"}',
            )

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_repos("test")
        # 返回结构化错误（不抛异常）
        assert isinstance(results, list)
        assert len(results) == 1
        assert "error" in results[0]
        assert "rate" in results[0]["error"].lower() or "limit" in results[0]["error"].lower()

    def test_search_repos_network_error(self, monkeypatch):
        """网络错误应被捕获，返回结构化错误"""
        def _fake_urlopen(req, timeout=30):
            raise URLError("network down")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_repos("test")
        assert isinstance(results, list)
        assert len(results) == 1
        assert "error" in results[0]

    def test_search_repos_json_error(self, monkeypatch):
        """损坏的 JSON 应被捕获"""
        def _fake_urlopen(req, timeout=30):
            return _FakeResponse("not valid json {{{")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_repos("test")
        assert isinstance(results, list)
        assert len(results) == 1
        assert "error" in results[0]

    def test_search_repos_respects_limit(self, monkeypatch):
        """limit 参数应传入 per_page"""
        captured = {}

        def _fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            return _FakeResponse('{"total_count": 0, "items": []}')

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        s.search_repos("test", limit=3)
        assert "per_page=3" in captured["url"]


class TestSearchCode:
    def test_search_code_success(self, monkeypatch):
        """成功搜索代码：验证响应解析"""
        payload = {
            "total_count": 1,
            "items": [
                {
                    "name": "main.py",
                    "path": "src/main.py",
                    "repository": {"full_name": "octocat/repo"},
                    "html_url": "https://github.com/octocat/repo/blob/main/src/main.py",
                }
            ],
        }

        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(__import__("json").dumps(payload))

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_code("def main", language="python", limit=5)

        assert len(results) == 1
        r = results[0]
        assert r["name"] == "main.py"
        assert r["path"] == "src/main.py"
        assert r["repository"] == "octocat/repo"
        assert r["html_url"] == "https://github.com/octocat/repo/blob/main/src/main.py"

    def test_search_code_url(self, monkeypatch):
        """验证请求指向 search/code"""
        captured = {}

        def _fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            return _FakeResponse('{"total_count": 0, "items": []}')

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        s.search_code("import os")
        assert "api.github.com/search/code" in captured["url"]

    def test_search_code_rate_limit(self, monkeypatch):
        def _fake_urlopen(req, timeout=30):
            raise _make_http_error(
                403, "Forbidden", {"X-RateLimit-Remaining": "0"},
                body=b"rate limited",
            )

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_code("test")
        assert len(results) == 1
        assert "error" in results[0]

    def test_search_code_network_error(self, monkeypatch):
        def _fake_urlopen(req, timeout=30):
            raise URLError("timeout")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        s = GitHubSearcher()
        results = s.search_code("test")
        assert len(results) == 1
        assert "error" in results[0]


# ============================================================
# RepoCloner
# ============================================================

class TestRepoCloner:
    def test_clone_success(self, tmp_path, monkeypatch):
        """成功克隆：验证 git clone --depth 1 命令"""
        captured = {}

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["timeout"] = kw.get("timeout")
            captured["capture"] = kw.get("capture_output")
            # 模拟 git 创建目录
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "README.md").write_text("cloned", encoding="utf-8")
            return _FakeCompletedProcess(returncode=0, stdout="", stderr="")

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/octocat/Hello-World.git")

        assert result is not None
        assert result.exists()
        # 验证命令是 git clone --depth 1
        assert captured["cmd"][0] == "git"
        assert captured["cmd"][1] == "clone"
        assert "--depth" in captured["cmd"]
        assert "1" in captured["cmd"]
        assert captured["cmd"][-2] == "https://github.com/octocat/Hello-World.git"
        assert captured["timeout"] == 120
        assert captured["capture"] is True

    def test_clone_with_custom_name(self, tmp_path, monkeypatch):
        """指定 name 时克隆到该子目录"""
        captured = {}

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/octocat/Hello-World.git", name="my-repo")

        assert result == tmp_path / "my-repo"
        assert "my-repo" in captured["cmd"][-1]

    def test_clone_url_whitelist_github_accepted(self, tmp_path, monkeypatch):
        """https://github.com/ 开头的 URL 通过白名单"""
        def _fake_run(cmd, **kw):
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/user/repo.git")
        assert result is not None

    def test_clone_url_whitelist_rejects_other_domain(self, tmp_path, monkeypatch):
        """非 github.com 域名应被拒绝"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        # 恶意域名
        result = cloner.clone("https://evil.com/user/repo.git")
        assert result is None
        assert called["count"] == 0  # 不应调用 git

    def test_clone_url_whitelist_rejects_git_protocol(self, tmp_path, monkeypatch):
        """git:// 协议应被拒绝"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("git://github.com/user/repo.git")
        assert result is None
        assert called["count"] == 0

    def test_clone_url_whitelist_rejects_ssh(self, tmp_path, monkeypatch):
        """SSH URL 应被拒绝（仅允许 https://github.com/）"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("git@github.com:user/repo.git")
        assert result is None
        assert called["count"] == 0

    def test_clone_directory_already_exists(self, tmp_path, monkeypatch):
        """目标目录已存在时应返回 None（不覆盖）"""
        existing = tmp_path / "Hello-World"
        existing.mkdir()
        (existing / "existing.txt").write_text("old", encoding="utf-8")

        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/octocat/Hello-World.git")
        assert result is None
        assert called["count"] == 0  # 不应调用 git
        # 原文件未被覆盖
        assert (existing / "existing.txt").read_text() == "old"

    def test_clone_timeout(self, tmp_path, monkeypatch):
        """克隆超时应被捕获"""
        import subprocess

        def _fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/user/repo.git")
        assert result is None

    def test_clone_git_not_found(self, tmp_path, monkeypatch):
        """git 命令不存在（FileNotFoundError）应被捕获"""
        import subprocess

        def _fake_run(cmd, **kw):
            raise FileNotFoundError("git not installed")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/user/repo.git")
        assert result is None

    def test_clone_git_failure(self, tmp_path, monkeypatch):
        """git 返回非零退出码应返回 None"""
        import subprocess

        def _fake_run(cmd, **kw):
            return _FakeCompletedProcess(
                returncode=128, stderr="fatal: repository not found"
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)

        cloner = RepoCloner(target_dir=tmp_path)
        result = cloner.clone("https://github.com/user/nonexistent.git")
        assert result is None

    def test_clone_default_target_dir(self, monkeypatch):
        """未指定 target_dir 时默认 skills/repos/"""
        import subprocess
        captured = {}

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            return _FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # 使用 chdir 到临时目录避免污染真实 skills/repos
        try:
            cloner = RepoCloner()
            assert cloner.target_dir == Path("skills/repos")
        finally:
            pass


# ============================================================
# FileDownloader
# ============================================================

class TestFileDownloader:
    def test_download_raw_success(self, tmp_path, monkeypatch):
        """成功下载文件"""
        content = b"print('hello world')"

        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(content)

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "main.py"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://raw.githubusercontent.com/user/repo/main/main.py", target
        )

        assert result == target
        assert target.read_bytes() == content

    def test_download_raw_url_raw_githubusercontent_accepted(self, tmp_path, monkeypatch):
        """raw.githubusercontent.com URL 通过"""
        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(b"data")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "f.py"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://raw.githubusercontent.com/user/repo/main/f.py", target
        )
        assert result == target

    def test_download_raw_url_github_raw_accepted(self, tmp_path, monkeypatch):
        """github.com/*/raw/ URL 通过"""
        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(b"data")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "f.py"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://github.com/user/repo/raw/main/f.py", target
        )
        assert result == target

    def test_download_raw_invalid_url_rejected(self, tmp_path, monkeypatch):
        """非 GitHub raw URL 应被拒绝"""
        called = {"count": 0}

        def _fake_urlopen(req, timeout=30):
            called["count"] += 1
            return _FakeResponse(b"data")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "f.py"
        dl = FileDownloader()
        result = dl.download_raw("https://evil.com/file.py", target)
        assert result is None
        assert called["count"] == 0
        assert not target.exists()

    def test_download_raw_file_size_limit(self, tmp_path, monkeypatch):
        """超过 1MB 的文件应被拒绝"""
        big_data = b"x" * (1024 * 1024 + 1)  # 1MB + 1

        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(big_data)

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "big.bin"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://raw.githubusercontent.com/user/repo/main/big.bin", target
        )
        assert result is None
        assert not target.exists()

    def test_download_raw_exactly_1mb_allowed(self, tmp_path, monkeypatch):
        """正好 1MB 应允许"""
        data = b"x" * (1024 * 1024)  # 正好 1MB

        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(data)

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "exact.bin"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://raw.githubusercontent.com/user/repo/main/exact.bin", target
        )
        assert result == target
        assert target.read_bytes() == data

    def test_download_raw_network_error(self, tmp_path, monkeypatch):
        """网络错误应被捕获"""
        def _fake_urlopen(req, timeout=30):
            raise URLError("connection refused")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "f.py"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://raw.githubusercontent.com/user/repo/main/f.py", target
        )
        assert result is None
        assert not target.exists()

    def test_download_raw_creates_parent_dirs(self, tmp_path, monkeypatch):
        """目标路径的父目录不存在时应自动创建"""
        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(b"data")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        target = tmp_path / "sub" / "dir" / "f.py"
        dl = FileDownloader()
        result = dl.download_raw(
            "https://raw.githubusercontent.com/user/repo/main/f.py", target
        )
        assert result == target
        assert target.exists()


# ============================================================
# DependencyInstaller
# ============================================================

class TestDependencyInstaller:
    def test_default_whitelist(self):
        """默认白名单应包含指定包"""
        installer = DependencyInstaller()
        for pkg in ["requests", "httpx", "aiohttp", "beautifulsoup4", "lxml",
                    "pyyaml", "tomli"]:
            assert pkg in installer.allowed_packages
        assert installer.allowed_packages is DEFAULT_ALLOWED_PACKAGES

    def test_custom_whitelist(self):
        """自定义白名单"""
        installer = DependencyInstaller(allowed_packages={"numpy", "pandas"})
        assert installer.allowed_packages == {"numpy", "pandas"}
        assert "requests" not in installer.allowed_packages

    def test_install_success(self, monkeypatch):
        """白名单内包安装成功"""
        captured = {}

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["timeout"] = kw.get("timeout")
            captured["capture"] = kw.get("capture_output")
            return _FakeCompletedProcess(returncode=0, stdout="Successfully installed")

        import subprocess
        import sys as _sys
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("requests")

        assert ok is True
        assert captured["cmd"][0] == _sys.executable
        assert captured["cmd"][1] == "-m"
        assert captured["cmd"][2] == "pip"
        assert captured["cmd"][3] == "install"
        assert captured["cmd"][4] == "requests"
        assert captured["timeout"] == 120
        assert captured["capture"] is True

    def test_install_rejects_non_whitelisted(self, monkeypatch):
        """非白名单包应被拒绝"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("malicious-package")
        assert ok is False
        assert called["count"] == 0

    def test_install_rejects_injection_semicolon(self, monkeypatch):
        """包名注入防护：分号"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("requests; rm -rf /")
        assert ok is False
        assert called["count"] == 0

    def test_install_rejects_injection_space(self, monkeypatch):
        """包名注入防护：空格（额外参数）"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("requests --user")
        assert ok is False
        assert called["count"] == 0

    def test_install_rejects_injection_slash(self, monkeypatch):
        """包名注入防护：斜杠"""
        called = {"count": 0}

        def _fake_run(cmd, **kw):
            called["count"] += 1
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("../evil/path")
        assert ok is False
        assert called["count"] == 0

    def test_install_pip_failure(self, monkeypatch):
        """pip 返回非零退出码应返回 False"""
        import subprocess

        def _fake_run(cmd, **kw):
            return _FakeCompletedProcess(
                returncode=1, stderr="ERROR: Could not find package"
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("requests")
        assert ok is False

    def test_install_timeout(self, monkeypatch):
        """pip 超时应被捕获"""
        import subprocess

        def _fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install("requests")
        assert ok is False

    def test_install_requirements_success(self, tmp_path, monkeypatch):
        """安装 requirements.txt 中白名单内的包"""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "requests\nhttpx\n# comment line\nlxml\n\n",
            encoding="utf-8",
        )

        installed = []

        def _fake_run(cmd, **kw):
            installed.append(cmd[-1])
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install_requirements(req_file)

        assert ok is True
        assert "requests" in installed
        assert "httpx" in installed
        assert "lxml" in installed
        # 注释和空行不应被安装
        assert "# comment line" not in installed

    def test_install_requirements_rejects_non_whitelisted(self, tmp_path, monkeypatch):
        """requirements.txt 含非白名单包时应拒绝该包"""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "requests\nevil-package\n",
            encoding="utf-8",
        )

        installed = []

        def _fake_run(cmd, **kw):
            installed.append(cmd[-1])
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        ok = installer.install_requirements(req_file)

        # requests 应安装，evil-package 应跳过；整体返回 False（有跳过项）
        assert "requests" in installed
        assert "evil-package" not in installed
        assert ok is False

    def test_install_requirements_strips_version_specifiers(self, tmp_path, monkeypatch):
        """requirements.txt 中的版本说明符应被剥离后检查白名单"""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "requests>=2.0\nhttpx==0.24\n",
            encoding="utf-8",
        )

        installed = []

        def _fake_run(cmd, **kw):
            installed.append(cmd[-1])
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        installer = DependencyInstaller()
        installer.install_requirements(req_file)
        # 应安装 requests 和 httpx（版本说明符剥离后通过白名单）
        assert "requests" in installed
        assert "httpx" in installed

    def test_install_requirements_nonexistent_file(self, tmp_path):
        """requirements.txt 不存在应返回 False"""
        installer = DependencyInstaller()
        ok = installer.install_requirements(tmp_path / "nope.txt")
        assert ok is False


# ============================================================
# 集成测试 — build_default_tools(github=True)
# ============================================================

class TestBuildDefaultToolsIntegration:
    def test_github_tools_registered_when_enabled(self, tmp_workspace):
        """github=True 时应注册 4 个新工具"""
        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=True
        )
        names = tools.names
        assert "github_search" in names
        assert "github_clone" in names
        assert "github_download" in names
        assert "pip_install" in names

    def test_github_tools_not_registered_when_disabled(self, tmp_workspace):
        """github=False（默认）时不应注册 github 工具"""
        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=False
        )
        names = tools.names
        assert "github_search" not in names
        assert "github_clone" not in names
        assert "github_download" not in names
        assert "pip_install" not in names

    def test_github_tools_default_disabled(self, tmp_workspace):
        """默认（不传 github）时不应注册 github 工具"""
        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False
        )
        names = tools.names
        assert "github_search" not in names

    def test_github_tools_have_descriptions(self, tmp_workspace):
        """github 工具应有描述和参数 schema"""
        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=True
        )
        for name in ["github_search", "github_clone", "github_download", "pip_install"]:
            desc = tools.get_description(name)
            assert desc and len(desc) > 0
            params = tools.get_params(name)
            assert isinstance(params, dict)

    def test_github_search_tool_call(self, tmp_workspace, monkeypatch):
        """调用 github_search 工具应触发 GitHubSearcher.search_repos"""
        payload = {
            "total_count": 1,
            "items": [
                {
                    "full_name": "user/repo",
                    "description": "a repo",
                    "stargazers_count": 10,
                    "html_url": "https://github.com/user/repo",
                    "clone_url": "https://github.com/user/repo.git",
                    "default_branch": "main",
                }
            ],
        }

        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(__import__("json").dumps(payload))

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=True
        )
        result = tools.call("github_search", query="test")
        assert result.error is False
        assert "user/repo" in result.content

    def test_pip_install_tool_call(self, tmp_workspace, monkeypatch):
        """调用 pip_install 工具应触发 DependencyInstaller.install"""
        def _fake_run(cmd, **kw):
            return _FakeCompletedProcess(returncode=0, stdout="installed")

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=True
        )
        result = tools.call("pip_install", package="requests")
        assert result.error is False
        assert "requests" in result.content or "成功" in result.content or "True" in result.content

    def test_github_clone_tool_call(self, tmp_workspace, monkeypatch):
        """调用 github_clone 工具应触发 RepoCloner.clone"""
        def _fake_run(cmd, **kw):
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            return _FakeCompletedProcess(returncode=0)

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=True
        )
        result = tools.call(
            "github_clone",
            url="https://github.com/user/repo.git",
        )
        assert result.error is False

    def test_github_download_tool_call(self, tmp_workspace, monkeypatch):
        """调用 github_download 工具应触发 FileDownloader.download_raw"""
        def _fake_urlopen(req, timeout=30):
            return _FakeResponse(b"file content")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        tools = build_default_tools(
            str(tmp_workspace), shell=False, file_tools=False,
            web=False, think=False, github=True
        )
        result = tools.call(
            "github_download",
            url="https://raw.githubusercontent.com/user/repo/main/f.py",
            target_path=str(tmp_workspace / "f.py"),
        )
        assert result.error is False
        assert (tmp_workspace / "f.py").exists()
