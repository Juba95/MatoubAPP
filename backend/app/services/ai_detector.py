"""
Détecteur de contenu IA — moteur stylométrique multi-signaux (100 % gratuit).

Trois modes :
- analyze_text(text)  : un contenu collé → score IA + risque Google
- analyze_page(url)   : une URL → extraction du contenu principal + signaux page
- analyze_site(url)   : échantillon de pages via sitemap → signaux « site IA »
                        (génération de masse, gabarits, publication en rafale)

Le score n'est PAS un simple compteur de mots-clés : ~45 signaux répartis en
6 familles pondérées, chacun calibré sur les différences mesurables entre
texte humain et texte LLM :

  A. Rythme & variance (burstiness)   — l'IA écrit des phrases trop régulières
  B. Lexique IA                       — clichés, connecteurs, règle de trois
  C. Structure                        — listes à puces gras, titres gabarits,
                                        sections Conclusion/FAQ mécaniques
  D. Spécificité & incarnation        — l'IA est vague : peu de chiffres, de
                                        noms propres, de vécu, de dates
  E. Diversité & répétition           — n-grams répétés, débuts de phrases
  F. Empreinte machine                — ponctuation aseptisée, zéro oralité

Le « risque Google » est distinct du score IA : Google ne pénalise pas l'IA
en soi mais le contenu de masse sans valeur (Helpful Content / Scaled Content
Abuse). Il combine score IA + déficit de spécificité + signaux d'échelle.
"""
import math
import re
import statistics
import unicodedata
from collections import Counter

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Lexiques (FR + EN)
# ---------------------------------------------------------------------------

AI_PHRASES_FR = [
    "dans un monde en constante évolution", "à l'ère du numérique", "de nos jours",
    "aujourd'hui plus que jamais", "il est important de noter", "il est essentiel de",
    "il convient de noter", "il convient de", "il est crucial de", "il est primordial de",
    "il est recommandé de", "force est de constater", "il va sans dire",
    "n'hésitez pas à", "que vous soyez", "en somme", "en définitive", "en conclusion",
    "pour conclure", "joue un rôle crucial", "joue un rôle essentiel",
    "joue un rôle clé", "faire appel à un professionnel", "grâce à son expertise",
    "en un rien de temps", "sans plus attendre", "plongeons", "explorons ensemble",
    "dans cet article", "cet article vous", "nous allons voir", "voyons maintenant",
    "passons maintenant", "penchons-nous", "guide complet", "tout ce qu'il faut savoir",
    "un large éventail", "une vaste gamme", "une large gamme", "un gage de",
    "la pierre angulaire", "un atout majeur", "une solution sur mesure",
    "répondre à vos besoins", "adapté à vos besoins", "adaptée à vos besoins",
    "selon vos besoins", "un excellent rapport qualité-prix",
    "prendre une décision éclairée", "des décisions éclairées", "en toute sérénité",
    "en toute tranquillité", "l'esprit tranquille", "un investissement judicieux",
    "ne cesse de croître", "en plein essor", "au cœur de", "à ne pas négliger",
    "à prendre en compte", "il ne faut pas oublier", "comme mentionné précédemment",
    "comme évoqué précédemment", "au fil des ans", "au fil du temps",
    "le monde du", "l'univers du", "l'univers de", "un véritable havre",
    "incontournable", "révolutionner", "optimiser votre", "maximiser votre",
    "sublimer", "un must", "une expérience unique", "une expérience optimale",
    "un choix judicieux", "la solution idéale", "le choix idéal",
    "quels que soient vos besoins", "dans les moindres détails",
    "vous garantit une", "des prestations de qualité", "un service de qualité",
    "un travail soigné", "un savoir-faire",
]

AI_PHRASES_EN = [
    "in today's fast-paced world", "in today's digital age", "delve into",
    "it's important to note", "it is worth noting", "in conclusion", "to sum up",
    "whether you're", "look no further", "a wide range of", "plays a crucial role",
    "seamlessly", "comprehensive guide", "unlock the", "elevate your", "embark on",
    "in the realm of", "navigating the", "ever-evolving", "game-changer",
    "harness the power", "dive into", "let's explore", "when it comes to",
    "at the end of the day", "needless to say", "stands out", "top-notch",
    "hassle-free", "peace of mind", "state-of-the-art", "cutting-edge",
    "tailored to your needs", "a testament to", "the landscape of", "tapestry",
    "treasure trove", "look no further than", "boasts", "meticulously",
]

# Mots « IA » isolés (densité pour 1000 mots)
AI_WORDS_FR = {
    "crucial", "cruciale", "cruciaux", "essentiel", "essentielle", "essentiels",
    "primordial", "primordiale", "incontournable", "incontournables", "optimal",
    "optimale", "optimaux", "pléthore", "myriade", "innovant", "innovante",
    "novateur", "holistique", "synergie", "robuste", "exhaustif", "exhaustive",
    "fluide", "harmonieusement", "judicieux", "judicieuse", "idéal", "idéale",
    "exceptionnel", "exceptionnelle", "remarquable", "inégalé", "inégalée",
    "impeccable", "irréprochable", "notoire", "considérable", "significatif",
    "significative", "polyvalent", "polyvalente", "efficacement", "aisément",
}
AI_WORDS_EN = {
    "crucial", "essential", "pivotal", "paramount", "plethora", "myriad",
    "innovative", "holistic", "synergy", "robust", "comprehensive", "seamless",
    "effortlessly", "meticulous", "impeccable", "remarkable", "unparalleled",
    "versatile", "invaluable", "leverage", "streamline", "elevate", "unlock",
    "foster", "empower", "transformative", "dynamic", "vibrant", "bespoke",
}

# Connecteurs en début de phrase (l'IA en abuse)
CONNECTORS_FR = [
    "de plus", "en outre", "par ailleurs", "en effet", "ainsi", "cependant",
    "néanmoins", "toutefois", "enfin", "ensuite", "d'une part", "d'autre part",
    "en résumé", "en conclusion", "pour finir", "premièrement", "deuxièmement",
    "troisièmement", "notamment", "également", "en somme", "en définitive",
    "par conséquent", "de ce fait", "dès lors",
]
CONNECTORS_EN = [
    "additionally", "furthermore", "moreover", "however", "in addition",
    "finally", "firstly", "secondly", "overall", "ultimately", "consequently",
    "therefore", "nevertheless", "in conclusion", "in summary",
]

# Marqueurs de flou (contenu vague, sans donnée concrète)
VAGUE_FR = [
    "de nombreux", "de nombreuses", "plusieurs", "divers", "diverses",
    "différents", "différentes", "un certain nombre", "la plupart",
    "généralement", "souvent", "parfois", "certains", "certaines", "il existe",
    "quelques", "beaucoup de", "toutes sortes",
]
VAGUE_EN = [
    "many", "numerous", "various", "several", "a number of", "most",
    "generally", "often", "sometimes", "some", "there are", "a lot of",
    "all kinds of", "countless",
]

# Marqueurs d'oralité / d'humain (leur ABSENCE est un signal IA)
HUMAN_MARKERS_FR = [
    "bref", "franchement", "honnêtement", "du coup", "en vrai", "carrément",
    "vachement", "pas mal", "bah", "hein", "voilà", "j'avoue", "perso",
    "d'ailleurs", "au passage", "entre nous", "attention", "petit bémol",
    "bémol", "à mon avis", "je pense", "je trouve", "on va pas se mentir",
]
HUMAN_MARKERS_EN = [
    "honestly", "frankly", "to be fair", "in my opinion", "i think", "i found",
    "personally", "by the way", "heads up", "full disclosure", "real talk",
]

FIRST_PERSON_FR = ["je ", "j'ai ", "j'avais", "mon expérience", "notre expérience",
                   "nous avons testé", "j'ai testé", "on a testé", "chez nous"]
FIRST_PERSON_EN = ["i ", "i've ", "my experience", "we tested", "i tested", "in my"]

_MONTHS = ("janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|"
           "novembre|décembre|january|february|march|april|may|june|july|august|"
           "september|october|november|december")

_WORD_RE = re.compile(r"[a-zà-öø-ÿœæ0-9'’-]+", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")

# Stopwords pour l'analyse de répétition (on ne signale que les mots pleins)
_STOP_FR = set("""le la les de des du un une et en pour dans sur avec par au aux
ce cette ces cet qui que quoi dont où est sont être avoir été plus très tout
tous toute toutes leur leurs notre nos votre vos son sa ses mais ou donc or ni
car ne pas nous vous ils elles je tu il elle on se comme si aussi fait faire
deux trois entre chez vers après avant depuis pendant contre sans sous ainsi
alors même peut peuvent dont autres autre chaque encore bien afin lors selon
votre notre cela ceci celui celle ceux celles était vont va être aura
sera fois lorsque quand toutes""".split())
_STOP_EN = set("""the a an and or but of to in on for with by at from is are
was were be been being have has had this that these those it its as not no we
you they i he she will would can could should may might do does did more most
very all each other any some such than then there here when where which who
whom while about into over under out up down off just also only own same so
too s t don now your our their his her my me us them what""".split())


def _norm(t: str) -> str:
    return unicodedata.normalize("NFC", (t or "")).lower()


def _ramp(value: float, low: float, high: float, invert: bool = False) -> float:
    """Normalise une valeur brute en score 0-100 entre deux bornes calibrées.
    invert=True : une valeur BASSE = très IA (ex : diversité des débuts de phrase)."""
    if high == low:
        return 0.0
    x = (value - low) / (high - low)
    x = max(0.0, min(1.0, x))
    return round((1 - x if invert else x) * 100, 1)


# ---------------------------------------------------------------------------
# Moteur texte
# ---------------------------------------------------------------------------


def analyze_text(text: str, language: str = "") -> dict:
    """Analyse stylométrique complète d'un texte. Retourne score global,
    familles, ~45 signaux détaillés, preuves et recommandations."""
    text = (text or "").strip()
    words = _WORD_RE.findall(_norm(text))
    n_words = len(words)
    if n_words < 80:
        return {"error": "Texte trop court pour une analyse fiable (minimum ~80 mots)",
                "word_count": n_words}

    # Langue : fr par défaut, en si détecté
    lang = (language or "").lower()[:2]
    if not lang:
        fr_hits = sum(1 for w in words if w in ("le", "la", "les", "des", "une", "est", "pour", "dans"))
        en_hits = sum(1 for w in words if w in ("the", "and", "for", "with", "this", "that", "are", "you"))
        lang = "en" if en_hits > fr_hits else "fr"
    fr = lang != "en"

    phrases = AI_PHRASES_FR + AI_PHRASES_EN if fr else AI_PHRASES_EN
    ai_words = AI_WORDS_FR if fr else AI_WORDS_EN
    connectors = CONNECTORS_FR if fr else CONNECTORS_EN
    vague = VAGUE_FR if fr else VAGUE_EN
    human_markers = HUMAN_MARKERS_FR + HUMAN_MARKERS_EN if fr else HUMAN_MARKERS_EN
    first_person = FIRST_PERSON_FR if fr else FIRST_PERSON_EN

    low = _norm(text)
    per_k = 1000.0 / n_words          # facteur « pour 1000 mots »

    # Découpage phrases / paragraphes
    raw_sents = [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) > 2]
    sent_lens = [len(_WORD_RE.findall(s)) for s in raw_sents] or [n_words]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if len(p.strip()) > 40]
    para_lens = [len(_WORD_RE.findall(p)) for p in paragraphs]

    signals: list[dict] = []

    def sig(family, name, raw, score, why):
        signals.append({"family": family, "name": name, "raw": raw,
                        "score": score, "why": why})

    # ───────────────────────── A. Rythme & variance ─────────────────────────
    mean_len = statistics.mean(sent_lens)
    cv = (statistics.pstdev(sent_lens) / mean_len) if mean_len else 0
    # Humain : cv ~0.55-0.9 ; IA : ~0.25-0.45
    sig("A", "Burstiness (variation des longueurs de phrases)", round(cv, 2),
        _ramp(cv, 0.25, 0.75, invert=True),
        "L'humain alterne phrases courtes et longues ; l'IA lisse tout.")
    within = sum(1 for l in sent_lens if abs(l - mean_len) <= 0.25 * mean_len)
    unif = within / len(sent_lens)
    sig("A", "Phrases dans la moyenne ±25%", f"{round(unif*100)}%",
        _ramp(unif, 0.30, 0.70),
        "Trop de phrases de longueur quasi identique = métronome IA.")
    if len(para_lens) >= 3:
        pcv = statistics.pstdev(para_lens) / statistics.mean(para_lens)
        sig("A", "Variation des longueurs de paragraphes", round(pcv, 2),
            _ramp(pcv, 0.15, 0.65, invert=True),
            "Paragraphes calibrés au mot près = gabarit de génération.")
    very_short = sum(1 for l in sent_lens if l <= 4)
    sig("A", "Phrases très courtes (≤4 mots)", very_short,
        _ramp(very_short / max(1, len(sent_lens)), 0.0, 0.12, invert=True),
        "Les « Oui. », « Un conseil. » sont humains ; l'IA n'en écrit presque jamais.")

    # ───────────────────────── B. Lexique IA ────────────────────────────────
    phrase_hits = [p for p in phrases if p in low]
    phrase_count = sum(low.count(p) for p in phrase_hits)
    sig("B", "Expressions cliché IA", f"{phrase_count} ({round(phrase_count*per_k,1)}/1000 mots)",
        _ramp(phrase_count * per_k, 1.0, 9.0),
        "« dans un monde en constante évolution », « n'hésitez pas à »…")
    aw = sum(1 for w in words if w in ai_words)
    sig("B", "Vocabulaire IA (crucial, optimal, pléthore…)", f"{aw} ({round(aw*per_k,1)}/1000)",
        _ramp(aw * per_k, 3.0, 22.0),
        "Adjectifs emphatiques génériques sur-représentés chez les LLM.")
    conn_starts = sum(1 for s in raw_sents if any(_norm(s).startswith(c) for c in connectors))
    conn_ratio = conn_starts / max(1, len(raw_sents))
    sig("B", "Phrases ouvertes par un connecteur", f"{round(conn_ratio*100)}%",
        _ramp(conn_ratio, 0.06, 0.30),
        "« De plus… En outre… Par ailleurs… » : le tic n°1 des LLM.")
    triples = len(re.findall(r"\b[\wà-ÿ'’-]+,\s+[\wà-ÿ'’-]+\s+(?:et|and|ou|or)\s+[\wà-ÿ'’-]+", text, re.I))
    sig("B", "Règle de trois (X, Y et Z)", f"{triples} ({round(triples*per_k,1)}/1000)",
        _ramp(triples * per_k, 2.0, 12.0),
        "Les énumérations ternaires systématiques sont une signature LLM.")
    neg_par = len(re.findall(
        r"(?:ce n'est pas (?:seulement|juste|simplement)|not (?:just|only|merely))", low))
    sig("B", "Parallélisme négatif (« pas seulement X, mais Y »)", neg_par,
        _ramp(neg_par * per_k, 0.4, 3.0),
        "Tournure rhétorique fétiche des modèles récents.")
    superl = len(re.findall(
        r"\b(?:meilleur|meilleure|parfait|parfaite|idéal|idéale|exceptionnel|"
        r"incroyable|inégalé|best|perfect|amazing|incredible|ultimate)\b", low))
    sig("B", "Densité promotionnelle (superlatifs)", f"{superl} ({round(superl*per_k,1)}/1000)",
        _ramp(superl * per_k, 2.0, 14.0),
        "Le ton publicitaire uniforme est typique du contenu généré en masse.")

    # ───────────────────────── C. Structure ─────────────────────────────────
    bold_bullets = len(re.findall(r"(?:^|\n)\s*(?:[-*•]|\d+\.)\s*(?:\*\*|<b>|<strong>)?[A-ZÀ-Ý][^:\n]{2,40}\s*:", text))
    sig("C", "Puces « Terme : explication »", bold_bullets,
        _ramp(bold_bullets * per_k, 1.0, 10.0),
        "Listes à puces avec libellé en tête : mise en forme ChatGPT par excellence.")
    concl = 1 if re.search(r"(?:^|\n)\s*(?:#{1,4}\s*)?(?:en )?(?:conclusion|pour conclure|en résumé|en somme|le mot de la fin|final thoughts|wrapping up)\b", low) else 0
    sig("C", "Section « Conclusion » explicite", "oui" if concl else "non",
        70.0 if concl else 0.0,
        "Le plan intro-développement-conclusion scolaire est un réflexe LLM.")
    headings = re.findall(r"(?:^|\n)\s*(?:#{1,4}|<h[23][^>]*>)\s*([^\n<]{4,90})", text)
    if len(headings) >= 3:
        q_head = sum(1 for h in headings if h.strip().endswith("?"))
        colon_head = sum(1 for h in headings if ":" in h)
        pat = max(q_head, colon_head) / len(headings)
        sig("C", "Titres au gabarit uniforme", f"{round(pat*100)}%",
            _ramp(pat, 0.4, 0.95),
            "Tous les titres en question ou en « X : Y » = plan généré.")
    em_dash = text.count("—") + text.count(" - ")
    sig("C", "Tirets cadratins / incises", f"{em_dash} ({round(em_dash*per_k,1)}/1000)",
        _ramp(em_dash * per_k, 2.5, 14.0),
        "L'abus d'incises en tiret est un marqueur documenté des LLM.")
    faq = 1 if re.search(r"(?:^|\n)\s*(?:#{1,4}\s*)?(?:faq|questions fréquentes|foire aux questions|frequently asked)", low) else 0
    sig("C", "Bloc FAQ standardisé", "oui" if faq else "non", 45.0 if faq else 0.0,
        "Une FAQ n'est pas un problème en soi, mais s'ajoute aux autres gabarits.")

    # ───────────────── D. Spécificité & incarnation (inversés) ──────────────
    digits = len(re.findall(r"\d", text))
    numbers = len(re.findall(r"\b\d[\d\s,.]*(?:€|\$|%|km|kg|m²|h\b)?", text))
    sig("D", "Densité de chiffres concrets", f"{numbers} ({round(numbers*per_k,1)}/1000)",
        _ramp(numbers * per_k, 2.0, 18.0, invert=True),
        "Prix, dates, quantités : l'humain sait, l'IA reste dans le flou.")
    mid_caps = len(re.findall(r"(?<![.!?…]\s)(?<!^)\b[A-ZÀ-Ý][a-zà-ÿ]{2,}", text))
    sig("D", "Noms propres hors début de phrase", f"{mid_caps} ({round(mid_caps*per_k,1)}/1000)",
        _ramp(mid_caps * per_k, 4.0, 30.0, invert=True),
        "Marques, lieux, personnes précises = ancrage réel.")
    fp = sum(low.count(m) for m in first_person)
    sig("D", "Marqueurs de première personne / vécu", fp,
        _ramp(fp * per_k, 0.0, 6.0, invert=True),
        "« J'ai testé », « notre expérience » : le vécu ne s'invente pas facilement.")
    dates = len(re.findall(rf"\b(?:{_MONTHS})\b|\b(?:19|20)\d{{2}}\b", low))
    sig("D", "Ancres temporelles (dates, années)", dates,
        _ramp(dates * per_k, 0.5, 6.0, invert=True),
        "Un contenu incarné cite des moments précis.")
    vg = sum(low.count(v) for v in vague)
    sig("D", "Marqueurs de flou (« de nombreux », « divers »…)", f"{vg} ({round(vg*per_k,1)}/1000)",
        _ramp(vg * per_k, 5.0, 28.0),
        "Le flou quantitatif masque l'absence de données réelles.")
    quotes = len(re.findall(r"[«\"“][^»\"”]{15,240}[»\"”]", text))
    sig("D", "Citations / discours rapporté", quotes,
        _ramp(quotes * per_k, 0.0, 3.0, invert=True),
        "Les vraies citations sont rares dans le contenu généré.")

    # ───────────────────── E. Diversité & répétition ────────────────────────
    uniq_ratio = len(set(words)) / n_words
    win = 200
    ttrs = [len(set(words[i:i+win])) / min(win, n_words - i)
            for i in range(0, max(1, n_words - 50), win)]
    wttr = statistics.mean(ttrs) if ttrs else uniq_ratio
    sig("E", "Diversité lexicale (fenêtres de 200 mots)", round(wttr, 2),
        _ramp(wttr, 0.42, 0.62, invert=True) * 0.6 + _ramp(wttr, 0.72, 0.85) * 0.4,
        "Trop basse = répétitif ; anormalement « parfaite » = lissage IA.")
    four_grams = [" ".join(words[i:i+4]) for i in range(n_words - 3)]
    fg_counts = Counter(four_grams)
    repeated_fg = sum(c for c in fg_counts.values() if c >= 2) / max(1, len(four_grams))
    sig("E", "4-grams répétés", f"{round(repeated_fg*100,1)}%",
        _ramp(repeated_fg, 0.02, 0.12),
        "Blocs de 4 mots récurrents = phrasé formulaïque.")
    starts = [_norm(s).split()[0] for s in raw_sents if s.split()]
    start_div = len(set(starts)) / max(1, len(starts))
    sig("E", "Diversité des débuts de phrases", f"{round(start_div*100)}%",
        _ramp(start_div, 0.35, 0.75, invert=True),
        "L'IA recycle les mêmes ouvertures (« Le », « Il est », « Les »).")
    content_words = [w for w in words if len(w) >= 5]
    if content_words:
        top_word, top_n = Counter(content_words).most_common(1)[0]
        kw_share = top_n / len(content_words)
        sig("E", f"Sur-optimisation du mot « {top_word} »", f"{round(kw_share*100,1)}%",
            _ramp(kw_share, 0.02, 0.07),
            "Keyword stuffing : densité anormale du mot-clé principal.")

    # ───────────────────── F. Empreinte machine ─────────────────────────────
    punct = Counter(c for c in text if c in ".,;:!?…()—«»\"'")
    total_p = sum(punct.values()) or 1
    p_entropy = -sum((c / total_p) * math.log2(c / total_p) for c in punct.values())
    sig("F", "Entropie de ponctuation", round(p_entropy, 2),
        _ramp(p_entropy, 1.2, 2.6, invert=True),
        "L'humain utilise ?, !, parenthèses, guillemets ; l'IA surtout . et ,")
    excl_q = text.count("!") + text.count("?")
    sig("F", "Exclamations / interrogations", f"{excl_q} ({round(excl_q*per_k,1)}/1000)",
        _ramp(excl_q * per_k, 0.5, 8.0, invert=True),
        "Le ton neutre permanent est une empreinte machine.")
    hm = sum(low.count(m) for m in human_markers)
    sig("F", "Marqueurs d'oralité (« bref », « franchement »…)", hm,
        _ramp(hm * per_k, 0.0, 4.0, invert=True),
        "Les respirations de langage parlé manquent aux textes générés.")
    parens = text.count("(")
    sig("F", "Apartés entre parenthèses", parens,
        _ramp(parens * per_k, 0.3, 5.0, invert=True),
        "Les digressions entre parenthèses sont un réflexe d'auteur humain.")
    avg_wlen = statistics.mean(len(w) for w in words)
    sig("F", "Longueur moyenne des mots", round(avg_wlen, 2),
        _ramp(abs(avg_wlen - 5.4), 1.2, 0.2, invert=False) if fr else
        _ramp(abs(avg_wlen - 4.9), 1.2, 0.2),
        "Les LLM convergent vers une longueur de mot très stable.")

    # ─────────────────────── Agrégation pondérée ────────────────────────────
    weights = {"A": 20, "B": 20, "C": 13, "D": 20, "E": 15, "F": 12}
    # Les signaux de répétition (E) n'ont de sens que sur un texte assez long :
    # sur un texte court, un faible taux de répétition n'innocente rien.
    if n_words < 300:
        weights["E"] = 4
    elif n_words < 600:
        weights["E"] = 9
    fam_names = {"A": "Rythme & variance", "B": "Lexique IA", "C": "Structure",
                 "D": "Spécificité & incarnation", "E": "Diversité & répétition",
                 "F": "Empreinte machine"}
    families = {}
    total, wsum = 0.0, 0
    for f_key, w in weights.items():
        fs = [s["score"] for s in signals if s["family"] == f_key]
        if not fs:
            continue
        fam_score = round(statistics.mean(fs), 1)
        families[f_key] = {"label": fam_names[f_key], "score": fam_score, "weight": w}
        total += fam_score * w
        wsum += w
    global_score = round(total / wsum) if wsum else 0

    verdict = ("Quasi certainement généré par IA" if global_score >= 72 else
               "Probablement généré par IA" if global_score >= 55 else
               "Mixte — IA retravaillée ou humain très lisse" if global_score >= 38 else
               "Probablement humain")

    # Risque Google : IA-ness + déficit de valeur (famille D) + promo (B partiel)
    d_score = families.get("D", {}).get("score", 50)
    b_score = families.get("B", {}).get("score", 50)
    google_risk = round(min(100, global_score * 0.55 + d_score * 0.30 + b_score * 0.15))
    google_verdict = ("Risque élevé (profil « scaled content abuse »)" if google_risk >= 65 else
                      "Risque modéré — à enrichir avant publication" if google_risk >= 45 else
                      "Risque faible")

    # Preuves : phrases les plus « IA »
    evid = []
    for s in raw_sents:
        sl = _norm(s)
        score = sum(3 for p in phrases if p in sl)
        score += 2 if any(sl.startswith(c) for c in connectors) else 0
        score += sum(1 for w in _WORD_RE.findall(sl) if w in ai_words)
        if score >= 3:
            evid.append({"sentence": s[:220], "score": score})
    evid.sort(key=lambda x: -x["score"])

    # ───────────── Analyse phrase par phrase (façon GPTZero) ────────────────
    # Chaque phrase reçoit un score + la liste NOMINATIVE de ses problèmes,
    # pour un surlignage coloré et des corrections ciblées.
    sentence_details = []
    for s in raw_sents[:300]:
        sl = _norm(s)
        reasons = []
        s_score = 0
        found_ph = [p for p in phrases if p in sl]
        if found_ph:
            reasons.append("Cliché IA : " + ", ".join(f"« {p} »" for p in found_ph[:3]))
            s_score += 28 * len(found_ph)
        conn = next((c for c in connectors if sl.startswith(c)), None)
        if conn:
            reasons.append(f"Ouvre par le connecteur « {conn} »")
            s_score += 22
        s_words = _WORD_RE.findall(sl)
        aiw = [w for w in s_words if w in ai_words]
        if aiw:
            reasons.append("Vocabulaire IA : " + ", ".join(sorted(set(aiw))[:4]))
            s_score += 10 * len(set(aiw))
        vg_found = [v for v in vague if v in sl]
        if len(vg_found) >= 2:
            reasons.append("Flou : " + ", ".join(f"« {v} »" for v in vg_found[:3]))
            s_score += 8 * len(vg_found)
        if re.search(r"\b[\wà-ÿ'’-]+,\s+[\wà-ÿ'’-]+\s+(?:et|and|ou|or)\s+[\wà-ÿ'’-]+", s, re.I):
            reasons.append("Énumération ternaire (règle de trois)")
            s_score += 12
        if len(s_words) >= 12 and mean_len and abs(len(s_words) - mean_len) <= 0.15 * mean_len:
            s_score += 8   # phrase pile dans la moyenne : contribue sans être nommée
        s_score = min(100, s_score)
        label = "ia" if s_score >= 50 else ("suspect" if s_score >= 22 else "ok")
        sentence_details.append({"text": s[:300], "score": s_score,
                                 "label": label, "reasons": reasons})
    n_ia = sum(1 for x in sentence_details if x["label"] == "ia")
    n_susp = sum(1 for x in sentence_details if x["label"] == "suspect")

    # ───────────── Rapport de correction (problèmes nommés) ─────────────────
    stop = _STOP_FR | _STOP_EN
    problems = []

    def prob(severity, title, detail, fix, examples=None):
        problems.append({"severity": severity, "title": title, "detail": detail,
                         "fix": fix, "examples": examples or []})

    # 1. Répétition de mots pleins (le « mot XXX répété » du rapport)
    content = Counter(w for w in words if len(w) >= 4 and w not in stop and not w.isdigit())
    for w, c in content.most_common(6):
        density = c * per_k
        if c >= 5 and density >= 7:
            sev = "critique" if density >= 15 else "important"
            prob(sev, f"Répétition du mot « {w} »",
                 f"{c} occurrences ({round(density,1)} pour 1000 mots) — densité anormale.",
                 f"Varier avec des synonymes, pronoms ou reformulations ; viser ≤ {max(2, round(4/per_k))} occurrences.")
    # 2. 4-grams répétés (formules recopiées) — dédupliqués : deux formules qui
    # se chevauchent (3 mots communs) appartiennent à la même phrase recopiée
    reported_fg: list[set] = []
    for fg, c in fg_counts.most_common(12):
        if c < 3:
            break
        toks = set(fg.split())
        if any(len(toks & r) >= 3 for r in reported_fg):
            continue
        reported_fg.append(toks)
        prob("important", "Formule répétée mot pour mot",
             f"« {fg} » revient {c} fois.",
             "Reformuler chaque occurrence différemment.")
        if len(reported_fg) >= 3:
            break
    # 3. Clichés IA
    if phrase_count:
        top_ph = sorted(phrase_hits, key=lambda p: -low.count(p))[:5]
        prob("critique" if phrase_count * per_k >= 6 else "important",
             "Expressions cliché IA",
             f"{phrase_count} clichés détectés.",
             "Supprimer ou remplacer par une formulation concrète et personnelle.",
             [f"« {p} » ×{low.count(p)}" for p in top_ph])
    # 4. Connecteurs mécaniques
    if conn_ratio >= 0.15:
        used = Counter(next((c for c in connectors if _norm(s).startswith(c)), "")
                       for s in raw_sents)
        used.pop("", None)
        prob("important", "Connecteurs mécaniques en début de phrase",
             f"{round(conn_ratio*100)}% des phrases ({conn_starts}/{len(raw_sents)}) "
             "ouvrent par un connecteur logique.",
             "En garder 1 sur 3 maximum ; attaquer les phrases directement par le sujet.",
             [f"« {c.capitalize()} » ×{n}" for c, n in used.most_common(4)])
    # 5. Vocabulaire IA
    aw_counts = Counter(w for w in words if w in ai_words)
    if aw and aw * per_k >= 8:
        prob("important", "Vocabulaire IA emphatique",
             f"{aw} adjectifs génériques ({round(aw*per_k,1)}/1000 mots).",
             "Remplacer par des faits : au lieu d'« optimal », donner le chiffre qui le prouve.",
             [f"« {w} » ×{c}" for w, c in aw_counts.most_common(5)])
    # 6. Rythme trop régulier
    if cv < 0.42:
        prob("important", "Rythme de phrases trop régulier (burstiness faible)",
             f"Variation de {round(cv,2)} (humain typique : 0,55-0,90) — phrases de "
             f"{round(mean_len)} mots en moyenne, presque toutes semblables.",
             "Casser le rythme : insérer des phrases de 3-5 mots. Puis une très longue, "
             "qui déroule. Et recommencer.")
    # 7. Déficit de concret
    if numbers * per_k < 3:
        prob("critique" if numbers == 0 else "important",
             "Quasi-absence de données concrètes",
             f"Seulement {numbers} chiffres (prix, dates, quantités) dans tout le texte.",
             "Ajouter prix réels, délais, dates, pourcentages, références vérifiables.")
    if fp == 0:
        prob("mineur", "Aucune marque de vécu",
             "Pas de première personne ni d'expérience rapportée.",
             "Une anecdote ou un retour d'expérience crédibilise et humanise.")
    # 8. Flou quantitatif
    if vg * per_k >= 15:
        vg_counts = Counter()
        for v in vague:
            c = low.count(v)
            if c:
                vg_counts[v] = c
        prob("mineur", "Flou quantitatif",
             f"{vg} marqueurs vagues ({round(vg*per_k,1)}/1000 mots).",
             "Remplacer « de nombreux » par le nombre réel.",
             [f"« {v} » ×{c}" for v, c in vg_counts.most_common(4)])
    sev_rank = {"critique": 0, "important": 1, "mineur": 2}
    problems.sort(key=lambda x: sev_rank[x["severity"]])

    recos = []
    if families.get("D", {}).get("score", 0) >= 55:
        recos.append("Injecter du concret : prix réels, dates, noms de lieux/marques, chiffres vérifiables.")
    if families.get("B", {}).get("score", 0) >= 55:
        recos.append("Supprimer les clichés IA détectés (voir preuves) et les connecteurs mécaniques en début de phrase.")
    if families.get("A", {}).get("score", 0) >= 55:
        recos.append("Casser le rythme : alterner phrases très courtes et longues, varier la longueur des paragraphes.")
    if families.get("F", {}).get("score", 0) >= 55:
        recos.append("Ajouter de l'oralité : apartés entre parenthèses, questions, une pointe d'opinion assumée.")
    if families.get("C", {}).get("score", 0) >= 55:
        recos.append("Dé-gabaritiser : varier les formats de titres, éviter la conclusion scolaire et les puces « Terme : ».")
    if not recos:
        recos.append("Profil sain : rien à signaler de critique.")

    return {
        "language": lang,
        "word_count": n_words,
        "sentence_count": len(raw_sents),
        "ai_score": global_score,
        "verdict": verdict,
        "google_risk": google_risk,
        "google_verdict": google_verdict,
        "families": families,
        "signals": signals,
        "evidence": evid[:6],
        "matched_phrases": phrase_hits[:15],
        "recommendations": recos,
        "sentences": sentence_details,
        "sentence_stats": {"ia": n_ia, "suspect": n_susp,
                           "ok": len(sentence_details) - n_ia - n_susp},
        "report": problems,
    }


# ---------------------------------------------------------------------------
# Mode URL (page) et mode site (échantillon sitemap)
# ---------------------------------------------------------------------------

_UA = {"User-Agent": "Mozilla/5.0 (compatible; JubaWorkflowBot/1.0) AI-Detector"}


def _extract_main_text(html: str) -> tuple[str, dict]:
    """Extrait le texte principal + signaux page (auteur, dates, liens…)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    parts = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        t = el.get_text(" ", strip=True)
        if len(t) > 2:
            prefix = "## " if el.name in ("h2", "h3", "h4") else ""
            parts.append(prefix + t)
    text = "\n\n".join(parts)

    full = html.lower()
    page_signals = {
        "has_author": bool(re.search(r'rel="author"|class="[^"]*author|"author"\s*:', full)),
        "has_date": bool(re.search(r"<time|datepublished|datemodified", full)),
        "external_links": len({
            m for m in re.findall(r'href="(https?://[^"/]+)', html)
        }),
        "has_comments": bool(re.search(r"class=\"[^\"]*comment|id=\"comments", full)),
    }
    return text, page_signals


def analyze_page(url: str) -> dict:
    """Analyse une URL : contenu principal + signaux page."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, headers=_UA, follow_redirects=True, timeout=25)
        r.raise_for_status()
    except Exception as exc:
        return {"error": f"Impossible de charger la page : {exc}"}
    text, page_signals = _extract_main_text(r.text)
    result = analyze_text(text)
    if "error" in result:
        return result
    result["url"] = url
    result["page_signals"] = page_signals

    # Ajustements page : pas d'auteur ni date ni liens externes → risque Google +
    missing = sum(1 for k in ("has_author", "has_date", "has_comments")
                  if not page_signals.get(k))
    if page_signals.get("external_links", 0) <= 1:
        missing += 1
    bump = missing * 4
    result["google_risk"] = min(100, result["google_risk"] + bump)
    if bump:
        result["recommendations"].insert(0,
            "Signaux E-E-A-T absents sur la page : auteur identifié, date de "
            "publication, sources externes citées — Google s'en sert pour "
            "juger la fiabilité.")
    return result


def analyze_site(url: str, max_pages: int = 8, progress=None) -> dict:
    """Échantillonne des pages du site (sitemap ou liens internes) et agrège :
    score IA moyen, uniformité inter-pages (gabarits), publication en rafale."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    base = url.rstrip("/")
    host = re.sub(r"^https?://", "", base).split("/")[0]

    urls: list[str] = []
    lastmods: list[str] = []
    try:
        with httpx.Client(headers=_UA, follow_redirects=True, timeout=25) as client:
            # sitemap direct ou via robots.txt
            candidates = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]
            try:
                rob = client.get(f"{base}/robots.txt")
                candidates += re.findall(r"(?i)sitemap:\s*(\S+)", rob.text)
            except Exception:
                pass
            seen = set()
            queue = list(dict.fromkeys(candidates))
            while queue and len(urls) < 200:
                sm = queue.pop(0)
                if sm in seen:
                    continue
                seen.add(sm)
                try:
                    r = client.get(sm)
                    if r.status_code != 200:
                        continue
                    if "<sitemapindex" in r.text.lower():
                        queue += re.findall(r"<loc>\s*(\S+?)\s*</loc>", r.text)[:20]
                    else:
                        urls += re.findall(r"<loc>\s*(\S+?)\s*</loc>", r.text)
                        lastmods += re.findall(r"<lastmod>\s*(\S+?)\s*</lastmod>", r.text)
                except Exception:
                    continue
            urls = [u for u in dict.fromkeys(urls) if host in u]
            if not urls:
                # repli : liens internes de l'accueil
                r = client.get(base)
                urls = [u for u in dict.fromkeys(
                    re.findall(r'href="(https?://[^"#?]+)"', r.text)) if host in u][:40]

            if not urls:
                return {"error": "Aucune page découverte (ni sitemap ni liens internes)"}

            # Échantillon réparti (début / milieu / fin du sitemap)
            step = max(1, len(urls) // max_pages)
            sample = urls[::step][:max_pages]

            pages = []
            for i, u in enumerate(sample):
                try:
                    r = client.get(u)
                    if r.status_code != 200:
                        continue
                    text, psig = _extract_main_text(r.text)
                    res = analyze_text(text)
                    if "error" in res:
                        continue
                    title_m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
                    pages.append({
                        "url": u,
                        "title": (title_m.group(1).strip()[:110] if title_m else ""),
                        "ai_score": res["ai_score"],
                        "google_risk": res["google_risk"],
                        "word_count": res["word_count"],
                        "verdict": res["verdict"],
                        "top_family": max(res["families"].values(), key=lambda f: f["score"])["label"]
                        if res["families"] else "",
                        "page_signals": psig,
                    })
                except Exception:
                    continue
                if progress:
                    progress(i + 1, len(sample))
    except Exception as exc:
        return {"error": f"Analyse du site impossible : {exc}"}

    if not pages:
        return {"error": "Aucune page analysable (contenus trop courts ?)"}

    scores = [p["ai_score"] for p in pages]
    avg = round(statistics.mean(scores))

    # Signaux site : uniformité inter-pages
    site_signals = []
    if len(scores) >= 3:
        spread = statistics.pstdev(scores)
        site_signals.append({
            "name": "Uniformité des scores entre pages",
            "raw": f"écart-type {round(spread,1)}",
            "score": _ramp(spread, 3, 18, invert=True),
            "why": "Des pages toutes identiquement « IA » trahissent une génération en série.",
        })
    # Gabarit de titres (mots partagés entre titres)
    titles = [set(_WORD_RE.findall(_norm(p["title"]))) for p in pages if p["title"]]
    if len(titles) >= 3:
        pairs, sim = 0, 0.0
        for i in range(len(titles)):
            for j in range(i + 1, len(titles)):
                inter = len(titles[i] & titles[j])
                union = len(titles[i] | titles[j]) or 1
                sim += inter / union
                pairs += 1
        avg_sim = sim / pairs
        site_signals.append({
            "name": "Titres construits sur le même gabarit",
            "raw": f"similarité {round(avg_sim*100)}%",
            "score": _ramp(avg_sim, 0.15, 0.55),
            "why": "« Service à Ville1 », « Service à Ville2 »… : gabarit dupliqué à l'échelle.",
        })
    # Publication en rafale (lastmod)
    if len(lastmods) >= 4:
        days = Counter(lm[:10] for lm in lastmods)
        top_day, top_n = days.most_common(1)[0]
        burst = top_n / len(lastmods)
        site_signals.append({
            "name": "Publication en rafale",
            "raw": f"{round(burst*100)}% des pages datées du {top_day}",
            "score": _ramp(burst, 0.2, 0.8),
            "why": "Des centaines de pages « publiées » le même jour = génération de masse.",
        })
    # E-E-A-T site
    no_author = sum(1 for p in pages if not p["page_signals"].get("has_author"))
    site_signals.append({
        "name": "Pages sans auteur identifié",
        "raw": f"{no_author}/{len(pages)}",
        "score": _ramp(no_author / len(pages), 0.3, 1.0),
        "why": "L'absence systématique d'auteur pèse sur l'évaluation E-E-A-T.",
    })

    site_bonus = statistics.mean([s["score"] for s in site_signals]) if site_signals else 0
    site_ai = round(min(100, avg * 0.75 + site_bonus * 0.25))
    risk = round(min(100, statistics.mean([p["google_risk"] for p in pages]) * 0.7
                     + site_bonus * 0.3))

    return {
        "url": base,
        "pages_discovered": len(urls),
        "pages_analyzed": len(pages),
        "ai_score": site_ai,
        "verdict": ("Site très probablement généré par IA" if site_ai >= 70 else
                    "Site en partie généré par IA" if site_ai >= 50 else
                    "Site à dominante humaine"),
        "google_risk": risk,
        "google_verdict": ("Risque élevé (profil « scaled content abuse »)" if risk >= 65 else
                           "Risque modéré" if risk >= 45 else "Risque faible"),
        "site_signals": site_signals,
        "pages": sorted(pages, key=lambda p: -p["ai_score"]),
    }
