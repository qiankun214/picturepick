"""AI 识鸟（bird identification）子包。

对外主要入口：

* :class:`~picturepick.birdid.service.BirdService`：会话/任务/采纳/EXIF 编排；
* :func:`~picturepick.birdid.client.build_client`：按配置返回真实/模拟客户端；
* :func:`~picturepick.birdid.config.resolve_config`：解析环境变量 + 本机配置。
"""

from __future__ import annotations

from .config import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    BirdConfig,
    config_path,
    endpoint_protocol,
    resolve_config,
    update_config,
    validate_endpoint,
)
from .service import BirdService

__all__ = [
    "BirdConfig",
    "BirdService",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "config_path",
    "endpoint_protocol",
    "resolve_config",
    "update_config",
    "validate_endpoint",
]
