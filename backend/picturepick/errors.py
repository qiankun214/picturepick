"""领域错误与 API 错误码映射（docs/API.md §1）。

所有非 2xx 响应体统一为::

    {"detail": {"code": "<CODE>", "message": "<人类可读信息>"}}

``ApiError`` 同时被 API 层（异常处理器转 JSON）与 CLI 层（打印后非 0 退出）复用。
"""

from __future__ import annotations

from typing import Dict

# ---- 契约错误码 ------------------------------------------------------------

INVALID_PATH = "INVALID_PATH"
NO_PHOTOS = "NO_PHOTOS"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
PHOTO_NOT_FOUND = "PHOTO_NOT_FOUND"
ROUND_NOT_FOUND = "ROUND_NOT_FOUND"
NOT_ENOUGH_PICKS = "NOT_ENOUGH_PICKS"
PHASE_FINISHED = "PHASE_FINISHED"
DIALOG_UNAVAILABLE = "DIALOG_UNAVAILABLE"
WRITE_FAILED = "WRITE_FAILED"

#: docs/API.md §1 定义的错误码 -> 推荐 HTTP 状态码
STATUS_BY_CODE: Dict[str, int] = {
    INVALID_PATH: 400,
    NO_PHOTOS: 400,
    SESSION_NOT_FOUND: 404,
    PHOTO_NOT_FOUND: 404,
    ROUND_NOT_FOUND: 404,
    NOT_ENOUGH_PICKS: 409,
    PHASE_FINISHED: 409,
    DIALOG_UNAVAILABLE: 501,
    WRITE_FAILED: 400,
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
