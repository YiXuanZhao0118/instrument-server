# client.py
"""
Tiny RPC client for your Instrument Server.

Usage:
    from client import post

    base = "http://172.30.10.18:9999"   # or "172.30.10.18:9999"
    instrument = "HF1"                  # name 或 id 都可以
    cmd = "set_wavelength"
    args = [1550.0]                     # 位置參數 -> list
    kwargs = {"unit": "nm"}             # 關鍵字參數 -> dict

    result = post(base, instrument, cmd, args, kwargs)
    print("RPC result:", result)

Requires:
    pip install requests
"""

from __future__ import annotations
from typing import Any, Mapping, Sequence, Optional
import json
import re

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "client.py needs the 'requests' package. Install with: pip install requests"
    ) from exc


class RPCError(RuntimeError):
    """Raised when the server returns an error JSON or non-200 HTTP response."""


def _normalize_base(ip_or_base: str) -> str:
    """
    Ensure base URL has scheme and no trailing slash.
    - Accepts: 'http://host:9999', 'https://host:9999', or 'host:9999'
    """
    base = (ip_or_base or "").strip()
    if not re.match(r"^https?://", base, flags=re.IGNORECASE):
        base = "http://" + base
    return base.rstrip("/")


def post(
    IP: str,
    Instrument: str,
    command: str,
    args: Optional[Sequence[Any]] = None,
    Kwargs: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = 15.0,
    return_full: bool = False,
) -> Any:
    """
    Call /rpc on the Instrument Server.

    Parameters
    ----------
    IP : str
        伺服器 base，例如 'http://172.30.10.18:9999' 或 '172.30.10.18:9999'
    Instrument : str
        儀器的 name 或 id（伺服器兩者都接受）
    command : str
        要呼叫的驅動方法名稱（需存在於該 Driver 的公開函式）
    args : list | tuple | None
        位置參數，會原樣傳入（預設 []）
    Kwargs : dict | None
        關鍵字參數，會原樣傳入（預設 {}）
    timeout : float
        HTTP 請求逾時秒數（預設 15）
    return_full : bool
        True → 回傳完整的伺服器 JSON；False → 回傳 JSON 裡的 result 欄位

    Returns
    -------
    Any
        伺服器 JSON 的 result 欄位（或 return_full=True 時回傳整個 JSON）

    Raises
    ------
    RPCError
        當 HTTP 非 2xx、或回傳 JSON 沒有 ok、或 ok=False 時。
    """
    base = _normalize_base(IP)
    url = f"{base}/rpc"

    payload = {
        "instrument": Instrument,
        "command": command,
        "args": list(args) if args is not None else [],
        "kwargs": dict(Kwargs) if Kwargs is not None else {},
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise RPCError(f"RPC request failed: {e}") from e

    # 非 2xx
    if not (200 <= resp.status_code < 300):
        # 嘗試抽出錯誤訊息
        msg = resp.text
        try:
            j = resp.json()
            msg = json.dumps(j, ensure_ascii=False)
        except Exception:
            pass
        raise RPCError(f"HTTP {resp.status_code} {resp.reason}: {msg}")

    # 解析 JSON
    try:
        j = resp.json()
    except ValueError as e:
        raise RPCError(f"Server did not return JSON: {resp.text[:200]}") from e

    if not isinstance(j, dict) or "ok" not in j:
        raise RPCError(f"Malformed response: {json.dumps(j, ensure_ascii=False)}")

    if not j.get("ok", False):
        # 伺服器在 HTTPException 時通常會直接回 4xx/5xx，不會進到這；這裡是保險
        raise RPCError(f"RPC returned ok=False: {json.dumps(j, ensure_ascii=False)}")

    return j if return_full else j.get("result", None)
