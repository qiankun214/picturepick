"""领域错误与 API 错误码映射（docs/API.md 第 3 版 §1）。

所有非 2xx 响应体统一为::

    {"detail": {"code": "<CODE>", "message": "<人类可读信息>"}}

``ApiError`` 同时被 API 层（异常处理器转 JSON）与 CLI 层（打印后非 0 退出）复用。
"""

from __future__ import annotations

from typing import Dict

# ---- 契约错误码 ------------------------------------------------------------

INVALID_PATH = "INVALID_PATH"
NO_PHOTOS = "NO_PHOTOS"
INVALID_PHOTOS = "INVALID_PHOTOS"
TOO_MANY_PHOTOS = "TOO_MANY_PHOTOS"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
PHOTO_NOT_FOUND = "PHOTO_NOT_FOUND"
ROUND_NOT_FOUND = "ROUND_NOT_FOUND"
NOT_ENOUGH_PICKS = "NOT_ENOUGH_PICKS"
PHASE_FINISHED = "PHASE_FINISHED"
PHASE_PICKING = "PHASE_PICKING"
DIALOG_UNAVAILABLE = "DIALOG_UNAVAILABLE"
WRITE_FAILED = "WRITE_FAILED"
#: 导出最后一轮保留照片时，最后一轮没有任何 pick
NO_PICKED_PHOTOS = "NO_PICKED_PHOTOS"
#: 来源不支持该操作（如 browser 会话调用 export-photos / resume）
INVALID_SOURCE = "INVALID_SOURCE"
#: 载入的结果 JSON 结构非法
INVALID_RESTORE = "INVALID_RESTORE"
#: 结果 JSON 的 schemaVersion 不受支持
UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"

# ---- AI 识鸟（第 5 版） ----------------------------------------------------

#: 未配置 ARK API Key（且未开启模拟模式）
BIRD_NOT_CONFIGURED = "BIRD_NOT_CONFIGURED"
#: 识别任务不存在（或不属于该会话）
BIRD_TASK_NOT_FOUND = "BIRD_TASK_NOT_FOUND"
#: 任务尚未完成（排队/识别中），无法采纳
BIRD_TASK_NOT_COMPLETED = "BIRD_TASK_NOT_COMPLETED"
#: 任务已被采纳，不能忽略
BIRD_TASK_ADOPTED = "BIRD_TASK_ADOPTED"
#: 任务结果不可用（模型判断画面中没有鸟）
BIRD_RESULT_UNAVAILABLE = "BIRD_RESULT_UNAVAILABLE"
#: 任务没有可用的参考照片
BIRD_REFERENCE_UNAVAILABLE = "BIRD_REFERENCE_UNAVAILABLE"
#: AI 识鸟配置非法（PUT /api/bird/config）
INVALID_CONFIG = "INVALID_CONFIG"
#: 调用方舟接口失败（任务级错误码）
BIRD_API_ERROR = "BIRD_API_ERROR"
#: 调用方舟接口超时（任务级错误码）
BIRD_API_TIMEOUT = "BIRD_API_TIMEOUT"
#: 模型输出无法解析（任务级错误码）
BIRD_PARSE_ERROR = "BIRD_PARSE_ERROR"

#: docs/API.md §1 定义的错误码 -> 推荐 HTTP 状态码
STATUS_BY_CODE: Dict[str, int] = {
    INVALID_PATH: 400,
    NO_PHOTOS: 400,
    INVALID_PHOTOS: 400,
    TOO_MANY_PHOTOS: 400,
    SESSION_NOT_FOUND: 404,
    PHOTO_NOT_FOUND: 404,
    ROUND_NOT_FOUND: 404,
    NOT_ENOUGH_PICKS: 409,
    PHASE_FINISHED: 409,
    PHASE_PICKING: 409,
    DIALOG_UNAVAILABLE: 501,
    WRITE_FAILED: 400,
    NO_PICKED_PHOTOS: 400,
    INVALID_SOURCE: 400,
    INVALID_RESTORE: 400,
    UNSUPPORTED_SCHEMA: 400,
    BIRD_NOT_CONFIGURED: 409,
    BIRD_TASK_NOT_FOUND: 404,
    BIRD_TASK_NOT_COMPLETED: 409,
    BIRD_TASK_ADOPTED: 409,
    BIRD_RESULT_UNAVAILABLE: 409,
    BIRD_REFERENCE_UNAVAILABLE: 404,
    INVALID_CONFIG: 400,
    BIRD_API_ERROR: 502,
    BIRD_API_TIMEOUT: 504,
    BIRD_PARSE_ERROR: 502,
}


class ApiError(Exception):
    """可安全返回给客户端的业务错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code if status_code is not None else STATUS_BY_CODE.get(code, 400)
        super().__init__(f"{code}: {message}")

    def to_payload(self) -> Dict[str, object]:
        return {"detail": {"code": self.code, "message": self.message}}

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"ApiError(code={self.code!r}, message={self.message!r}, status_code={self.status_code})"
