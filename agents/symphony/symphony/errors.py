"""Typed errors with stable portable categories (DESIGN.md "Errors")."""


class SymphonyError(Exception):
    """Base error. `category` is stable; the message is human-readable and secret-free."""

    def __init__(self, category: str, message: str = ""):
        super().__init__(f"{category}: {message}" if message else category)
        self.category = category
        self.message = message


class WorkflowError(SymphonyError):
    """missing_workflow_file, workflow_parse_error, workflow_front_matter_not_a_map,
    template_parse_error, template_render_error."""


class ConfigError(SymphonyError):
    """invalid_config, unsupported_tracker_kind, invalid_tracker_config,
    missing_tracker_secret, missing_codex_command."""


class TrackerError(SymphonyError):
    """tracker_request, tracker_status, tracker_response, tracker_pagination,
    tracker_rate_limited, tracker_auth (Linear MCP login missing or expired: run
    `symphony login`), tracker_tool_error (an MCP tool reported an error)."""


class WorkspaceError(SymphonyError):
    """workspace_path_outside_root, workspace_not_directory, workspace_create_failed,
    invalid_workspace_cwd."""


class HookError(SymphonyError):
    """hook_failed, hook_timeout."""

    def __init__(self, category: str, message: str = "", *, hook: str = "",
                 exit_code: int | None = None):
        super().__init__(category, message)
        self.hook = hook
        self.exit_code = exit_code


class AgentError(SymphonyError):
    """codex_not_found, invalid_workspace_cwd, response_timeout, turn_timeout, port_exit,
    response_error, turn_failed, turn_cancelled, turn_input_required, startup_failed,
    thread_resume_failed."""
