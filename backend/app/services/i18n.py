"""
Internationalisation — catalogue des langues et helpers SEO multilingue.

Réutilisé par la génération géoloc, le générateur WordPress et le crawler
pour produire du contenu dans la bonne langue et poser les bons signaux SEO
internationaux (hreflang, x-default, og:locale, attribut lang, JSON-LD).
"""
import re

# code : (nom français, endonyme, nom anglais pour prompts, hreflang, og:locale, drapeau)
# Les variantes régionales (zh-tw, pt-br, en-gb…) sont des entrées à part
# entière : hreflang précis, locale WordPress dédiée et consigne de rédaction
# adaptée au marché local.
LANGUAGES: dict[str, dict] = {
    "fr": {"name": "Français", "native": "Français", "en": "French", "hreflang": "fr", "locale": "fr_FR", "flag": "🇫🇷"},
    "fr-ca": {"name": "Français (Québec)", "native": "Français canadien", "en": "Canadian French (Québec)", "hreflang": "fr-CA", "locale": "fr_CA", "flag": "🇨🇦", "base": "fr"},
    "fr-be": {"name": "Français (Belgique)", "native": "Français de Belgique", "en": "Belgian French", "hreflang": "fr-BE", "locale": "fr_BE", "flag": "🇧🇪", "base": "fr"},
    "en": {"name": "Anglais", "native": "English", "en": "English", "hreflang": "en", "locale": "en_US", "flag": "🇬🇧"},
    "en-gb": {"name": "Anglais (UK)", "native": "British English", "en": "British English", "hreflang": "en-GB", "locale": "en_GB", "flag": "🇬🇧", "base": "en"},
    "en-us": {"name": "Anglais (US)", "native": "American English", "en": "American English", "hreflang": "en-US", "locale": "en_US", "flag": "🇺🇸", "base": "en"},
    "en-au": {"name": "Anglais (Australie)", "native": "Australian English", "en": "Australian English", "hreflang": "en-AU", "locale": "en_AU", "flag": "🇦🇺", "base": "en"},
    "es": {"name": "Espagnol", "native": "Español", "en": "Spanish", "hreflang": "es", "locale": "es_ES", "flag": "🇪🇸"},
    "es-mx": {"name": "Espagnol (Mexique)", "native": "Español de México", "en": "Mexican Spanish", "hreflang": "es-MX", "locale": "es_MX", "flag": "🇲🇽", "base": "es"},
    "es-ar": {"name": "Espagnol (Argentine)", "native": "Español rioplatense", "en": "Argentinian Spanish", "hreflang": "es-AR", "locale": "es_AR", "flag": "🇦🇷", "base": "es"},
    "de": {"name": "Allemand", "native": "Deutsch", "en": "German", "hreflang": "de", "locale": "de_DE", "flag": "🇩🇪"},
    "de-at": {"name": "Allemand (Autriche)", "native": "Österreichisches Deutsch", "en": "Austrian German", "hreflang": "de-AT", "locale": "de_AT", "flag": "🇦🇹", "base": "de"},
    "de-ch": {"name": "Allemand (Suisse)", "native": "Schweizerdeutsch", "en": "Swiss German", "hreflang": "de-CH", "locale": "de_CH", "flag": "🇨🇭", "base": "de"},
    "it": {"name": "Italien", "native": "Italiano", "en": "Italian", "hreflang": "it", "locale": "it_IT", "flag": "🇮🇹"},
    "pt": {"name": "Portugais", "native": "Português", "en": "European Portuguese", "hreflang": "pt-PT", "locale": "pt_PT", "flag": "🇵🇹"},
    "pt-br": {"name": "Portugais (Brésil)", "native": "Português do Brasil", "en": "Brazilian Portuguese", "hreflang": "pt-BR", "locale": "pt_BR", "flag": "🇧🇷", "base": "pt"},
    "nl": {"name": "Néerlandais", "native": "Nederlands", "en": "Dutch", "hreflang": "nl", "locale": "nl_NL", "flag": "🇳🇱"},
    "pl": {"name": "Polonais", "native": "Polski", "en": "Polish", "hreflang": "pl", "locale": "pl_PL", "flag": "🇵🇱"},
    "ar": {"name": "Arabe", "native": "العربية", "en": "Arabic (Modern Standard)", "hreflang": "ar", "locale": "ar", "flag": "🇸🇦"},
    "ar-ma": {"name": "Arabe (Maroc)", "native": "الدارجة المغربية", "en": "Moroccan Arabic (Darija)", "hreflang": "ar-MA", "locale": "ar_MA", "flag": "🇲🇦", "base": "ar"},
    "ru": {"name": "Russe", "native": "Русский", "en": "Russian", "hreflang": "ru", "locale": "ru_RU", "flag": "🇷🇺"},
    "uk": {"name": "Ukrainien", "native": "Українська", "en": "Ukrainian", "hreflang": "uk", "locale": "uk", "flag": "🇺🇦"},
    "zh": {"name": "Chinois (mandarin simplifié)", "native": "简体中文", "en": "Simplified Chinese (Mandarin)", "hreflang": "zh-Hans", "locale": "zh_CN", "flag": "🇨🇳"},
    "zh-tw": {"name": "Chinois traditionnel (Taïwan)", "native": "繁體中文（台灣）", "en": "Traditional Chinese (Taiwan, Mandarin)", "hreflang": "zh-Hant", "locale": "zh_TW", "flag": "🇹🇼", "base": "zh"},
    "zh-hk": {"name": "Chinois (Hong Kong, cantonais)", "native": "繁體中文（香港）", "en": "Traditional Chinese (Hong Kong, Cantonese usage)", "hreflang": "zh-HK", "locale": "zh_HK", "flag": "🇭🇰", "base": "zh"},
    "ja": {"name": "Japonais", "native": "日本語", "en": "Japanese", "hreflang": "ja", "locale": "ja", "flag": "🇯🇵"},
    "ko": {"name": "Coréen", "native": "한국어", "en": "Korean", "hreflang": "ko", "locale": "ko_KR", "flag": "🇰🇷"},
    "ca": {"name": "Catalan", "native": "Català", "en": "Catalan", "hreflang": "ca", "locale": "ca", "flag": "🇪🇸"},
    "tr": {"name": "Turc", "native": "Türkçe", "en": "Turkish", "hreflang": "tr", "locale": "tr_TR", "flag": "🇹🇷"},
    "kab": {"name": "Kabyle", "native": "Taqbaylit ⵣ", "en": "Kabyle (Berber, Latin script)", "hreflang": "kab", "locale": "kab", "flag": "ⵣ"},
}

DEFAULT_LANG = "fr"
RTL_LANGS = {"ar", "ar-ma", "he", "fa", "ur"}


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def normalize(code: str) -> str:
    """Normalise un code langue. Les variantes régionales connues sont
    conservées telles quelles (pt-BR -> pt-br) ; sinon on retombe sur la
    langue de base (fr-FR -> fr), puis sur le défaut si inconnu."""
    if not code:
        return DEFAULT_LANG
    c = code.strip().lower().replace("_", "-")
    if c in LANGUAGES:
        return c
    base = c.split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANG


def base_lang(code: str) -> str:
    """Langue de base d'une variante (pt-br -> pt ; fr -> fr)."""
    info = LANGUAGES.get(normalize(code), {})
    return info.get("base", normalize(code))


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
    variant_note = ""
    if LANGUAGES.get(code, {}).get("base"):
        variant_note = (
            f" Respecte STRICTEMENT les conventions de cette variante régionale "
            f"({name_en}) : vocabulaire, orthographe, expressions et références "
            f"locales propres à ce marché — pas la variante générique."
        )
    return (
        f"\n\nLANGUE DE RÉDACTION : écris TOUT le contenu en {name_en} ({native}). "
        f"Titres, paragraphes, FAQ, méta, ancres : entièrement en {name_en}, "
        f"de façon naturelle et idiomatique (pas de traduction mot-à-mot). "
        f"Adapte les tournures au marché local de cette langue." + variant_note
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
