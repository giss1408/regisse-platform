"""Phone numbers: Côte d'Ivoire normalisation to E.164, masking, WhatsApp click-to-chat links."""

import re
from urllib.parse import quote

_LOCAL_CI = re.compile(r"^0[157]\d{8}$")  # 10 digits, 01 / 05 / 07
_DE_MOBILE = re.compile(r"^1[5-7]\d{8,9}$")


class InvalidPhone(ValueError):
    pass


def _test_phone(digits: str) -> str | None:
    for prefix in ("+49", "0049"):
        if digits.startswith(prefix) and _DE_MOBILE.match(digits[len(prefix):].lstrip("0")):
            return "+49" + digits[len(prefix):].lstrip("0")
    return None


def normalize_ci_phone(raw: str, *, allow_test_numbers: bool = False) -> str:
    """Accept 0701020304, 07 01 02 03 04, +225 07…, 00225 07…; return E.164 (+225 + 10 digits).

    With `allow_test_numbers`, German mobiles (+49 15x/16x/17x) are accepted too: for testing
    from Germany outside production. The caller decides, the kit has no environment.
    """
    digits = re.sub(r"[\s.\-()/]", "", raw or "")
    if allow_test_numbers:
        test = _test_phone(digits)
        if test:
            return test
    if digits.startswith("00225"):
        digits = digits[5:]
    elif digits.startswith("+225"):
        digits = digits[4:]
    elif digits.startswith("225") and len(digits) == 13:
        digits = digits[3:]
    if not _LOCAL_CI.match(digits):
        raise InvalidPhone("Numéro ivoirien à 10 chiffres attendu")
    return "+225" + digits


def mask_phone(phone_e164: str) -> str:
    """+2250701020345 -> +225 07 •• •• 45"""
    if phone_e164.startswith("+225") and len(phone_e164) == 14:
        local = phone_e164[4:]
        return f"+225 {local[:2]} •• •• {local[-2:]}"
    return phone_e164[:4] + " •• " + phone_e164[-2:]


def phone_key(phone_e164: str) -> str:
    """The key fidelia uses for phone-keyed data (`tel:+225...`); carried as the `ph` claim in SSO tokens."""
    return f"tel:{phone_e164}"


def wa_link(phone_e164: str, text: str) -> str:
    """WhatsApp click-to-chat link with the message already written."""
    return f"https://wa.me/{phone_e164.lstrip('+')}?text={quote(text)}"
