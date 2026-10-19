"""Recognize a review that could not run, without turning a denial into approval."""
import json

CAPACITY_REVIEW_ERROR = (
    "Automatic approval review failed: Selected model is at capacity. Please try a different model.\n"
    "The action was not executed because automatic approval review could not be completed. "
    "This is a review failure, not a determination that the action is unsafe. "
    "Do not bypass the approval check; resolve the error or ask the user for guidance."
)


def capacity_review_error(error):
    """Accept only the complete not-executed diagnostic, never a quoted fragment or denial."""
    if not isinstance(error, str) or len(error) > 8192:
        return False
    text = error.strip()
    # Codex can expose the same message through its exec tool's nested error.
    # Decode only this observed wrapper; do not search arbitrary log/tool prose.
    prefix = 'exec_command failed: CreateProcess { message: '
    if text.startswith(prefix) and text.endswith(' }'):
        try:
            text = json.loads(text[len(prefix):-2])
            if not isinstance(text, str) or not text.startswith('Rejected(') or not text.endswith(')'):
                return False
            text = json.loads(text[len('Rejected('):-1])
        except (ValueError, RecursionError):
            return False
    # Preserve all words, including the explicit non-execution and no-bypass
    # statements; tolerate only whitespace/layout differences in the saved text.
    return isinstance(text, str) and ' '.join(text.split()) == ' '.join(CAPACITY_REVIEW_ERROR.split())
