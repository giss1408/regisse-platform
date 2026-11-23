"""Password policy shared by every product."""


class WeakPassword(ValueError):
    pass


def check_password_strength(password: str) -> None:
    if len(password) < 8:
        raise WeakPassword("8 caractères minimum")
    if password.isdigit() and len(set(password)) <= 2:
        raise WeakPassword("Mot de passe trop simple")
