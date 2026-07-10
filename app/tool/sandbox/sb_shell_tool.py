import asyncio
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from app.daytona.tool_base import Sandbox, SandboxToolsBase
from app.daytona.sandbox import SessionExecuteRequest
from app.logger import logger
from app.tool.base import ToolResult

_SHELL_DESCRIPTION = """\
在工作区目录中执行 shell 命令。
重要：命令默认是非阻塞的，在 tmux 会话中运行。
这非常适合长时间运行的操作，如启动服务器或构建过程。
使用会话来维护命令之间的状态。
此工具对于运行 CLI 工具、安装包和管理系统操作至关重要。
"""


class SandboxShellTool(SandboxToolsBase):
    """用于在 Daytona 沙箱中执行 shell 命令的工具。"""

    name: str = "sandbox_shell"
    description: str = _SHELL_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "execute_command",
                    "check_command_output",
                    "terminate_command",
                    "list_commands",
                ],
                "description": "要执行的 shell 操作",
            },
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令。可用于运行 CLI 工具、安装包或系统操作。",
            },
            "folder": {
                "type": "string",
                "description": "可选的相对路径，指向 /workspace 的子目录，命令应在此目录中执行。",
            },
            "session_name": {
                "type": "string",
                "description": "要使用的 tmux 会话的可选名称。默认为随机会话名称。",
            },
            "blocking": {
                "type": "boolean",
                "description": "是否等待命令完成。默认为 false，用于非阻塞执行。",
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": "阻塞命令的可选超时时间（秒）。默认为 60。",
                "default": 60,
            },
            "kill_session": {
                "type": "boolean",
                "description": "检查后是否终止 tmux 会话。",
                "default": False,
            },
        },
        "required": ["action"],
        "dependencies": {
            "execute_command": ["command"],
            "check_command_output": ["session_name"],
            "terminate_command": ["session_name"],
            "list_commands": [],
        },
    }

    def __init__(
        self, sandbox: Optional[Sandbox] = None, **data
    ):
        """使用可选的 sandbox 初始化。"""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    async def _ensure_session(self, session_name: str = "default") -> str:
        """确保会话存在并返回其 ID。"""
        if session_name not in self._sessions:
            session_id = str(uuid4())
            try:
                await self._ensure_sandbox()
                self.sandbox.process.create_session(session_id)
                self._sessions[session_name] = session_id
            except Exception as e:
                raise RuntimeError(f"Failed to create session: {str(e)}")
        return self._sessions[session_name]

    async def _cleanup_session(self, session_name: str):
        """如果会话存在，则清理它。"""
        if session_name in self._sessions:
            try:
                await self._ensure_sandbox()
                self.sandbox.process.delete_session(self._sessions[session_name])
                del self._sessions[session_name]
            except Exception as e:
                print(f"Warning: Failed to cleanup session {session_name}: {str(e)}")

    async def _execute_raw_command(self, command: str) -> Dict[str, Any]:
        """直接在沙箱中执行原始命令。"""
        session_id = await self._ensure_session("raw_commands")

        req = SessionExecuteRequest(
            command=command, run_async=False, cwd=self.workspace_path
        )

        response = self.sandbox.process.execute_session_command(
            session_id=session_id,
            req=req,
            timeout=30,
        )

        logs = self.sandbox.process.get_session_command_logs(
            session_id=session_id, command_id=response.cmd_id
        )

        return {"output": logs, "exit_code": response.exit_code}

    async def _execute_command(
        self,
        command: str,
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60,
    ) -> ToolResult:
        try:
            await self._ensure_sandbox()

            cwd = self.workspace_path
            if folder:
                folder = folder.strip("/")
                cwd = f"{self.workspace_path}/{folder}"

            if not session_name:
                session_name = f"session_{str(uuid4())[:8]}"

            # 检查 tmux 会话是否已存在
            check_session = await self._execute_raw_command(
                f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'"
            )
            session_exists = "not_exists" not in check_session.get("output", "")

            if not session_exists:
                await self._execute_raw_command(f"tmux new-session -d -s {session_name}")

            # 将命令发送到 tmux 会话
            full_command = f"cd {cwd} && {command}"
            wrapped_command = full_command.replace('"', '\\"')
            await self._execute_raw_command(
                f'tmux send-keys -t {session_name} "{wrapped_command}" Enter'
            )

            if blocking:
                start_time = time.time()
                while (time.time() - start_time) < timeout:
                    time.sleep(2)

                    check_result = await self._execute_raw_command(
                        f"tmux has-session -t {session_name} 2>/dev/null || echo 'ended'"
                    )
                    if "ended" in check_result.get("output", ""):
                        break

                    output_result = await self._execute_raw_command(
                        f"tmux capture-pane -t {session_name} -p -S - -E -"
                    )
                    current_output = output_result.get("output", "")

                    last_lines = current_output.split("\n")[-3:]
                    completion_indicators = ["$", "#", ">", "Done", "Completed", "Finished", "✓"]
                    if any(
                        indicator in line
                        for indicator in completion_indicators
                        for line in last_lines
                    ):
                        break

                output_result = await self._execute_raw_command(
                    f"tmux capture-pane -t {session_name} -p -S - -E -"
                )
                final_output = output_result.get("output", "")

                await self._execute_raw_command(f"tmux kill-session -t {session_name}")

                return self.success_response({
                    "output": final_output,
                    "session_name": session_name,
                    "cwd": cwd,
                    "completed": True,
                })
            else:
                return self.success_response({
                    "session_name": session_name,
                    "cwd": cwd,
                    "message": f"Command sent to tmux session '{session_name}'. Use check_command_output to view results.",
                    "completed": False,
                })

        except Exception as e:
            if session_name:
                try:
                    await self._execute_raw_command(f"tmux kill-session -t {session_name}")
                except:
                    pass
            return self.fail_response(f"Error executing command: {str(e)}")

    async def _check_command_output(
        self, session_name: str, kill_session: bool = False
    ) -> ToolResult:
        try:
            await self._ensure_sandbox()

            check_result = await self._execute_raw_command(
                f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'"
            )
            if "not_exists" in check_result.get("output", ""):
                return self.fail_response(f"Tmux session '{session_name}' does not exist.")

            output_result = await self._execute_raw_command(
                f"tmux capture-pane -t {session_name} -p -S - -E -"
            )
            output = output_result.get("output", "")

            if kill_session:
                await self._execute_raw_command(f"tmux kill-session -t {session_name}")
                termination_status = "Session terminated."
            else:
                termination_status = "Session still running."

            return self.success_response({
                "output": output,
                "session_name": session_name,
                "status": termination_status,
            })

        except Exception as e:
            return self.fail_response(f"Error checking command output: {str(e)}")

    async def _terminate_command(self, session_name: str) -> ToolResult:
        try:
            await self._ensure_sandbox()

            check_result = await self._execute_raw_command(
                f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'"
            )
            if "not_exists" in check_result.get("output", ""):
                return self.fail_response(f"Tmux session '{session_name}' does not exist.")

            await self._execute_raw_command(f"tmux kill-session -t {session_name}")

            return self.success_response({"message": f"Tmux session '{session_name}' terminated successfully."})

        except Exception as e:
            return self.fail_response(f"Error terminating command: {str(e)}")

    async def _list_commands(self) -> ToolResult:
        try:
            await self._ensure_sandbox()

            result = await self._execute_raw_command(
                "tmux list-sessions 2>/dev/null || echo 'No sessions'"
            )
            output = result.get("output", "")

            if "No sessions" in output or not output.strip():
                return self.success_response({"message": "No active tmux sessions found.", "sessions": []})

            sessions = []
            for line in output.split("\n"):
                if line.strip():
                    parts = line.split(":")
                    if parts:
                        session_name = parts[0].strip()
                        sessions.append(session_name)

            return self.success_response({
                "message": f"Found {len(sessions)} active sessions.",
                "sessions": sessions,
            })

        except Exception as e:
            return self.fail_response(f"Error listing commands: {str(e)}")

    async def execute(
        self,
        action: str,
        command: Optional[str] = None,
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60,
        kill_session: bool = False,
        **kwargs,
    ) -> ToolResult:
        async with asyncio.Lock():
            try:
                if action == "execute_command":
                    if not command:
                        return self.fail_response("command is required for execute_command")
                    return await self._execute_command(command, folder, session_name, blocking, timeout)
                elif action == "check_command_output":
                    if session_name is None:
                        return self.fail_response("session_name is required for check_command_output")
                    return await self._check_command_output(session_name, kill_session)
                elif action == "terminate_command":
                    if session_name is None:
                        return self.fail_response("session_name is required for terminate_command")
                    return await self._terminate_command(session_name)
                elif action == "list_commands":
                    return await self._list_commands()
                else:
                    return self.fail_response(f"Unknown action: {action}")
            except Exception as e:
                logger.error(f"Error executing shell action: {e}")
                return self.fail_response(f"Error executing shell action: {e}")

    async def cleanup(self):
        """清理所有会话。"""
        for session_name in list(self._sessions.keys()):
            await self._cleanup_session(session_name)

        try:
            await self._ensure_sandbox()
            await self._execute_raw_command("tmux kill-server 2>/dev/null || true")
        except Exception as e:
            logger.error(f"Error during shell cleanup: {e}")
