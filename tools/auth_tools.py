"""
LangChain @tool wrappers for the Auth MCP (Fernet-encrypted key vault).

Agents use auth_store_key at setup time and auth_get_key when making
authenticated external API calls.  auth_validate lets an agent check for
credentials without retrieving the plaintext value.
"""
from langchain_core.tools import tool
from mcp.auth import get_auth_mcp

_mcp = get_auth_mcp()


@tool
def auth_store_key(service: str, key: str) -> str:
    """Securely store an API key or token for a named service.

    The key is encrypted with Fernet symmetric encryption before being
    persisted to SQLite. If the service already has a stored key it is
    overwritten.

    Args:
        service: Service identifier, e.g. 'openai', 'github', 'stripe'.
        key: The secret API key or bearer token string.

    Returns:
        'stored' on success, or 'ERROR: ...' if encryption is unavailable.
    """
    return _mcp.store(service, key).to_tool_str()


@tool
def auth_get_key(service: str) -> str:
    """Retrieve a previously stored API key for a service.

    Args:
        service: Service identifier to look up.

    Returns:
        The decrypted key string on success,
        or 'ERROR: No key stored for service ...' if not found.
    """
    return _mcp.retrieve(service).to_tool_str()


@tool
def auth_validate(service: str) -> str:
    """Check whether a stored and decryptable key exists for a service.

    Use this before making authenticated API calls to verify credentials are set.

    Args:
        service: Service identifier to check.

    Returns:
        'True' if a valid key exists, 'False' otherwise.
    """
    return _mcp.validate(service).to_tool_str()


@tool
def auth_list_services() -> str:
    """List all service names that have a stored API key.

    Returns the service identifiers only — no plaintext keys are exposed.

    Returns:
        A JSON array of service name strings, e.g. '["github", "openai"]',
        or 'ERROR: ...' on failure.
    """
    return _mcp.list_services().to_tool_str()


@tool
def auth_revoke(service: str) -> str:
    """Delete the stored API key for a service, preventing future retrieval.

    Args:
        service: Service identifier whose key should be permanently deleted.

    Returns:
        'revoked' on success, or 'ERROR: ...' if no key was found.
    """
    return _mcp.revoke(service).to_tool_str()
