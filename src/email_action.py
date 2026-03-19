"""Custom browser-use action for fetching email verification codes."""

from browser_use.tools.service import Tools

from src.email_reader import poll_for_verification_code


def create_email_tools() -> Tools:
    """Create a Tools instance with the email verification code action."""
    tools = Tools()

    @tools.action(
        "Fetch a verification code sent to the applicant's email. "
        "Use this when a form asks for a verification code, OTP, or security "
        "code that was sent to the applicant's email address. "
        "Returns the code as a string, or 'NOT_FOUND' if no code was received. "
        "No parameters needed — just call it."
    )
    async def get_email_verification_code() -> str:
        """Poll the applicant's email inbox for a recent verification code."""
        code = await poll_for_verification_code(
            max_wait_seconds=60,
            poll_interval=8,
            max_age_seconds=300,
        )
        return code or "NOT_FOUND"

    return tools
