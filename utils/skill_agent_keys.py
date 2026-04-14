from __future__ import annotations

import json
import os
from pathlib import Path

_KEYS_FILENAME = "_project_keys.json"


def _get_skills_base() -> Path:
    skills_root = os.environ.get("SKILLS_ROOT", "").strip()
    if skills_root:
        return Path(skills_root)
    return Path(__file__).resolve().parent.parent / "skills"


def _get_keys_path(skills_base: Path | str | None = None) -> Path:
    base = Path(skills_base) if skills_base else _get_skills_base()
    return base / _KEYS_FILENAME


def _load_keys(skills_base: Path | str | None = None) -> dict[str, str]:
    p = _get_keys_path(skills_base)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_keys(keys: dict[str, str], skills_base: Path | str | None = None) -> None:
    p = _get_keys_path(skills_base)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")


def has_project_key(project_name: str, skills_base: Path | str | None = None) -> bool:
    """返回该项目是否已设置过密钥。"""
    return bool(_load_keys(skills_base).get(project_name))


def check_project_key(project_name: str, provided_key: str | None, skills_base: Path | str | None = None) -> bool:
    """
    校验项目密钥。
    - 项目已设置密钥 → provided_key 必须匹配才放行
    - 项目未设置密钥 → 拒绝访问（所有项目均需密钥）
    """
    keys = _load_keys(skills_base)
    stored = keys.get(project_name)
    if not stored:
        return False
    return bool(provided_key) and provided_key.strip() == stored


def set_project_key(project_name: str, new_key: str, skills_base: Path | str | None = None) -> None:
    """设置项目密钥（仅在新增项目时调用，设置后不可更改）。"""
    keys = _load_keys(skills_base)
    keys[project_name] = new_key.strip()
    _save_keys(keys, skills_base)


def _cleanup_project_key(project_name: str, skills_base: Path | str | None = None) -> None:
    """删除项目时清理密钥记录（内部使用）。"""
    keys = _load_keys(skills_base)
    keys.pop(project_name, None)
    _save_keys(keys, skills_base)
