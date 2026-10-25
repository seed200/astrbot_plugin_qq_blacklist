"""QQ 黑名单 / 每小时限流插件。

两种模式：

- 永久屏蔽：该 QQ 的任何消息都不会进入后续处理。
- 间歇屏蔽：每个自然小时最多放行 N 条消息，超过后拦截到下一个小时。

配置在 AstrBot WebUI 的「插件 -> QQ 黑名单 -> 插件配置」。
"""

from __future__ import annotations

import json
import time

import astrbot.api.star as star
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.config.astrbot_config import AstrBotConfig

DEFAULT_HOURLY_LIMIT = 5


class QQBlacklistPlugin(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.data_dir = star.StarTools.get_data_dir("astrbot_plugin_qq_blacklist")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "hourly_state.json"
        self.state = self._load_state()

    def _load_state(self) -> dict:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_state(self) -> None:
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.state, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.state_file)
        except Exception as exc:
            logger.warning(f"[QQ黑名单] 保存小时计数失败: {exc}")

    @staticmethod
    def _as_list(value) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _permanent_numbers(self) -> set[str]:
        result = set(self._as_list(self.config.get("permanent_blacklist", [])))
        # 兼容旧版插件的 blacklist 字段。
        result.update(self._as_list(self.config.get("blacklist", [])))
        return result

    def _hourly_numbers(self) -> set[str]:
        return set(self._as_list(self.config.get("hourly_blacklist", [])))

    def _hourly_limit(self) -> int:
        try:
            value = int(self.config.get("hourly_limit", DEFAULT_HOURLY_LIMIT))
        except (TypeError, ValueError):
            value = DEFAULT_HOURLY_LIMIT
        return max(1, value)

    def _consume_hourly(self, qq: str, limit: int) -> bool:
        """放行并计数返回 True；达到上限返回 False。"""
        hour = int(time.time() // 3600)
        record = self.state.get(qq)
        if not isinstance(record, dict) or int(record.get("hour", -1)) != hour:
            record = {"hour": hour, "count": 0}

        count = int(record.get("count", 0))
        if count >= limit:
            self.state[qq] = record
            return False

        record["count"] = count + 1
        self.state[qq] = record
        self._save_state()
        return True

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def block_blacklisted_user(self, event: AstrMessageEvent) -> None:
        if not self.config.get("enabled", True):
            return

        try:
            qq = str(event.get_sender_id()).strip()
        except Exception:
            return
        if not qq:
            return

        if qq in self._permanent_numbers():
            logger.info(f"[QQ黑名单] 永久屏蔽 QQ {qq} 的消息")
            event.stop_event()
            return

        if qq in self._hourly_numbers():
            limit = self._hourly_limit()
            if not self._consume_hourly(qq, limit):
                logger.info(
                    f"[QQ黑名单] QQ {qq} 本小时已达 {limit} 条，拦截消息"
                )
                event.stop_event()
