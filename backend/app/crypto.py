"""
Chiffrement Fernet des credentials sensibles stockés en BDD.
Utilisé comme TypeDecorator SQLAlchemy : transparent sur les modèles.
"""
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text


def _get_fernet() -> Fernet | None:
    from app.config import get_settings
    key = get_settings().encryption_key
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    """Chiffre une valeur. Retourne en clair si ENCRYPTION_KEY non configurée."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    return f.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Déchiffre une valeur. Gère les données en clair (migration)."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except InvalidToken:
        # Donnée non chiffrée (legacy) — on la retourne telle quelle.
        # ATTENTION : si ENCRYPTION_KEY a changé, on retombe aussi ici et le
        # ciphertext est renvoyé tel quel → connexions WP/GSC incompréhensiblement
        # cassées. Le préfixe Fernet permet de distinguer les deux cas.
        if value.startswith("gAAAA"):
            import logging
            logging.getLogger(__name__).warning(
                "Déchiffrement impossible d'une valeur chiffrée — "
                "ENCRYPTION_KEY a probablement changé"
            )
        return value
    except Exception:
        return value


class EncryptedString(TypeDecorator):
    """Type SQLAlchemy qui chiffre/déchiffre automatiquement."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Appelé à l'écriture (INSERT/UPDATE)."""
        if value is not None:
            return encrypt(value)
        return value

    def process_result_value(self, value, dialect):
        """Appelé à la lecture (SELECT)."""
        if value is not None:
            return decrypt(value)
        return value
