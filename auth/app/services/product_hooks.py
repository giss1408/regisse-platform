"""Products that keep data keyed by phone number must move it before a number change takes effect (auth then follows).
Called synchronously from the confirm step, so a failure stops the change with nothing changed in auth. The URL and key come from
configuration only (AUTH_PRODUCT_HOOKS_JSON); no user input ever reaches a URL."""

import httpx

from app.config import get_settings


class HookFailed(Exception):
    def __init__(self, product: str):
        self.product = product


async def phone_changed(product: str, old_phone: str, new_phone: str) -> bool:
    """True when the product moved its data (or had nothing to move). False = no hook configured for this product."""
    hook = get_settings().product_hooks.get(product)
    if not hook:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(hook["url"], json={"old_phone": old_phone, "new_phone": new_phone}, headers={"X-Service-Key": hook["key"]})
        r.raise_for_status()
    except (httpx.HTTPError, KeyError):
        raise HookFailed(product) from None
    return True
