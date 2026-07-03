"""
Internationalisation — catalogue des langues et helpers SEO multilingue.

Réutilisé par la génération géoloc, le générateur WordPress et le crawler
pour produire du contenu dans la bonne langue et poser les bons signaux SEO
internationaux (hreflang, x-default, og:locale, attribut lang, JSON-LD).
"""
import re

# code : (nom français, endonyme, nom anglais pour prompts, hreflang, og:locale, drapeau)
LANGUAGES: dict[str, dict] = {
    "fr": {"name": "Français", "native": "Français", "en": "French", "hreflang": "fr", "locale": "fr_FR", "flag": "🇫🇷"},
    "en": {"name": "Anglais", "native": "English", "en": "English", "hreflang": "en", "locale": "en_US", "flag": "🇬🇧"},
    "es": {"name": "Espagnol", "native": "Español", "en": "Spanish", "hreflang": "es", "locale": "es_ES", "flag": "🇪🇸"},
    "de": {"name": "Allemand", "native": "Deutsch", "en": "German", "hreflang": "de", "locale": "de_DE", "flag": "🇩🇪"},
    "it": {"name": "Italien", "native": "Italiano", "en": "Italian", "hreflang": "it", "locale": "it_IT", "flag": "🇮🇹"},
    "pt": {"name": "Portugais", "native": "Português", "en": "Portuguese", "hreflang": "pt", "locale": "pt_PT", "flag": "🇵🇹"},
    "nl": {"name": "Néerlandais", "native": "Nederlands", "en": "Dutch", "hreflang": "nl", "locale": "nl_NL", "flag": "🇳🇱"},
    "pl": {"name": "Polonais", "native": "Polski", "en": "Polish", "hreflang": "pl", "locale": "pl_PL", "flag": "🇵🇱"},
    "ar": {"name": "Arabe", "native": "العربية", "en": "Arabic", "hreflang": "ar", "locale": "ar_AR", "flag": "🇸🇦"},
    "ru": {"name": "Russe", "native": "Русский", "en": "Russian", "hreflang": "ru", "locale": "ru_RU", "flag": "🇷🇺"},
    "ca": {"name": "Catalan", "native": "Català", "en": "Catalan", "hreflang": "ca", "locale": "ca_ES", "flag": "🇪🇸"},
    "tr": {"name": "Turc", "native": "Türkçe", "en": "Turkish", "hreflang": "tr", "locale": "tr_TR", "flag": "🇹🇷"},
}

DEFAULT_LANG = "fr"
RTL_LANGS = {"ar", "he", "fa", "ur"}


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def normalize(code: str) -> str:
    """Normalise un code langue (fr-FR -> fr) et retombe sur le défaut si inconnu."""
    if not code:
        return DEFAULT_LANG
    c = code.strip().lower().replace("_", "-").split("-")[0]
    return c if c in LANGUAGES else DEFAULT_LANG


def lang_name(code: str, kind: str = "en") -> str:
    """Nom d'une langue. kind: 'en' (pour prompts), 'name' (FR), 'native'."""
    info = LANGUAGES.get(normalize(code), LANGUAGES[DEFAULT_LANG])
    return info.get(kind, info["en"])


def locale(code: str) -> str:
    return LANGUAGES.get(normalize(code), LANGUAGES[DEFAULT_LANG])["locale"]


def hreflang(code: str) -> str:
    return LANGUAGES.get(normalize(code), LANGUAGES[DEFAULT_LANG])["hreflang"]


def is_rtl(code: str) -> bool:
    return normalize(code) in RTL_LANGS


def lang_prefix(code: str, main_lang: str) -> str:
    """Préfixe d'URL pour une langue (vide pour la langue principale).

    ex: main=fr, code=en -> '/en'  ; main=fr, code=fr -> ''.
    """
    code, main_lang = normalize(code), normalize(main_lang)
    return "" if code == main_lang else f"/{code}"


def build_hreflang_tags(urls_by_lang: dict[str, str], x_default: str = "") -> str:
    """Construit les balises <link rel="alternate" hreflang=...>.

    *urls_by_lang* : {code_langue: url_absolue}. *x_default* : URL par défaut
    (souvent la langue principale). Toutes les versions se référencent
    mutuellement (réciprocité exigée par Google).
    """
    if not urls_by_lang:
        return ""
    tags = []
    for code, url in urls_by_lang.items():
        tags.append(f'<link rel="alternate" hreflang="{hreflang(code)}" href="{url}" />')
    if x_default:
        tags.append(f'<link rel="alternate" hreflang="x-default" href="{x_default}" />')
    return "\n".join(tags)


def prompt_language_instruction(code: str) -> str:
    """Instruction à ajouter à un prompt Claude pour rédiger dans la langue cible."""
    code = normalize(code)
    if code == DEFAULT_LANG:
        return ""  # français : comportement par défaut des prompts existants
    name_en = lang_name(code, "en")
    native = lang_name(code, "native")
    return (
        f"\n\nLANGUE DE RÉDACTION : écris TOUT le contenu en {name_en} ({native}). "
        f"Titres, paragraphes, FAQ, méta, ancres : entièrement en {name_en}, "
        f"de façon naturelle et idiomatique (pas de traduction mot-à-mot). "
        f"Adapte les tournures au marché local de cette langue."
    )


def coerce_langs(main_language: str, languages: list[str] | None) -> tuple[str, list[str]]:
    """Nettoie et normalise (langue principale, liste complète des langues).

    Garantit : main valide, main incluse en tête, doublons retirés, langues
    inconnues ignorées.
    """
    main = normalize(main_language)
    langs = [normalize(l) for l in (languages or []) if is_supported(normalize(l))]
    ordered = [main] + [l for l in langs if l != main]
    seen, out = set(), []
    for l in ordered:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return main, out
