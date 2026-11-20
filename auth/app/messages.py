"""WhatsApp messages the admin sends from their own phone (wa.me). Written server-side so wording is consistent and
the apps never compose security messages. Users are addressed with "tu" (as in flariane)."""

PRODUCT_LABEL = {"flariane": "Flariane", "fidelia": "Fidelia"}


def label(product: str) -> str:
    return PRODUCT_LABEL.get(product, product.capitalize())


def first_name(display_name: str | None) -> str:
    return (display_name or "").strip().split(" ")[0] or "Bonjour"


def activation(product: str, display_name: str | None, code: str) -> str:
    return (
        f"Bonjour {first_name(display_name)} 👋 Bienvenue chez {label(product)} !\n\n"
        f"Ton code de vérification : *{code}*\n\n"
        f"Saisis-le dans l’app {label(product)} pour activer ton compte. Si tu n’as pas créé de compte, ignore ce message."
    )


def temp_password(product: str, display_name: str | None, temp: str) -> str:
    return (
        f"Bonjour {first_name(display_name)} 👋 Voici ton mot de passe temporaire {label(product)} : *{temp}*\n\n"
        "Connecte-toi dans les 24 h avec ce mot de passe : l’app te demandera d’en choisir un nouveau. "
        "Si tu n’as pas fait cette demande, réponds-nous ici."
    )


def phone_change(product: str, display_name: str | None, code: str) -> str:
    return (
        f"Bonjour {first_name(display_name)} 👋 Ton code pour changer de numéro sur {label(product)} : *{code}*\n\n"
        f"Saisis-le dans l’app {label(product)} pour confirmer ce nouveau numéro. "
        "Si tu n’as pas fait cette demande, ne le donne à personne et réponds-nous ici."
    )
