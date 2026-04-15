from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen
from zipfile import ZipFile

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

## 本地debug
import os
from dotenv import load_dotenv
load_dotenv()

from utils.skill_agent_keys import check_project_key

def _rewrite_internal_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname == "api":
        dify_api_url = os.environ.get("DIFY_API_URL", "").strip().rstrip("/")
        if dify_api_url:
            base = urlparse(dify_api_url)
            return urlunparse(parsed._replace(scheme=base.scheme, netloc=base.netloc))
    return url


def get_file_content(url: str, timeout: int = 30) -> bytes:
    # url = _rewrite_internal_url(url)  # ### 本地debug
    try:
        req = Request(url, headers={"User-Agent": "dify-plugin-skill/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        raise RuntimeError(f"文件下载失败: {str(e)}") from e

### 本地debug
# def get_skills_dir(project_name: str) -> Path:
#     root = Path(__file__).resolve().parent.parent
#     skills_dir = root / "skills" / project_name
#     skills_dir.mkdir(parents=True, exist_ok=True)
#     return skills_dir
def get_skills_dir(project_name: str) -> Path:
    skills_root = os.environ.get("SKILLS_ROOT", "").strip()
    if skills_root:
        root = Path(skills_root)
    else:
        root = Path(__file__).resolve().parent.parent / "skills"
    skills_dir = root / project_name
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def list_skills_sorted(project_name: str) -> list[Path]:
    skills_dir = get_skills_dir(project_name)
    folders = [p for p in skills_dir.iterdir() if p.is_dir()]
    folders.sort(key=lambda p: p.stat().st_ctime)
    return folders


def _parse_description(content: str) -> str:
    """从 SKILL.md frontmatter 中提取 description，支持单行和 >/ | 块标量。"""
    raw_lines = content.splitlines()
    if not raw_lines or raw_lines[0].strip() != "---":
        return ""
    i = 1
    while i < len(raw_lines):
        line = raw_lines[i]
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            val = line.split(":", 1)[1].strip()
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                # 块标量：收集后续缩进行
                block: list[str] = []
                i += 1
                while i < len(raw_lines):
                    bl = raw_lines[i]
                    if bl.strip() == "---":
                        break
                    if bl and not bl[0].isspace():
                        break  # 新的 key，停止
                    block.append(bl.strip())
                    i += 1
                return " ".join(s for s in block if s)
            return val
        i += 1
    return ""


def extract_url_and_name(file_item: Any) -> tuple[str | None, str | None]:
    url = None
    name = None
    if hasattr(file_item, "url"):
        url = getattr(file_item, "url", None)
    if hasattr(file_item, "filename"):
        name = getattr(file_item, "filename", None)
    if hasattr(file_item, "name") and not name:
        name = getattr(file_item, "name", None)
    if isinstance(file_item, dict):
        url = file_item.get("url", url)
        name = file_item.get("filename", name) or file_item.get("name", name)
    return url, name


def infer_ext_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix
    return ext if ext else ".zip"


def safe_filename(preferred_name: str | None, fallback_ext: str = ".zip") -> str:
    if preferred_name:
        base = Path(preferred_name).name
        base = re.sub(r"[<>:\"/\\\\|?*]+", "_", base).strip()
        if base:
            return base
    return f"{uuid.uuid4().hex}{fallback_ext}"


def _is_within_dir(base: Path, target: Path) -> bool:
    try:
        base_resolved = base.resolve()
        target_resolved = target.resolve()
        return base_resolved == target_resolved or base_resolved in target_resolved.parents
    except Exception:
        return False


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name:
                continue
            if name.startswith("/") or name.startswith("\\"):
                raise RuntimeError("压缩包包含非法路径")
            target_path = (dest_dir / name).resolve()
            if not _is_within_dir(dest_dir, target_path):
                raise RuntimeError("压缩包包含越权路径")
            if info.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _find_skill_folders(extracted_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for p in extracted_root.iterdir():
        if p.is_dir():
            candidates.append(p)
    if candidates:
        with_skill_md = [p for p in candidates if (p / "SKILL.md").is_file()]
        if with_skill_md:
            return with_skill_md
        if len(candidates) == 1:
            return candidates
        return candidates
    if (extracted_root / "SKILL.md").is_file():
        return [extracted_root]
    return []


class TMTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        command = str(tool_parameters.get("command", "")).strip()
        files_param = tool_parameters.get("files")
        project_name = str(tool_parameters.get("project_name") or "").strip()
        access_key = str(tool_parameters.get("access_key") or "").strip()

        if not project_name:
            yield self.create_text_message("❌请填写项目名称（project_name）。\n")
            return

        # 新增/删除技能 → 需要密钥（查看技能、技能详情不需要）
        if command in ("新增技能", "存入技能", "保存技能", "删除技能"):
            if not check_project_key(project_name, access_key):
                yield self.create_text_message("❌密钥错误，无权对项目【{}】执行写操作。\n".format(project_name))
                return

        if command in ("查看技能", "查看 技能", "查看"):
            _skills_root = os.environ.get("SKILLS_ROOT", "").strip()
            _root = Path(_skills_root) if _skills_root else Path(__file__).resolve().parent.parent / "skills"
            if not (_root / project_name).is_dir():
                yield self.create_text_message(
                    f"❌项目「{project_name}」不存在，请先在「项目管理」中新增该项目，再回来添加技能。\n"
                )
                return

            skills = list_skills_sorted(project_name)
            if not skills:
                yield self.create_text_message(f"❌项目【{project_name}】当前没有已存入的技能包。\n")
                return
            lines = [f"{idx + 1}. {p.name}" for idx, p in enumerate(skills)]
            yield self.create_text_message(f"✅查询成功，项目【{project_name}】当前技能列表如下：\n")
            yield self.create_text_message("\n".join(lines))
            return


        if command in ("技能详情", "技能描述"):
            _skills_root = os.environ.get("SKILLS_ROOT", "").strip()
            _root = Path(_skills_root) if _skills_root else Path(__file__).resolve().parent.parent / "skills"
            if not (_root / project_name).is_dir():
                yield self.create_text_message(
                    f"❌项目「{project_name}」不存在，请先在「项目管理」中新增该项目，再回来添加技能。\n"
                )
                return

            skills = list_skills_sorted(project_name)
            if not skills:
                yield self.create_text_message(f"❌项目【{project_name}】当前没有已存入的技能包。\n")
                return
            lines = []
            for idx, p in enumerate(skills):
                skill_md = p / "SKILL.md"
                description = ""
                if skill_md.is_file():
                    content = skill_md.read_text(encoding="utf-8", errors="ignore")
                    description = _parse_description(content)
                lines.append(f"{idx + 1}. {p.name}：{description}")
            yield self.create_text_message(f"当前技能和详情如下：\n")
            yield self.create_text_message("\n".join(lines))
            return


        if command in ("新增技能", "存入技能", "保存技能"):
            _skills_root = os.environ.get("SKILLS_ROOT", "").strip()
            _root = Path(_skills_root) if _skills_root else Path(__file__).resolve().parent.parent / "skills"
            if not (_root / project_name).is_dir():
                yield self.create_text_message(
                    f"❌项目「{project_name}」不存在，请先在「项目管理」中新增该项目，再回来添加技能。\n"
                )
                return

            file_items: list[Any] = []
            if isinstance(files_param, list):
                file_items = [x for x in files_param if x]
            elif files_param:
                file_items = [files_param]
            elif "file" in tool_parameters and tool_parameters["file"]:
                file_items = [tool_parameters["file"]]

            if not file_items:
                yield self.create_text_message("❌未检测到上传的 zip 文件，请提供 files 参数。\n")
                return

            skills_dir = get_skills_dir(project_name)
            installed: list[str] = []

            for file_item in file_items:
                url, preferred_name = extract_url_and_name(file_item)

                if not url:
                    yield self.create_text_message("❌无法获取文件URL，请检查入参（files[i].url）。\n")
                    return

                filename_attr = None
                try:
                    filename_attr = getattr(file_item, "filename", None)
                except Exception:
                    filename_attr = None
                if isinstance(file_item, dict):
                    filename_attr = file_item.get("filename", filename_attr)

                try:
                    content = get_file_content(url)
                except Exception as e:
                    yield self.create_text_message(str(e))
                    return

                if filename_attr:
                    filename = Path(filename_attr).name
                else:
                    ext = infer_ext_from_url(url)
                    filename = safe_filename(preferred_name, fallback_ext=ext if ext else ".zip")

                with tempfile.TemporaryDirectory(prefix="skill-upload-") as td:
                    tmp_dir = Path(td)
                    zip_path = tmp_dir / filename
                    try:
                        zip_path.write_bytes(content)
                    except Exception as e:
                        yield self.create_text_message(f"❌保存临时文件失败：{e}\n")
                        return

                    extract_dir = tmp_dir / "extracted"
                    try:
                        _safe_extract_zip(zip_path, extract_dir)
                    except Exception as e:
                        yield self.create_text_message(f"❌解压失败：{e}\n")
                        return

                    skill_folders = _find_skill_folders(extract_dir)
                    if not skill_folders:
                        yield self.create_text_message("❌压缩包内未找到技能目录（应包含 SKILL.md）。\n")
                        return

                    for folder in skill_folders:
                        target = skills_dir / folder.name
                        if target.exists():
                            yield self.create_text_message(f"❌技能已存在：{folder.name}（请先删除同名技能）\n")
                            return
                        try:
                            shutil.move(str(folder), str(target))
                            installed.append(target.name)
                        except Exception as e:
                            yield self.create_text_message(f"❌安装技能失败：{e}\n")
                            return

            yield self.create_text_message("✅技能已安装：\n" + "\n".join(installed) + "\n")
            skills = list_skills_sorted(project_name)
            lines = [f"{idx + 1}. {p.name}" for idx, p in enumerate(skills)]
            yield self.create_text_message(f"👓当前项目【{project_name}】技能列表：\n" + ("\n".join(lines) if lines else "（空）\n"))
            return

        if command in ("删除技能",):
            skill_name = str(tool_parameters.get("skill_name") or "").strip()
            if not skill_name:
                yield self.create_text_message("❌删除技能时必须填写技能名称（skill_name）。\n")
                return
            skills_dir = get_skills_dir(project_name)
            target = skills_dir / skill_name
            if not target.exists() or not target.is_dir():
                yield self.create_text_message(f"❌技能「{skill_name}」不存在，请先使用”查看技能”确认技能名称。\n")
                return
            try:
                shutil.rmtree(target, ignore_errors=False)
            except Exception as e:
                yield self.create_text_message(f"❌删除失败：{e}\n")
                return
            yield self.create_text_message(f"✅已删除技能：{skill_name}\n")
            skills = list_skills_sorted(project_name)
            if not skills:
                yield self.create_text_message(f"😑当前项目【{project_name}】技能列表为空。\n")
            else:
                lines = [f"{i + 1}. {p.name}" for i, p in enumerate(skills)]
                yield self.create_text_message(f"👓当前项目【{project_name}】技能列表：\n" + "\n".join(lines))
            return

        yield self.create_text_message("😑未识别的技能管理命令。支持：查看技能、技能详情、新增技能、删除技能。\n")
        return
