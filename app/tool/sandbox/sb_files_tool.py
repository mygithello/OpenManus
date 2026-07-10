import asyncio
from typing import Optional

from pydantic import Field

from app.daytona.tool_base import Sandbox, SandboxToolsBase
from app.logger import logger
from app.tool.base import ToolResult

_FILES_DESCRIPTION = """\
基于沙箱的文件系统工具，允许在安全的沙箱环境中进行文件操作。
* 此工具提供在工作区中创建、读取、更新和删除文件的命令
* 所有操作都相对于 /workspace 目录执行以确保安全
* 当您需要在沙箱中管理文件、编辑代码或操作文件内容时使用此工具
主要功能包括：
* 文件创建：使用指定内容和权限创建新文件
* 文件修改：替换特定字符串或完全重写文件
* 文件删除：从工作区中删除文件
* 文件读取：读取文件内容，可选择行范围

【注意】当需要"查看文件"时，使用 action="view" 可以读取文件内容。
"""


class SandboxFilesTool(SandboxToolsBase):
    name: str = "sandbox_files"
    description: str = _FILES_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "view",
                    "create_file",
                    "str_replace",
                    "full_file_rewrite",
                    "delete_file",
                ],
                "description": "要执行的文件操作",
            },
            "file_path": {
                "type": "string",
                "description": "文件路径，相对于 /workspace（例如：'src/main.py'）",
            },
            "file_contents": {
                "type": "string",
                "description": "要写入文件的内容",
            },
            "old_str": {
                "type": "string",
                "description": "要替换的文本（必须恰好出现一次）",
            },
            "new_str": {
                "type": "string",
                "description": "替换文本",
            },
            "permissions": {
                "type": "string",
                "description": "文件权限（八进制格式，例如：'644'）",
                "default": "644",
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "查看文件的起始和结束行号（可选，例如：[1, 20]）",
            },
        },
        "required": ["action"],
        "dependencies": {
            "view": ["file_path"],
            "create_file": ["file_path", "file_contents"],
            "str_replace": ["file_path", "old_str", "new_str"],
            "full_file_rewrite": ["file_path", "file_contents"],
            "delete_file": ["file_path"],
        },
    }
    SNIPPET_LINES: int = Field(default=4, exclude=True)

    def __init__(
        self, sandbox: Optional[Sandbox] = None, **data
    ):
        """使用可选的 sandbox 初始化。"""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    def _file_exists(self, path: str) -> bool:
        """检查文件是否存在于沙箱中"""
        try:
            self.sandbox.fs.get_file_info(path)
            return True
        except Exception:
            return False

    async def execute(
        self,
        action: str,
        file_path: Optional[str] = None,
        file_contents: Optional[str] = None,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        permissions: Optional[str] = "644",
        view_range: Optional[list] = None,
        **kwargs,
    ) -> ToolResult:
        """在沙箱环境中执行文件操作。"""
        async with asyncio.Lock():
            try:
                if action == "view":
                    if not file_path:
                        return self.fail_response("file_path is required for view")
                    return await self._view_file(file_path, view_range)

                elif action == "create_file":
                    if not file_path or not file_contents:
                        return self.fail_response(
                            "file_path and file_contents are required for create_file"
                        )
                    return await self._create_file(file_path, file_contents, permissions)

                elif action == "str_replace":
                    if not file_path or not old_str or not new_str:
                        return self.fail_response(
                            "file_path, old_str, and new_str are required for str_replace"
                        )
                    return await self._str_replace(file_path, old_str, new_str)

                elif action == "full_file_rewrite":
                    if not file_path or not file_contents:
                        return self.fail_response(
                            "file_path and file_contents are required for full_file_rewrite"
                        )
                    return await self._full_file_rewrite(file_path, file_contents, permissions)

                elif action == "delete_file":
                    if not file_path:
                        return self.fail_response("file_path is required for delete_file")
                    return await self._delete_file(file_path)

                else:
                    return self.fail_response(f"Unknown action: {action}")

            except Exception as e:
                logger.error(f"Error executing file action: {e}")
                return self.fail_response(f"Error executing file action: {e}")

    async def _view_file(self, file_path: str, view_range: Optional[list] = None) -> ToolResult:
        """读取文件内容。"""
        try:
            await self._ensure_sandbox()
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"

            if not self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' does not exist")

            content = self.sandbox.fs.download_file(full_path).decode()
            lines = content.split("\n")
            total_lines = len(lines)

            if view_range and len(view_range) == 2:
                start, end = view_range
                # Convert to 0-indexed
                start_idx = max(0, start - 1)
                end_idx = min(total_lines, end)
                selected = lines[start_idx:end_idx]
                output = "\n".join(selected)
                return self.success_response(
                    f"File '{file_path}' ({total_lines} lines, showing lines {start}-{end}):\n```\n{output}\n```"
                )
            else:
                return self.success_response(
                    f"File '{file_path}' ({total_lines} lines):\n```\n{content}\n```"
                )

        except Exception as e:
            return self.fail_response(f"Error viewing file: {str(e)}")

    async def _create_file(self, file_path: str, file_contents: str, permissions: str = "644") -> ToolResult:
        """使用提供的内容创建新文件"""
        try:
            await self._ensure_sandbox()
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"

            if self._file_exists(full_path):
                return self.fail_response(
                    f"File '{file_path}' already exists. Use full_file_rewrite to modify existing files."
                )

            # 如果需要，创建父目录
            parent_dir = "/".join(full_path.split("/")[:-1])
            if parent_dir:
                self.sandbox.fs.create_folder(parent_dir, "755")

            # 写入文件内容
            self.sandbox.fs.upload_file(file_contents.encode(), full_path)
            self.sandbox.fs.set_file_permissions(full_path, permissions)

            message = f"File '{file_path}' created successfully."

            # 检查是否创建了 index.html 并添加 8080 服务器信息
            if file_path.lower() == "index.html":
                try:
                    website_link = self.sandbox.get_preview_link(8080)
                    website_url = (
                        website_link.url
                        if hasattr(website_link, "url")
                        else str(website_link)
                    )
                    message += f"\n\n[Auto-detected index.html - HTTP server available at: {website_url}]"
                except Exception as e:
                    logger.warning(f"Failed to get website URL for index.html: {str(e)}")

            return self.success_response(message)
        except Exception as e:
            return self.fail_response(f"Error creating file: {str(e)}")

    async def _str_replace(self, file_path: str, old_str: str, new_str: str) -> ToolResult:
        """替换文件中的特定文本"""
        try:
            await self._ensure_sandbox()
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"

            if not self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' does not exist")

            content = self.sandbox.fs.download_file(full_path).decode()
            old_str = old_str.expandtabs()
            new_str = new_str.expandtabs()

            occurrences = content.count(old_str)
            if occurrences == 0:
                return self.fail_response(f"String '{old_str}' not found in file")
            if occurrences > 1:
                lines = [
                    i + 1
                    for i, line in enumerate(content.split("\n"))
                    if old_str in line
                ]
                return self.fail_response(
                    f"Multiple occurrences found in lines {lines}. Please ensure string is unique"
                )

            # 执行替换
            new_content = content.replace(old_str, new_str)
            self.sandbox.fs.upload_file(new_content.encode(), full_path)

            return self.success_response("Replacement successful.")
        except Exception as e:
            return self.fail_response(f"Error replacing string: {str(e)}")

    async def _full_file_rewrite(self, file_path: str, file_contents: str, permissions: str = "644") -> ToolResult:
        """使用新内容完全重写现有文件"""
        try:
            await self._ensure_sandbox()
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"

            if not self._file_exists(full_path):
                return self.fail_response(
                    f"File '{file_path}' does not exist. Use create_file to create a new file."
                )

            self.sandbox.fs.upload_file(file_contents.encode(), full_path)
            self.sandbox.fs.set_file_permissions(full_path, permissions)

            message = f"File '{file_path}' completely rewritten successfully."

            if file_path.lower() == "index.html":
                try:
                    website_link = self.sandbox.get_preview_link(8080)
                    website_url = (
                        website_link.url
                        if hasattr(website_link, "url")
                        else str(website_link)
                    )
                    message += f"\n\n[Auto-detected index.html - HTTP server available at: {website_url}]"
                except Exception as e:
                    logger.warning(f"Failed to get website URL: {str(e)}")

            return self.success_response(message)
        except Exception as e:
            return self.fail_response(f"Error rewriting file: {str(e)}")

    async def _delete_file(self, file_path: str) -> ToolResult:
        """删除给定路径的文件"""
        try:
            await self._ensure_sandbox()
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"

            if not self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' does not exist")

            self.sandbox.fs.delete_file(full_path)
            return self.success_response(f"File '{file_path}' deleted successfully.")
        except Exception as e:
            return self.fail_response(f"Error deleting file: {str(e)}")

    async def cleanup(self):
        """清理沙箱资源。"""
        pass
