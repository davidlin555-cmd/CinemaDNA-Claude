"""CinemaDNA / DramaOS-X 配置层 —— 环境变量的唯一权威来源

代码里**不要**散落 `os.getenv("XXX")`；一律从这里读，好处：

1. 变量名统一，改名只改一处
2. 启动时自动加载 .env（若装了 python-dotenv 且文件存在），没有也能降级
3. 有密钥 / 没密钥的判断集中在 `has()`，供各能力决定"接真实模型还是走 mock"

**绝不在日志里打印密钥值**：`describe()` 只报告"配了没配"，不报告值本身。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

#: 仓库根（本文件所在目录）
ROOT: Final = Path(__file__).resolve().parent
ENV_FILE: Final = ROOT / ".env"


def _load_dotenv() -> bool:
    """尽力加载 .env。装了 python-dotenv 就用它，否则手工解析，都没有就跳过。"""
    if not ENV_FILE.exists():
        return False
    try:
        from dotenv import load_dotenv

        # override=False：真实环境变量优先于 .env（CI / 容器里更安全）
        load_dotenv(ENV_FILE, override=False)
        return True
    except ImportError:
        # 退化：手工解析，绝不覆盖已存在的环境变量
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        return True


_load_dotenv()


# ---------------------------------------------------------------------------
# 变量登记表（唯一权威）—— 新增变量先在此登记
# ---------------------------------------------------------------------------

#: 密钥类变量，按能力分组。分组名用于 describe() 与"这个能力接没接"的判断。
API_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "llm": ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"),
    "video": ("KLING_API_KEY", "SEEDANCE_API_KEY", "RUNWAY_API_KEY"),
    "image": ("FAL_KEY", "TOGETHER_API_KEY"),
    "audio": ("ELEVENLABS_API_KEY",),
}

#: 所有已登记的密钥变量名（扁平）
ALL_KEY_NAMES: Final[tuple[str, ...]] = tuple(
    name for names in API_KEYS.values() for name in names
)


# ---------------------------------------------------------------------------
# 读取入口
# ---------------------------------------------------------------------------


def get(name: str, default: str = "") -> str:
    """读一个环境变量（去除首尾空白）。"""
    return (os.environ.get(name) or default).strip()


def has(name: str) -> bool:
    """该密钥是否已配置（非空）。各能力据此决定接真实模型还是走 mock。"""
    return bool(get(name))


def require(name: str) -> str:
    """读一个必须存在的密钥，没配就抛错（接真实模型时用）。"""
    val = get(name)
    if not val:
        raise RuntimeError(
            f"环境变量 {name} 未配置。请在 {ENV_FILE} 里填入，"
            f"或先用 mock / placeholder 后端。"
        )
    return val


def capability_enabled(group: str) -> bool:
    """某类能力是否至少有一个可用密钥（如 video / image / audio / llm）。"""
    if group not in API_KEYS:
        raise KeyError(f"未知能力分组 {group!r}，可选 {sorted(API_KEYS)}")
    return any(has(name) for name in API_KEYS[group])


# ---------------------------------------------------------------------------
# 运行环境
# ---------------------------------------------------------------------------

ENV: Final = get("CINEMA_DNA_ENV", "development")
WEB_HOST: Final = get("WEB_HOST", "127.0.0.1")
WEB_PORT: Final = int(get("WEB_PORT", "8700") or "8700")

IS_PRODUCTION: Final = ENV == "production"


# ---------------------------------------------------------------------------
# 自检 / 看板
# ---------------------------------------------------------------------------


def describe() -> dict[str, object]:
    """报告配置状态 —— **只说配没配，绝不泄漏密钥值**。"""
    return {
        "env": ENV,
        "web_host": WEB_HOST,
        "web_port": WEB_PORT,
        "dotenv_loaded": ENV_FILE.exists(),
        "capabilities": {
            group: {
                "enabled": capability_enabled(group),
                "keys": {name: has(name) for name in names},
            }
            for group, names in API_KEYS.items()
        },
    }


__all__ = [
    "ROOT", "ENV_FILE", "API_KEYS", "ALL_KEY_NAMES",
    "get", "has", "require", "capability_enabled",
    "ENV", "WEB_HOST", "WEB_PORT", "IS_PRODUCTION", "describe",
]
