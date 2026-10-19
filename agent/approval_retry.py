"""Recognize a review that could not run, without turning a denial into approval."""

CAPACITY_REVIEW_ERROR = (
    "Automatic approval review failed: Selected model is at capacity. Please try a different model.\n"
    "The action was not executed because automatic approval review could not be completed. "
    "This is a review failure, not a determination that the action is unsafe. "
    "Do not bypass the approval check; resolve the error or ask the user for guidance."
)


def capacity_review_error(error):
    """Accept only the complete not-executed diagnostic, never a quoted fragment or denial."""
    return isinstance(error, str) and error.strip() == CAPACITY_REVIEW_ERROR
