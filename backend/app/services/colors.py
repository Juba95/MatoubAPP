"""
Génération d'une palette cohérente à partir d'une seule couleur principale.

L'utilisateur donne UNE couleur (ex: un bleu) et on dérive automatiquement,
par théorie des couleurs (roue HSL), les couleurs secondaire, claire, neutre
et CTA, avec un contraste suffisant pour l'accessibilité.
"""
import colorsys
import re


def _clean_hex(c: str) -> str:
    c = (c or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c or ""):
        return "2ea3f2"  # bleu par défaut
    return c.lower()


def hex_to_hls(c: str) -> tuple[float, float, float]:
    c = _clean_hex(c)
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h, l, s


def hls_to_hex(h: float, l: float, s: float) -> str:
    h = h % 1.0
    l = max(0.0, min(1.0, l))
    s = max(0.0, min(1.0, s))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _luminance(c: str) -> float:
    c = _clean_hex(c)
    def _lin(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(c1: str, c2: str) -> float:
    l1, l2 = _luminance(c1), _luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def text_on(bg: str) -> str:
    """Retourne #ffffff ou #1a1a2e selon le meilleur contraste sur *bg*."""
    return "#ffffff" if contrast_ratio(bg, "#ffffff") >= contrast_ratio(bg, "#1a1a2e") else "#1a1a2e"


def build_palette(primary: str) -> dict:
    """Dérive une palette complète depuis la couleur principale.

    Retourne : primary, secondary, light, neutre, cta, text_on_primary,
    text_on_cta (+ variantes hover foncées).
    """
    h, l, s = hex_to_hls(primary)
    prim = hls_to_hex(h, l, s)

    # Secondaire : analogue (+30° sur la roue), légèrement plus clair
    secondary = hls_to_hex(h + 30 / 360, min(0.62, l + 0.08), max(0.35, s * 0.9))
    # Claire : même teinte, très désaturée et très claire (fonds de section)
    light = hls_to_hex(h, 0.96, min(0.30, s))
    # Neutre : teinte proche, très sombre et désaturée (textes/footer)
    neutre = hls_to_hex(h, 0.13, min(0.25, s * 0.5))
    # CTA : complémentaire (+180°) vive pour trancher — ou triadique si peu saturé
    cta_h = (h + 0.5) if s > 0.25 else (h + 1 / 3)
    cta = hls_to_hex(cta_h, 0.52, max(0.6, s))

    # Variantes hover (plus foncées de ~12% de luminosité)
    def darken(c, amount=0.12):
        hh, ll, ss = hex_to_hls(c)
        return hls_to_hex(hh, max(0.0, ll - amount), ss)

    return {
        "primary": prim,
        "secondary": secondary,
        "light": light,
        "neutre": neutre,
        "cta": cta,
        "primary_hover": darken(prim),
        "cta_hover": darken(cta),
        "text_on_primary": text_on(prim),
        "text_on_cta": text_on(cta),
    }
