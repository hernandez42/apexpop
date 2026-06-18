"""
飞书/Lark 渠道 — 移植自 nanobot/channels/feishu.py

基于 lark-oapi SDK 的 WebSocket 长连接实现。
需要安装: pip install lark-oapi

配置示例 (config.json):
{
    "channels": {
        "feishu": {
            "enabled": true,
            "app_id": "cli_xxxx",
            "app_secret": "xxxx",
            "allow_from": ["*"],
            "streaming": false
        }
    }
}
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from typing import Any, Dict, Optional

from .base import BaseChannel
from .bus import MessageBus
from .events import OutboundMessage

logger = logging.getLogger(__name__)

# 检测 lark-oapi 是否安装
FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None

# 消息类型显示映射
MSG_TYPE_MAP = {
    "image": "[图片]",
    "audio": "[语音]",
    "file": "[文件]",
    "sticker": "[表情]",
    "post": "[富文本]",
    "share_chat": "[分享群名片]",
    "share_user": "[分享个人名片]",
}


class FeishuChannel(BaseChannel):
    """飞书/Lark 渠道

    使用 lark-oapi SDK 的 WebSocket 客户端接收消息，
    通过 REST API 发送消息。

    需要:
        pip install lark-oapi
    配置:
        app_id: 飞书应用 App ID
        app_secret: 飞书应用 App Secret
    """

    name = "feishu"
    display_name = "飞书"
    send_progress = True
    show_reasoning = True

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self.app_id = self._get_config("app_id", "")
        self.app_secret = self._get_config("app_secret", "")
        self._lark = None
        self._ws_client: Any = None
        self._tenant_access_token = ""

    def _get_config(self, key: str, default: Any = "") -> Any:
        """从配置中读取值"""
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _load_lark(self):
        """延迟加载 lark-oapi SDK"""
        if not FEISHU_AVAILABLE:
            raise ImportError(
                "lark-oapi 未安装。请运行: pip install lark-oapi"
            )

        import lark_oapi as lark
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

        self._lark = lark
        self._feishu_domain = FEISHU_DOMAIN
        self._lark_domain = LARK_DOMAIN
        return lark

    async def start(self) -> None:
        """启动飞书 WebSocket 长连接"""
        if not self.app_id or not self.app_secret:
            logger.error("[Feishu] app_id 或 app_secret 未配置")
            return

        try:
            lark = self._load_lark()
        except ImportError as e:
            logger.error(f"[Feishu] {e}")
            return

        from lark_oapi.ws.client import Client as WsClient

        # 构建事件处理器
        event_handler = self._build_event_handler()

        # 创建 WebSocket 客户端
        self._ws_client = WsClient(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        self._running = True
        logger.info(f"[Feishu] ✅ 飞书渠道启动 (app_id={self.app_id[:8]}...)")

        # 启动 WebSocket 连接（阻塞）
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._ws_client.start
            )
        except asyncio.CancelledError:
            logger.info("[Feishu] WebSocket 连接已取消")
        except Exception as e:
            logger.error(f"[Feishu] WebSocket 错误: {e}")

    def _build_event_handler(self):
        """构建飞书事件处理器"""
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        def on_message_receive(data: P2ImMessageReceiveV1) -> None:
            """处理收到的消息事件"""
            try:
                asyncio.create_task(self._process_message_event(data))
            except RuntimeError:
                # 没有事件循环时同步处理
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._process_message_event(data))
                loop.close()

        # 注册事件处理器
        from lark_oapi import EventDispatcherHandler

        handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message_receive)
            .build()
        )
        return handler

    async def _process_message_event(self, data) -> None:
        """处理消息事件"""
        try:
            event = data.event
            msg = event.message
            sender = event.sender

            sender_id = sender.sender_id.open_id
            chat_id = msg.chat_id
            msg_type = msg.message_type

            # 只处理文本消息
            if msg_type != "text":
                content_str = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
                await self._handle_message(
                    sender_id=sender_id,
                    chat_id=chat_id,
                    content=content_str,
                    is_dm=(msg.chat_type == "p2p"),
                )
                return

            # 解析文本内容
            content = json.loads(msg.content)
            text = content.get("text", "").strip()

            if not text:
                return

            # 处理 @机器人 的情况
            mentions = msg.mentions or []
            for mention in mentions:
                if mention.id == self.app_id:
                    # 移除 @机器人 的文本
                    text = text.replace(f"@{_get_mention_name(mention)}", "").strip()

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=text,
                is_dm=(msg.chat_type == "p2p"),
            )

        except Exception as e:
            logger.error(f"[Feishu] 消息处理错误: {e}")

    async def stop(self) -> None:
        """停止飞书渠道"""
        self._running = False
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception:
                pass
        logger.info("[Feishu] 渠道已停止")

    async def send(self, msg: OutboundMessage) -> None:
        """通过飞书 API 发送消息"""
        if not self._lark:
            logger.error("[Feishu] SDK 未初始化")
            return

        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            # 获取 tenant_access_token
            token = await self._get_tenant_token()
            if not token:
                logger.error("[Feishu] 获取 tenant_access_token 失败")
                return

            # 构建请求
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(msg.chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": msg.content}))
                    .build()
                )
                .build()
            )

            # 发送
            response = self._lark.Client.builder() \
                .app_id(self.app_id) \
                .app_secret(self.app_secret) \
                .build() \
                .im.v1.message.create(request)

            if not response.success():
                logger.error(f"[Feishu] 发送失败: {response.msg}")
            else:
                logger.debug(f"[Feishu] 消息已发送到 {msg.chat_id}")

        except Exception as e:
            logger.error(f"[Feishu] 发送错误: {e}")

    async def send_delta(self, chat_id: str, delta: str,
                         metadata: Optional[Dict[str, Any]] = None) -> None:
        """流式发送（飞书通过更新消息实现，简化版直接发送）"""
        # 飞书流式需要先创建消息再更新，这里简化为直接发送
        await self.send(OutboundMessage(
            channel=self.name,
            chat_id=chat_id,
            content=delta,
        ))

    async def _get_tenant_token(self) -> str:
        """获取 tenant_access_token"""
        if self._tenant_access_token:
            return self._tenant_access_token

        try:
            import urllib.request
            import urllib.error

            url = f"{self._feishu_domain}/open-apis/auth/v3/tenant_access_token/internal"
            payload = json.dumps({
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            }).encode("utf-8")

            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("code") == 0:
                self._tenant_access_token = data.get("tenant_access_token", "")
                return self._tenant_access_token
            else:
                logger.error(f"[Feishu] 获取 token 失败: {data.get('msg')}")
                return ""
        except Exception as e:
            logger.error(f"[Feishu] 获取 token 错误: {e}")
            return ""

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "enabled": False,
            "app_id": "",
            "app_secret": "",
            "allow_from": ["*"],
            "streaming": False,
        }


def _get_mention_name(mention) -> str:
    """获取 @提及 的名称"""
    try:
        return mention.name or ""
    except AttributeError:
        return ""
