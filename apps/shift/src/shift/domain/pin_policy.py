"""PIN acceptance rules (S4, design §3): six digits, no guessable pattern, pure."""

from enum import StrEnum

PIN_LENGTH = 6
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
DIGITS = "0123456789"
_PERSIAN_ZERO = 0x06F0
_ARABIC_ZERO = 0x0660
_JALALI_YEARS = range(1300, 1421)
_GREGORIAN_YEARS = range(1900, 2031)


class PinProblem(StrEnum):
    LENGTH = "pin_length"
    NOT_DIGITS = "pin_not_digits"
    REPEATED = "pin_repeated"
    SEQUENCE = "pin_sequence"
    YEAR = "pin_contains_year"


class PinPolicyError(ValueError):
    """The chosen PIN is too guessable; `problem` names the rule that rejected it."""

    def __init__(self, problem: PinProblem) -> None:
        super().__init__(f"PIN rejected: {problem.value}")
        self.problem = problem


def normalise_digits(text: str) -> str:
    """Map Persian `۰-۹` and Arabic-Indic `٠-٩` digits to ASCII; phone keyboards send both."""
    translated = []
    for character in text.strip():
        if "۰" <= character <= "۹":
            translated.append(chr(ord(character) - _PERSIAN_ZERO + ord("0")))
        elif "٠" <= character <= "٩":
            translated.append(chr(ord(character) - _ARABIC_ZERO + ord("0")))
        else:
            translated.append(character)
    return "".join(translated)


def _is_repeated(pin: str) -> bool:
    return len(set(pin)) == 1


def _is_sequence(pin: str) -> bool:
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:], strict=False)}
    return len(steps) == 1 and steps <= {1, -1}


def _contains_year(pin: str) -> bool:
    """Any four-digit window that reads as a Jalali or Gregorian year is refused.

    No birth date is stored for staff (S03 §1), so the policy bans year-shaped windows
    instead of guessing which one is the holder's own birth year.
    """
    windows = (int(pin[index : index + 4]) for index in range(len(pin) - 3))
    return any(window in _JALALI_YEARS or window in _GREGORIAN_YEARS for window in windows)


def validate_new_pin(pin: str) -> str:
    """Return the normalised PIN, or raise `PinPolicyError` naming the failed rule."""
    candidate = normalise_digits(pin)
    if not candidate:
        raise PinPolicyError(PinProblem.LENGTH)
    if not candidate.isascii() or not candidate.isdigit():
        raise PinPolicyError(PinProblem.NOT_DIGITS)
    if len(candidate) != PIN_LENGTH:
        raise PinPolicyError(PinProblem.LENGTH)
    if _is_repeated(candidate):
        raise PinPolicyError(PinProblem.REPEATED)
    if _is_sequence(candidate):
        raise PinPolicyError(PinProblem.SEQUENCE)
    if _contains_year(candidate):
        raise PinPolicyError(PinProblem.YEAR)
    return candidate
