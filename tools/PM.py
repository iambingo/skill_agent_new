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


def get_skills_root() -> Path:
    skills_root = os.environ.get("SKILLS_ROOT", "").strip()
    if skills_root:
        return Path(skills_root)
    return Path(__file__).resolve().parent.parent / "skills"


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

        if not query:
            yield self.create_text_message("❌请填写指令（query）。\n")
            return

        # 查看项目
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
            root = get_skills_root()
            target = root / project_name
            if not target.exists() or not target.is_dir():
                yield self.create_text_message(f"❌项目「{project_name}」不存在。\n")
                return
            try:
                shutil.rmtree(target, ignore_errors=False)
            except Exception as e:
                yield self.create_text_message(f"❌删除失败：{e}\n")
                return
            yield self.create_text_message(f"✅已删除项目「{project_name}」。\n")
            projects = list_projects()
            if not projects:
                yield self.create_text_message("当前没有任何项目。\n")
            else:
                lines = [f"{idx + 1}. {p.name}" for idx, p in enumerate(projects)]
                yield self.create_text_message("👓当前项目列表：\n" + "\n".join(lines) + "\n")
            return

        yield self.create_text_message("😑未识别的指令。支持：查看项目、删除项目。\n")
