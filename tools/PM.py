from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

## 本地debug
from dotenv import load_dotenv
load_dotenv()

from utils.skill_agent_keys import (
    check_project_key,
    set_project_key,
    _cleanup_project_key,
    _get_skills_base,
)


def get_skills_root() -> Path:
    return _get_skills_base()


def list_projects() -> list[Path]:
    root = get_skills_root()
    if not root.exists():
        return []
    folders = [p for p in root.iterdir() if p.is_dir()]
    folders.sort(key=lambda p: p.stat().st_ctime)
    return folders


class PMTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        query = str(tool_parameters.get("query") or "").strip()
        project_name = str(tool_parameters.get("project_name") or "").strip()
        access_key = str(tool_parameters.get("access_key") or "").strip()

        if not query:
            yield self.create_text_message("❌请填写指令（query）。\n")
            return

        root = get_skills_root()

        # 新增项目
        if any(kw in query for kw in ("新增项目", "创建项目", "添加项目", "add")):
            if not project_name:
                yield self.create_text_message("❌新增项目时必须填写项目名称（project_name）。\n")
                return
            if not access_key:
                yield self.create_text_message("❌新增项目时必须填写项目密钥（access_key），该密钥将永久作为本项目的访问凭证，设置后不可更改。\n")
                return
            target = root / project_name
            if target.exists() and target.is_dir():
                yield self.create_text_message(f"❌项目「{project_name}」已存在，请重新选择一个项目名称。\n")
                return
            try:
                target.mkdir(parents=True, exist_ok=False)
            except Exception as e:
                yield self.create_text_message(f"❌创建项目失败：{e}\n")
                return
            set_project_key(project_name, access_key)
            yield self.create_text_message(f"✅已创建项目「{project_name}」并设置密钥。密钥设置后不可更改，请妥善保管。\n")
            projects = list_projects()
            lines = [f"{idx + 1}. {p.name}" for idx, p in enumerate(projects)]
            yield self.create_text_message("👓当前项目列表：\n" + "\n".join(lines) + "\n")
            return

        # 查看项目（无需密钥）
        if any(kw in query for kw in ("查看项目", "项目列表", "所有项目", "list")):
            projects = list_projects()
            if not projects:
                yield self.create_text_message("当前没有任何项目。\n")
                return
            lines = [f"{idx + 1}. {p.name}" for idx, p in enumerate(projects)]
            yield self.create_text_message("✅当前项目列表：\n" + "\n".join(lines) + "\n")
            return

        # 删除项目
        if any(kw in query for kw in ("删除项目", "delete")):
            if not project_name:
                yield self.create_text_message("❌删除项目时必须填写项目名称（project_name）。\n")
                return
            if not check_project_key(project_name, access_key):
                yield self.create_text_message("❌密钥错误，无权删除该项目。\n")
                return
            target = root / project_name
            if not target.exists() or not target.is_dir():
                yield self.create_text_message(f"❌项目「{project_name}」不存在。\n")
                return
            try:
                shutil.rmtree(target, ignore_errors=False)
            except Exception as e:
                yield self.create_text_message(f"❌删除失败：{e}\n")
                return
            _cleanup_project_key(project_name)
            yield self.create_text_message(f"✅已删除项目「{project_name}」。\n")
            projects = list_projects()
            if not projects:
                yield self.create_text_message("当前没有任何项目。\n")
            else:
                lines = [f"{idx + 1}. {p.name}" for idx, p in enumerate(projects)]
                yield self.create_text_message("👓当前项目列表：\n" + "\n".join(lines) + "\n")
            return

        yield self.create_text_message("😑未识别的指令。支持：查看项目、新增项目、删除项目。\n")
