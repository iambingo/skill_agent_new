from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from utils.skill_agent_constants import ALLOWED_COMMANDS
from utils.skill_agent_exec import (
    _ensure_python_module,
    _missing_executable_hint,
    _resolve_executable,
    _skill_contains_python_module,
)
from utils.skill_agent_paths import (
    _normalize_relative_file_path,
    _rewrite_existing_session_files_to_abs,
    _rewrite_out_arg_to_session_dir,
    _rewrite_uploads_paths_to_session_dir,
)
from utils.tools import _list_dir, _parse_frontmatter, _read_text, _safe_join


class _AgentRuntime:
    def __init__(
        self,
        *,
        skills_root: str | None,
        session_dir: str,
        max_steps: int,
        memory_turns: int,
    ) -> None:
        self.skills_root = skills_root
        self.session_dir = session_dir
        self.max_steps = max_steps
        self.memory_turns = memory_turns
        self._skill_metadata_cache: dict[str, dict[str, Any]] = {}
        self._skill_files_listed: set[str] = set()

    def has_skill_metadata(self, skill_name: str) -> bool:
        cached = self._skill_metadata_cache.get(skill_name)
        return bool(isinstance(cached, dict) and cached.get("skill") == skill_name)

    def load_skills_index(self) -> dict[str, Any]:
        if not self.skills_root:
            return {"root": None, "skills": []}
        skills: list[dict[str, Any]] = []
        for folder in sorted(os.listdir(self.skills_root)):
            path = os.path.join(self.skills_root, folder)
            if not os.path.isdir(path):
                continue
            skill_md = os.path.join(path, "SKILL.md")
            meta: dict[str, str] = {}
            if os.path.isfile(skill_md):
                meta = _parse_frontmatter(_read_text(skill_md, 4000))
            skills.append(
                {
                    "name": meta.get("name") or folder,
                    "folder": folder,
                    "description": meta.get("description") or "",
                }
            )
        return {"root": self.skills_root, "skills": skills}

    def get_skill_metadata(self, skill_name: str) -> dict[str, Any]:
        if not self.skills_root:
            return {"error": "skills_root not found"}
        path = _safe_join(self.skills_root, skill_name)
        skill_md = os.path.join(path, "SKILL.md")
        if not os.path.isfile(skill_md):
            return {"error": "SKILL.md not found", "skill": skill_name}
        content = _read_text(skill_md, 12000)
        meta = _parse_frontmatter(content)
        self._skill_metadata_cache[skill_name] = {"skill": skill_name, "metadata": meta}
        return {"skill": skill_name, "metadata": meta, "skill_md": content}

    def list_skill_files(self, skill_name: str, max_depth: int = 2) -> dict[str, Any]:
        if not self.skills_root:
            return {"error": "skills_root not found"}
        skill_path = _safe_join(self.skills_root, skill_name)
        self._skill_files_listed.add(skill_name)
        return {"skill": skill_name, "entries": _list_dir(skill_path, max_depth=max_depth)}

    def has_listed_skill_files(self, skill_name: str) -> bool:
        return str(skill_name or "").strip() in self._skill_files_listed

    def read_skill_file(self, skill_name: str, relative_path: str, max_chars: int = 12000) -> dict[str, Any]:
        if not self.skills_root:
            return {"error": "skills_root not found"}
        skill_path = _safe_join(self.skills_root, skill_name)
        file_path = _safe_join(skill_path, relative_path)
        if not os.path.isfile(file_path):
            return {"error": "file not found", "path": relative_path}
        return {"path": file_path, "content": _read_text(file_path, max_chars)}

    def write_temp_file(self, relative_path: str, content: str) -> dict[str, Any]:
        os.makedirs(self.session_dir, exist_ok=True)
        rp = _normalize_relative_file_path(relative_path)
        if not rp:
            return {"error": "invalid relative_path", "relative_path": relative_path}
        try:
            path = _safe_join(self.session_dir, rp)
        except Exception as e:
            return {"error": "invalid relative_path", "relative_path": relative_path, "exception": str(e)}
        if os.path.isdir(path):
            return {"error": "path is a directory", "relative_path": relative_path, "path": path}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content or "")
        except Exception as e:
            return {"error": "write failed", "relative_path": relative_path, "path": path, "exception": str(e)}
        return {"path": path, "bytes": len((content or "").encode("utf-8"))}

    def read_temp_file(self, relative_path: str, max_chars: int = 12000) -> dict[str, Any]:
        os.makedirs(self.session_dir, exist_ok=True)
        rp = _normalize_relative_file_path(relative_path)
        if not rp:
            return {"error": "invalid relative_path", "relative_path": relative_path}
        try:
            path = _safe_join(self.session_dir, rp)
        except Exception as e:
            return {"error": "invalid relative_path", "relative_path": relative_path, "exception": str(e)}
        if os.path.isdir(path):
            return {"error": "path is a directory", "relative_path": relative_path, "path": path}
        if not os.path.isfile(path):
            return {"error": "file not found", "relative_path": relative_path}
        try:
            return {"path": path, "content": _read_text(path, max_chars)}
        except Exception as e:
            return {"error": "read failed", "relative_path": relative_path, "path": path, "exception": str(e)}

    def list_temp_files(self, max_depth: int = 4) -> dict[str, Any]:
        os.makedirs(self.session_dir, exist_ok=True)
        return {"session_dir": self.session_dir, "entries": _list_dir(self.session_dir, max_depth=max_depth)}

    def get_session_context(self) -> dict[str, Any]:
        return {
            "skills_root": self.skills_root,
            "session_dir": self.session_dir,
        }

    def run_skill_command(
        self,
        *,
        skill_name: str,
        command: list[str] | str,
        cwd_relative: str | None = None,
        auto_install: bool = False,
    ) -> dict[str, Any]:
        if not self.skills_root:
            return {"error": "skills_root not found"}

        import shlex
        skill_path = _safe_join(self.skills_root, skill_name)

        # ── 字符串命令：shell=True，让系统 shell 处理所有引号，避免 shlex 的转义问题 ──
        if isinstance(command, str):
            command_str = command.replace("\\\n", " ").replace("\n", " ").strip()
            if not command_str:
                return {"error": "command must be non-empty"}
            first_word = command_str.split()[0]
            if first_word in ("python", "python3"):
                # 替换为当前解释器路径
                command_str = sys.executable + command_str[len(first_word):]
            elif first_word not in ALLOWED_COMMANDS:
                return {"error": f"command not allowed: {first_word}"}
            cwd = skill_path if not cwd_relative else _safe_join(skill_path, str(cwd_relative).strip("/\\"))
            try:
                result = subprocess.run(
                    command_str,
                    shell=True,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
            except Exception as e:
                return {"error": "subprocess_failed", "exception": str(e)}

        # ── 列表命令：保持原有逻辑 ──
        if not command:
            return {"error": "command must be a non-empty list"}

        # LLM 把多行 curl 转成列表时，会把行续接符变成 '\n' 或 '\\n' 独立元素插入列表。
        # 同时，列表元素内的换行符也需要清理。
        command = [str(a) for a in command]
        command = [a for a in command if a not in ("\n", "\\n", "\r\n", "")]
        command = [a.replace("\\\n", " ").replace("\n", " ").strip() for a in command]
        command = [a for a in command if a]  # 再次过滤清理后变空的元素

        # LLM 有时会在列表命令的 -d 参数里双重转义 JSON（{\"key\":\"val\"}）。
        # 用 json.loads 验证，确认 unescape 后是合法 JSON 再替换。
        import json as _json
        _DATA_FLAGS = {"-d", "--data", "--data-raw", "--data-binary"}
        _fixed: list[str] = []
        _ci = 0
        while _ci < len(command):
            _arg = command[_ci]
            if _arg in _DATA_FLAGS and _ci + 1 < len(command):
                _fixed.append(_arg)
                _ci += 1
                _data = command[_ci]
                if '\\"' in _data:
                    _unescaped = _data.replace('\\"', '"')
                    try:
                        _json.loads(_unescaped)
                        _data = _unescaped
                    except Exception:
                        pass
                _fixed.append(_data)
            else:
                _fixed.append(_arg)
            _ci += 1
        command = _fixed

        exe = command[0]
        if exe == "python":
            if "-m" in command:
                module_index = command.index("-m") + 1
                if module_index < len(command):
                    module_name = command[module_index]
                    if not _skill_contains_python_module(skill_path, str(module_name)):
                        return {
                            "error": "no_executable_found",
                            "skill": skill_name,
                            "reason": "python -m module not found in skill folder",
                            "module": str(module_name),
                        }
                    module_check = _ensure_python_module(str(module_name), auto_install=auto_install, cwd=self.session_dir)
                    if not module_check.get("ok"):
                        return module_check
            command = [sys.executable] + command[1:]
        elif exe not in ALLOWED_COMMANDS:
            return {"error": f"command not allowed: {exe}"}
        resolved0 = _resolve_executable(str(command[0] or ""))
        if not resolved0:
            missing = str(command[0] or exe)
            return {"error": "executable_not_found", "exe": missing, "hint": _missing_executable_hint(missing)}
        command = [resolved0] + command[1:]
        command = _rewrite_uploads_paths_to_session_dir(command, session_dir=self.session_dir)
        command = _rewrite_existing_session_files_to_abs(command, session_dir=self.session_dir)
        command = _rewrite_out_arg_to_session_dir(command, session_dir=self.session_dir)
        # cwd = skill_path if not cwd_relative else _safe_join(skill_path, cwd_relative)
        #
        # if len(command) > 1 and not os.path.isabs(command[1]):
        #     candidate = os.path.normpath(os.path.join(skill_path, command[1]))
        #     if os.path.isfile(candidate):
        #         command = [command[0], candidate] + command[2:]
        #
        # if not cwd_relative:
        #     cwd = skill_path
        # elif cwd_relative == skill_name:
        #     cwd = skill_path
        # else:
        #     cwd = _safe_join(skill_path, cwd_relative)

        script_resolved_from_skill_path = False
        if len(command) > 1 and not os.path.isabs(command[1]):
            candidate = os.path.normpath(os.path.join(skill_path, command[1]))
            if os.path.isfile(candidate):
                command = [command[0], candidate] + command[2:]
                script_resolved_from_skill_path = True
        # Normalize cwd_relative: ignore if script was resolved from skill_path (cwd = skill_path),
        # or if LLM passed the skill name itself as cwd_relative.
        if script_resolved_from_skill_path:
            cwd_rel_norm = None
        elif cwd_relative:
            cwd_rel_norm = cwd_relative.strip("/").strip("\\")
            if cwd_rel_norm == skill_name or cwd_rel_norm == "":
                cwd_rel_norm = None
            elif cwd_rel_norm.startswith(skill_name + "/") or cwd_rel_norm.startswith(skill_name + "\\"):
                cwd_rel_norm = cwd_rel_norm[len(skill_name) + 1:]
        else:
            cwd_rel_norm = None
        cwd = skill_path if not cwd_rel_norm else _safe_join(skill_path, cwd_rel_norm)

        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        except FileNotFoundError as e:
            return {"error": "executable_not_found", "exe": str(command[0] or exe), "exception": str(e)}
        except Exception as e:
            return {"error": "subprocess_failed", "exe": str(command[0] or exe), "exception": str(e)}

    def run_temp_command(
        self, *, command: list[str], cwd_relative: str | None = None, auto_install: bool = False
    ) -> dict[str, Any]:
        if not command:
            return {"error": "command must be a non-empty list"}
        exe = command[0]
        if exe == "python":
            if "-m" in command:
                module_index = command.index("-m") + 1
                if module_index < len(command):
                    module_name = command[module_index]
                    module_check = _ensure_python_module(str(module_name), auto_install=auto_install, cwd=self.session_dir)
                    if not module_check.get("ok"):
                        return module_check
            command = [sys.executable] + command[1:]
        elif exe not in ALLOWED_COMMANDS:
            return {"error": f"command not allowed: {exe}"}
        resolved0 = _resolve_executable(str(command[0] or ""))
        if not resolved0:
            missing = str(command[0] or exe)
            return {"error": "executable_not_found", "exe": missing, "hint": _missing_executable_hint(missing)}
        command = [resolved0] + command[1:]
        command = _rewrite_uploads_paths_to_session_dir(command, session_dir=self.session_dir)
        command = _rewrite_existing_session_files_to_abs(command, session_dir=self.session_dir)
        os.makedirs(self.session_dir, exist_ok=True)
        cwd = self.session_dir if not cwd_relative else _safe_join(self.session_dir, cwd_relative)
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        except FileNotFoundError as e:
            return {"error": "executable_not_found", "exe": str(command[0] or exe), "exception": str(e)}
        except Exception as e:
            return {"error": "subprocess_failed", "exe": str(command[0] or exe), "exception": str(e)}

    def export_temp_file(
        self,
        *,
        temp_relative_path: str,
        workspace_relative_path: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        os.makedirs(self.session_dir, exist_ok=True)
        rp = _normalize_relative_file_path(temp_relative_path)
        if not rp:
            return {"error": "invalid temp_relative_path", "temp_relative_path": temp_relative_path}
        try:
            src = _safe_join(self.session_dir, rp)
        except Exception as e:
            return {"error": "invalid temp_relative_path", "temp_relative_path": temp_relative_path, "exception": str(e)}
        if os.path.isdir(src):
            return {"error": "source path is a directory", "temp_relative_path": temp_relative_path, "source": src}
        if not os.path.isfile(src):
            return {"error": "source file not found", "temp_relative_path": temp_relative_path}
        return {
            "source": src,
            "relative_path": temp_relative_path,
            "bytes": os.path.getsize(src),
            "note": "export_temp_file does not copy files; tool marks final output only",
            "requested_name": workspace_relative_path,
            "overwrite": overwrite,
        }
