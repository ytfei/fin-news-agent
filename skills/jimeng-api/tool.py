"""即梦文生图工具：把 scripts/generate_image.py 封装成 LangChain 工具。

工具型 skill 约定（见 skills/README.md）：导出 get_tool() 返回 LangChain 工具，
由 loader.load_tool_skills 加载后挂入 Agent。这里用 subprocess 调用独立脚本，
sessionid 从环境变量 JIMENG_SESSION_ID 读取，不硬编码凭据。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

_SCRIPT = Path(__file__).resolve().parent / "scripts" / "generate_image.py"


@tool
def jimeng_text2image(prompt: str) -> str:
    """根据文本提示词调用即梦（Jimeng）API 生成图片，并下载到项目 pic/ 目录。

    用于「生成图片 / 插画 / 视觉内容」等文生图场景。
    参数 prompt：图片的文本描述（中文或英文均可）。
    返回：生成并下载的图片文件路径（成功）或错误信息（失败）。
    """
    session_id = os.environ.get("JIMENG_SESSION_ID")
    if not session_id:
        return "错误：未设置环境变量 JIMENG_SESSION_ID，无法调用即梦 API。"

    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "text", prompt, "--session-id", session_id],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return "错误：即梦 API 调用超时（300 秒）。"
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__} -> {str(exc)[:200]}"

    if result.returncode != 0:
        return f"错误：生成失败 -> {(result.stderr or result.stdout)[-300:]}"
    return result.stdout.strip()


def get_tool():
    """供 loader.load_tool_skills 调用，返回本 skill 的工具。"""
    return jimeng_text2image
