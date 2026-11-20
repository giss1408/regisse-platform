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


def phone_change_alert(product: str, display_name: str | None, new_masked: str) -> str:
    return (
        f"Bonjour {first_name(display_name)} 👋 Quelqu’un a demandé de changer le numéro de ton compte {label(product)} vers {new_masked}.\n\n"
        "Le changement n’est effectif qu’après un délai de sécurité. Si c’est toi, ne fais rien. Si ce n’est *pas toi*, réponds-nous ici tout de suite avec « PAS MOI » : nous bloquons la demande et ton compte."
    )


def phone_changed_notice(product: str, display_name: str | None, new_masked: str) -> str:
    return (
        f"Bonjour {first_name(display_name)} 👋 Ton compte {label(product)} est maintenant lié au numéro {new_masked}.\n\n"
        "Si ce n’est *pas toi*, réponds-nous ici tout de suite avec « PAS MOI » : nous remettons ton ancien numéro et bloquons le compte."
    )
