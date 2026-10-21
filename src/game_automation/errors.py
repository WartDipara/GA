from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable wire codes with default messages shared by the kernel and plugins."""

    AAPT2_TIMEOUT = ("AAPT2_TIMEOUT", "AAPT2 inspection timed out")
    AAPT2_UNAVAILABLE = ("AAPT2_UNAVAILABLE", "Install Android Build Tools or set GAME_AUTO_AAPT2")
    ACTION_ALREADY_DISPATCHED = (
        "ACTION_ALREADY_DISPATCHED",
        "Uncertain actions need reconciliation; resume cannot replay them",
    )
    ADB_COMMAND_FAILED = ("ADB_COMMAND_FAILED", "ADB command failed")
    ADB_START_FAILED = ("ADB_START_FAILED", "Cannot start ADB")
    ADB_TIMEOUT = ("ADB_TIMEOUT", "ADB command timed out")
    ADB_UNAVAILABLE = ("ADB_UNAVAILABLE", "ADB executable is unavailable")
    AI_OUTPUT_INVALID = ("AI_OUTPUT_INVALID", "Final message is not JSON")
    AI_OUTPUT_MISSING = ("AI_OUTPUT_MISSING", "Completed turn has no final message")
    AI_REQUIRES_INPUT = ("AI_REQUIRES_INPUT", "Codex requested external action or approval")
    AI_TURN_FAILED = ("AI_TURN_FAILED", "AI turn failed")
    AI_UNAVAILABLE = ("AI_UNAVAILABLE", "Codex authentication or quota requires attention")
    AMBIGUOUS_CAPABILITY = ("AMBIGUOUS_CAPABILITY", "Ambiguous capability")
    APK_ARCHIVE_LIMIT = ("APK_ARCHIVE_LIMIT", "APK archive exceeds inspection limits")
    APK_INPUT_REQUIRED = (
        "APK_INPUT_REQUIRED",
        "Resume preprocessing with --input pointing to the original APK list",
    )
    APK_INPUT_TOO_LARGE = ("APK_INPUT_TOO_LARGE", "APK input file exceeds 4 MiB")
    APK_PACKAGE_MISSING = ("APK_PACKAGE_MISSING", "AAPT2 did not return a valid package name")
    APK_PARSE_FAILED = ("APK_PARSE_FAILED", "AAPT2 could not inspect the downloaded APK")
    APK_SOURCE_CHANGED = (
        "APK_SOURCE_CHANGED",
        "Provide the original APK list file to resume this batch",
    )
    APK_TOO_LARGE = ("APK_TOO_LARGE", "APK exceeds the configured size limit")
    BUDGET_EXHAUSTED = ("BUDGET_EXHAUSTED", "Run time limit exceeded")
    CALL_FAILED = ("CALL_FAILED", "Call failed")
    CANCELLED = ("CANCELLED", "Cancellation requested")
    CAPABILITY_DENIED = ("CAPABILITY_DENIED", "Requested capability is not permitted")
    CLOSED = ("CLOSED", "Connection closed")
    CODEX_UNAVAILABLE = ("CODEX_UNAVAILABLE", "Codex executable was not found")
    CODEX_VERSION_MISMATCH = ("CODEX_VERSION_MISMATCH", "Codex version mismatch")
    DEPENDENCY_CYCLE = ("DEPENDENCY_CYCLE", "Capability call cycle or depth limit")
    DEVICE_NOT_BOOTED = ("DEVICE_NOT_BOOTED", "Device not booted")
    DEVICE_NOT_IMPLEMENTED = ("DEVICE_NOT_IMPLEMENTED", "Device execution is not available")
    DEVICE_SELECTION_REQUIRED = ("DEVICE_SELECTION_REQUIRED", "Device selection required")
    DEVICE_UNAVAILABLE = ("DEVICE_UNAVAILABLE", "Device unavailable")
    DOCUMENT_TOO_LARGE = ("DOCUMENT_TOO_LARGE", "Document exceeds the 200,000-byte limit")
    DOWNLOAD_ENCODING = ("DOWNLOAD_ENCODING", "Server ignored the identity encoding request")
    DOWNLOAD_HTTP_ERROR = ("DOWNLOAD_HTTP_ERROR", "Download returned an unsuccessful HTTP status")
    DOWNLOAD_INCOMPLETE = ("DOWNLOAD_INCOMPLETE", "Response ended before the APK was complete")
    DOWNLOAD_LENGTH_INVALID = ("DOWNLOAD_LENGTH_INVALID", "Invalid Content-Length")
    DOWNLOAD_NETWORK_ERROR = ("DOWNLOAD_NETWORK_ERROR", "Download network error")
    DOWNLOAD_RANGE_INVALID = ("DOWNLOAD_RANGE_INVALID", "Unexpected partial response")
    DOWNLOAD_RESTART = ("DOWNLOAD_RESTART", "Range rejected; restarting download")
    DOWNLOAD_STATE_INVALID = ("DOWNLOAD_STATE_INVALID", "Download checkpoint is invalid")
    ENVIRONMENT_BLOCKED = ("ENVIRONMENT_BLOCKED", "Environment blocked")
    EVENT_ID_CONFLICT = ("EVENT_ID_CONFLICT", "Event ID was reused for different content")
    HANDLER_FAILED = ("HANDLER_FAILED", "Handler failed")
    IDEMPOTENCY_CONFLICT = ("IDEMPOTENCY_CONFLICT", "Submission key belongs to another task")
    INTAKE_NEEDS_INPUT = ("INTAKE_NEEDS_INPUT", "Provide the requested information before resume")
    INTAKE_NEW_RUN_REQUIRED = (
        "INTAKE_NEW_RUN_REQUIRED",
        "Accepted input changed; create a new run",
    )
    INTAKE_OUTPUT_INVALID = (
        "INTAKE_OUTPUT_INVALID",
        "A ready result needs a task and no questions",
    )
    INTERNAL_ERROR = ("INTERNAL_ERROR", "Internal error")
    INVALID_ADB_OUTPUT = ("INVALID_ADB_OUTPUT", "Invalid ADB output")
    INVALID_ANDROID_PACKAGE = ("INVALID_ANDROID_PACKAGE", "Invalid Android package identifier")
    INVALID_APK = ("INVALID_APK", "APK archive or entry checksums are invalid")
    INVALID_APK_URL = ("INVALID_APK_URL", "APK URLs must be HTTP(S) without userinfo or fragments")
    INVALID_CONTRACT = ("INVALID_CONTRACT", "Payload does not match its contract")
    INVALID_DOCUMENT = ("INVALID_DOCUMENT", "Document is empty or contains null bytes")
    INVALID_ENCODING = ("INVALID_ENCODING", "Document must be UTF-8")
    INVALID_EVENT = ("INVALID_EVENT", "Unsupported plugin event kind")
    INVALID_PATH = ("INVALID_PATH", "Path escapes its assigned root")
    INVALID_PLUGIN = ("INVALID_PLUGIN", "Plugin name mismatch")
    INVALID_PROTOCOL = ("INVALID_PROTOCOL", "Expected JSON-RPC 2.0 object")
    INVALID_RUN_LIMIT = ("INVALID_RUN_LIMIT", "Run list limit must be between 1 and 200")
    INVALID_RUN_NAME = (
        "INVALID_RUN_NAME",
        "Run name must start with a letter or digit and use 1 to 40 ASCII letters, "
        "digits, dots, underscores, or hyphens",
    )
    INVALID_SKILL = ("INVALID_SKILL", "Invalid skill name")
    INVALID_STAGE = ("INVALID_STAGE", "Invalid stage")
    INVALID_STATE = ("INVALID_STATE", "Operation is not allowed in the current state")
    LOCAL_APK_CHANGED = ("LOCAL_APK_CHANGED", "Completed APK differs from its checkpoint")
    LOCAL_DOWNLOAD_CHANGED = ("LOCAL_DOWNLOAD_CHANGED", "Partial APK differs from its checkpoint")
    LOCAL_ERROR = ("LOCAL_ERROR", "Local error")
    MESSAGE_TOO_LARGE = ("MESSAGE_TOO_LARGE", "Use artifact references for large payloads")
    METHOD_NOT_FOUND = ("METHOD_NOT_FOUND", "Inbound method is not supported")
    MISSING_CAPABILITY = ("MISSING_CAPABILITY", "Missing capability")
    NOT_FOUND = ("NOT_FOUND", "Requested resource was not found")
    PACKAGE_UNAVAILABLE = ("PACKAGE_UNAVAILABLE", "Package unavailable")
    PLUGIN_INCOMPATIBLE = ("PLUGIN_INCOMPATIBLE", "Handshake differs from registered manifest")
    PLUGIN_UNHEALTHY = ("PLUGIN_UNHEALTHY", "Plugin health check failed")
    PREPROCESSING_BLOCKED = (
        "PREPROCESSING_BLOCKED",
        "Some APKs need attention; inspect item errors and resume with --input",
    )
    PREPROCESSING_INTERRUPTED = (
        "PREPROCESSING_INTERRUPTED",
        "APK preprocessing was interrupted; resume with the original input file",
    )
    PREVIOUS_PROCESS_EXITED = ("PREVIOUS_PROCESS_EXITED", "Previous process exited")
    PROCESS_EXITED = ("PROCESS_EXITED", "Protocol stream ended before completion")
    PROJECT_NOT_FOUND = ("PROJECT_NOT_FOUND", "Run from the repository or pass --project")
    RPC_ERROR = ("RPC_ERROR", "Remote procedure call failed")
    RUNTIME_CHANGED = ("RUNTIME_CHANGED", "Runtime files or interpreter changed; use a new batch")
    RUN_BUSY = ("RUN_BUSY", "Another local execution owns the device/workflow lock")
    RUN_MODE_REMOVED = ("RUN_MODE_REMOVED", "This run mode is no longer supported")
    RUN_TERMINAL = ("RUN_TERMINAL", "Use a new execution batch for a terminal run")
    SCHEMA_VERSION = ("SCHEMA_VERSION", "Unsupported database version")
    SKILL_MISSING = ("SKILL_MISSING", "Required runtime Skill is missing")
    SNAPSHOT_CHANGED = ("SNAPSHOT_CHANGED", "Manifest does not match its persisted digest")
    SNAPSHOT_MISSING = ("SNAPSHOT_MISSING", "Run has no completed snapshot; create a new run")
    TIMEOUT = ("TIMEOUT", "Operation timed out")
    TRACE_UNAVAILABLE = ("TRACE_UNAVAILABLE", "Cannot persist trace; execution halted")
    UNSUPPORTED_DOCUMENT = ("UNSUPPORTED_DOCUMENT", "Supported documents: UTF-8 .txt and .md")
    UNSUPPORTED_PLATFORM = ("UNSUPPORTED_PLATFORM", "Unsupported platform")

    def __new__(cls, code: str, message: str):
        member = str.__new__(cls, code)
        member._value_ = code
        member.message = message
        return member
