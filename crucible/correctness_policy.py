"""Common claimed correctness goal; manual review assesses conditional reasoning."""

POLICY_ID = "crucible-honest-agreement-128-v1"
FAILURE_EXPONENT = 128
REQUIREMENT = (
    "Every admitted configuration must claim honest decapsulation to the same "
    "shared secret with failure probability at most 2^-128, or perfect correctness. "
    "The guarantee may depend on explicit conjectural or modeling assumptions; "
    "their proof is not an admission prerequisite. Explain the conditional "
    "reasoning and its unproved steps. Research review does not turn assumptions "
    "or conjectures into proven guarantees."
)
SCOPE = (
    "Probability is over the specified honest key-generation and encapsulation "
    "randomness. Honest decapsulation rejection counts as failure. Any excluded "
    "generation/resource aborts and their conditioning must be explicit and "
    "reported separately; failures cannot be silently retried. Stronger claimed "
    "bounds remain visible. This is a common claimed reliability goal, not a "
    "security-bit claim, per-key guarantee or NIST-mandated numerical cutoff. "
    "A security reduction or usage requirement may demand a stricter bound. "
    "A proof gap alone does not refute a conditional claim; rigorous evidence "
    "that its claimed bound is violated is grounds for rejection or invalidation "
    "after substantive review."
)


def review_gaps(correctness: dict) -> list[str]:
    """Check a statistical target declaration, never parse or prove its bound."""
    if correctness.get("kind") != "statistical":
        return []  # Perfect/unknown label requirements are checked by their callers.
    exponent = correctness.get("failure_bound_exponent")
    if type(exponent) is not int or exponent < FAILURE_EXPONENT:
        return [
            "Statistical correctness requires failure_bound_exponent >=128 for "
            "approval/admission, declaring the common failure goal of at most "
            "2^-128 under explicit dependencies. Conjectural or modeled "
            "dependencies are allowed; weaker or missing targets remain drafts. "
            "The reviewer must check consistency with the bound text and reasoning."
        ]
    return []
