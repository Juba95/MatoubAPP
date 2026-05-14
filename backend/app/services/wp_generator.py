"""
wp_generator.py — Service complet de génération WordPress
=========================================================
Porté depuis le notebook Colab. Adapté pour FastAPI :
  - httpx au lieu de requests
  - logging au lieu de print
  - settings via app.config.get_settings()
  - Pas de Google Drive / Colab
  - Retourne des dicts structurés
"""

from __future__ import annotations

import ftplib
import io
import json
import logging
import re
import secrets
import shutil
import string
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import anthropic
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
#  Règles d'humanisation (injectées dans chaque génération Claude)
# ─────────────────────────────────────────────────────────────────
HUMANIZER_RULES = """
╔══ RÈGLES RÉDACTION HUMAINE — OBLIGATOIRES ══════════════════════════╗
║  Chaque texte doit être indétectable par GPTZero, Originality.ai,   ║
║  Google et tous les détecteurs d'IA. Rédige comme un vrai humain.   ║
╚═════════════════════════════════════════════════════════════════════╝

À FAIRE — marques d'écriture humaine :
• Alterner phrases courtes (< 10 mots) et longues (> 25 mots)
• Commencer certaines phrases par "Et", "Mais", "Car", "Parce que", "D'ailleurs"
• Chiffres précis non ronds : "depuis 11 ans", "47 clients satisfaits", "2h30 de délai"
• Références locales concrètes (rue, quartier, ville, région)
• Petites incises conversationnelles : "franchement", "soyons honnêtes", "entre nous"
• Questions rhétoriques ponctuelles
• Parenthèses occasionnelles (ça casse le rythme monotone)
• Transitions naturelles : "Au fait,", "Concrètement,", "Pour être direct,"
• Varier pronoms : "nous", "l'équipe", "nos techniciens", "moi"
• Expressions du secteur métier (jargon professionnel naturel)
• Imparfait ou conditionnel de temps en temps ("Ce que nos clients aimaient…")

À ÉVITER — marqueurs d'IA détectés :
✗ "révolutionnaire", "innovant", "solution", "dynamique", "optimal", "synergies"
✗ "fort de X années d'expérience", "notre équipe d'experts passionnés"
✗ "n'hésitez pas à nous contacter", "nous sommes à votre disposition"
✗ "dans un monde où", "à l'ère du numérique", "dans un contexte"
✗ Listes à puces trop régulières et uniformes (même longueur)
✗ Chaque paragraphe qui commence par le sujet de la phrase
✗ "Nous vous proposons", "Nous vous offrons", "Nous mettons à votre service"
✗ "de qualité", "sur mesure", "clé en main", "accompagnement personnalisé"
✗ Répétition du même mot-clé toutes les 2 phrases
"""


# ─────────────────────────────────────────────────────────────────
#  Utilitaires FTP
# ─────────────────────────────────────────────────────────────────
def _ftp_connect(cfg: dict) -> ftplib.FTP:
    ftp = ftplib.FTP(cfg["host"])
    ftp.login(cfg["user"], cfg["pwd"])
    return ftp


def _ftp_mkd_safe(conn: ftplib.FTP, path: str):
    try:
        conn.mkd(path)
    except ftplib.error_perm:
        pass


def _ftp_upload_dir(conn: ftplib.FTP, local: Path, remote: str,
                    label: str = "") -> int:
    """Upload récursif d'un dossier local vers le FTP. Retourne le nb de fichiers."""
    _ftp_mkd_safe(conn, remote)
    n = 0
    for item in local.iterdir():
        r_path = f"{remote}/{item.name}"
        if item.is_dir():
            n += _ftp_upload_dir(conn, item, r_path, label)
        else:
            conn.storbinary(f"STOR {r_path}", item.open("rb"))
            n += 1
    return n


# ═════════════════════════════════════════════════════════════════
#  SITE RESETTER
# ═════════════════════════════════════════════════════════════════

class SiteResetter:
    """
    Vide le FTP et la BDD via un script PHP temporaire uploadé sur le serveur.
    Ultra-rapide : le serveur supprime tout localement.
    """

    PHP_RESETTER = r"""<?php
@set_time_limit(300); @ini_set('memory_limit','128M');
$tk=$_GET['tk']??''; if(!$tk||$tk!=='{TOKEN}'){http_response_code(403);die('Forbidden');}
$action=$_GET['action']??''; $base=rtrim('{ROOT}','/');
header('Content-Type: application/json');

function rrmdir($dir){
    if(!is_dir($dir)) return 0;
    $n=0;
    $it=new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($dir, RecursiveDirectoryIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST);
    foreach($it as $f){
        $p=(string)$f;
        if($p===__FILE__) continue;
        $f->isDir() ? rmdir($p) : unlink($p);
        $n++;
    }
    return $n;
}

switch($action){
    case 'ping':
        echo json_encode(['ok'=>true,'php'=>PHP_VERSION,'dir'=>__DIR__,'base'=>$base]);
        break;
    case 'reset_files':
        $n = rrmdir(__DIR__);
        echo json_encode(['ok'=>true,'deleted'=>$n,'dir'=>__DIR__]);
        break;
    case 'reset_db':
        $d=json_decode(file_get_contents('php://input'),true);
        try {
            $pdo=new PDO(
                "mysql:host={$d['host']};dbname={$d['name']};charset=utf8",
                $d['user'], $d['pass'],
                [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]
            );
            $pdo->exec('SET FOREIGN_KEY_CHECKS=0');
            $tables=$pdo->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN);
            foreach($tables as $t){ $pdo->exec("DROP TABLE IF EXISTS `$t`"); }
            $pdo->exec('SET FOREIGN_KEY_CHECKS=1');
            echo json_encode(['ok'=>true,'tables'=>count($tables)]);
        } catch(Exception $e){
            echo json_encode(['ok'=>false,'error'=>$e->getMessage()]);
        }
        break;
    case 'cleanup':
        echo json_encode(['ok'=>true]);
        ob_flush(); flush();
        @unlink(__FILE__);
        break;
    default:
        echo json_encode(['ok'=>false,'error'=>'unknown action']);
}
?>"""

    def __init__(self, ftp_host: str, ftp_user: str, ftp_pass: str,
                 ftp_root: str, db_host: str, db_name: str,
                 db_user: str, db_pass: str, domain: str):
        self.ftp = dict(host=ftp_host, user=ftp_user, pwd=ftp_pass, root=ftp_root)
        self.db = dict(host=db_host, name=db_name, user=db_user, pwd=db_pass)
        self.domain = domain.rstrip("/")
        self._token = secrets.token_hex(24)
        self._fname = f"_reset_{secrets.token_hex(6)}.php"

    def _call(self, action: str, body: dict | None = None,
              timeout: int = 60) -> dict:
        script_url = (
            f"{self.domain}/{self._fname}?tk={self._token}&action={action}"
        )
        try:
            if body:
                r = httpx.post(script_url, json=body, timeout=timeout)
            else:
                r = httpx.get(script_url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise RuntimeError(f"[reset/{action}] {e}")

    def _upload_php(self):
        php = (self.PHP_RESETTER
               .replace("{TOKEN}", self._token)
               .replace("{ROOT}", self.ftp["root"]))
        conn = _ftp_connect(self.ftp)
        conn.storbinary(
            f"STOR {self.ftp['root']}/{self._fname}",
            io.BytesIO(php.encode()),
        )
        conn.quit()

    def _cleanup_php(self):
        try:
            self._call("cleanup", timeout=10)
        except Exception:
            pass
        try:
            conn = _ftp_connect(self.ftp)
            conn.delete(f"{self.ftp['root']}/{self._fname}")
            conn.quit()
        except Exception:
            pass

    def reset(self) -> dict:
        """Reset complet (FTP + BDD). Retourne un résumé."""
        t0 = time.time()

        # Upload du script PHP
        logger.info("Upload du script PHP de reset")
        self._upload_php()

        # Ping
        for attempt in range(10):
            try:
                ping = self._call("ping", timeout=8)
                if ping.get("ok"):
                    logger.info("Script PHP reset accessible (PHP %s)",
                                ping.get("php", "?"))
                    break
            except Exception:
                time.sleep(1)
        else:
            self._cleanup_php()
            raise RuntimeError(
                "Script PHP reset inaccessible — verifiez FTP_ROOT et DOMAIN."
            )

        # Suppression fichiers
        logger.info("Suppression des fichiers (cote serveur)")
        r_files = self._call("reset_files", timeout=60)
        files_deleted = r_files.get("deleted", 0)

        # Suppression BDD
        logger.info("Suppression des tables BDD")
        r_db = self._call("reset_db", body={
            "host": self.db["host"], "name": self.db["name"],
            "user": self.db["user"], "pass": self.db["pwd"],
        }, timeout=30)
        tables_deleted = r_db.get("tables", 0) if r_db.get("ok") else 0

        self._cleanup_php()
        elapsed = time.time() - t0
        logger.info("Reset termine en %.0fs (%d fichiers, %d tables)",
                     elapsed, files_deleted, tables_deleted)
        return {
            "files_deleted": files_deleted,
            "tables_deleted": tables_deleted,
            "elapsed_seconds": round(elapsed, 1),
        }


# ═════════════════════════════════════════════════════════════════
#  WEB ARCHIVE SCRAPER — Wayback Machine
# ═════════════════════════════════════════════════════════════════

class WebArchiveScraper:
    """
    Recupere le contenu d'un site depuis la Wayback Machine.
    Auto-detecte la derniere archive ou utilise une URL custom.
    """

    AVAILABILITY_API = "https://archive.org/wayback/available"
    CDX_API = "http://web.archive.org/cdx/search/cdx"

    def __init__(self, domain: str, custom_url: str = ""):
        self.domain = domain.rstrip("/")
        self.custom_url = custom_url.strip()

    def _find_snapshot(self) -> str | None:
        clean_domain = re.sub(r"https?://", "", self.domain)
        logger.info("Recherche d'archives Wayback Machine pour : %s",
                     clean_domain)

        # 1. API de disponibilite
        try:
            r = httpx.get(
                self.AVAILABILITY_API,
                params={"url": clean_domain},
                timeout=15,
            )
            snap = r.json().get("archived_snapshots", {}).get("closest", {})
            if snap.get("available") and snap.get("url"):
                return snap["url"]
        except Exception:
            pass

        # 2. CDX API
        try:
            r = httpx.get(self.CDX_API, params={
                "url": clean_domain,
                "output": "json",
                "fl": "timestamp,original",
                "limit": "-1",
                "filter": "statuscode:200",
            }, timeout=15)
            rows = r.json()
            if len(rows) > 1:
                ts, orig = rows[-1]
                return f"https://web.archive.org/web/{ts}/{orig}"
        except Exception:
            pass

        return None

    def _fetch_html(self, url: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; WPGenerator/1.0)"}
        r = httpx.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.text

    def _extract(self, html: str) -> dict:
        def strip_tags(s: str) -> str:
            return re.sub(r"<[^>]+>", "", s).strip()

        # Supprimer scripts/styles/toolbar Wayback Machine
        html = re.sub(
            r"<!-- BEGIN WAYBACK.*?END WAYBACK[^>]*-->", "", html,
            flags=re.DOTALL,
        )
        html = re.sub(
            r"<script[^>]*>.*?</script>", "", html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        html = re.sub(
            r"<style[^>]*>.*?</style>", "", html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        m = re.search(
            r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL,
        )
        title = strip_tags(m.group(1)) if m else ""

        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']'
            r'([^"\']{10,})',
            html, re.IGNORECASE,
        )
        meta_desc = m.group(1).strip() if m else ""

        headings = [
            strip_tags(h) for h in
            re.findall(
                r"<h[1-3][^>]*>(.*?)</h[1-3]>", html,
                re.IGNORECASE | re.DOTALL,
            )
            if strip_tags(h)
        ][:20]

        paragraphs = [
            strip_tags(p) for p in
            re.findall(
                r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL,
            )
            if len(strip_tags(p)) > 40
        ][:15]

        list_items = [
            strip_tags(li) for li in
            re.findall(
                r"<li[^>]*>(.*?)</li>", html, re.IGNORECASE | re.DOTALL,
            )
            if len(strip_tags(li)) > 10
        ][:20]

        return dict(
            title=title, meta_desc=meta_desc,
            headings=headings, paragraphs=paragraphs, list_items=list_items,
        )

    def get_context(self) -> dict:
        """
        Retourne un dict avec :
          - archive_url: str | None
          - raw: dict (title, meta_desc, headings, ...)
          - prompt_context: str  (bloc texte a injecter dans Claude)
        """
        archive_url = self.custom_url or self._find_snapshot()
        if not archive_url:
            logger.warning("Aucune archive Wayback Machine trouvee")
            return {"archive_url": None, "raw": {}, "prompt_context": None}

        logger.info("Archive trouvee : %s", archive_url)
        try:
            html = self._fetch_html(archive_url)
            content = self._extract(html)
        except Exception as e:
            logger.warning("Impossible de scraper l'archive : %s", e)
            return {"archive_url": archive_url, "raw": {}, "prompt_context": None}

        logger.info(
            "Contenu archive extrait : %d titres, %d paragraphes, %d listes",
            len(content["headings"]),
            len(content["paragraphs"]),
            len(content["list_items"]),
        )

        lines = [
            "═══ CONTENU DU SITE EXISTANT (Wayback Machine) ═══",
            f"Archive URL : {archive_url}",
            f"Titre : {content['title']}",
            f"Meta description : {content['meta_desc']}",
        ]
        if content["headings"]:
            lines.append("\nTitres / sections :")
            lines += [f"  • {h}" for h in content["headings"]]
        if content["paragraphs"]:
            lines.append("\nContenu textuel :")
            lines += [f"  › {p[:250]}" for p in content["paragraphs"]]
        if content["list_items"]:
            lines.append("\nServices / produits listes :")
            lines += [f"  – {li[:150]}" for li in content["list_items"]]
        lines += [
            "",
            "═══ INSTRUCTIONS POUR CLAUDE ═══",
            "Ce contenu provient du site existant du client.",
            "OBJECTIF : ameliore-le — ne repars PAS de zero.",
            "  • Conserve les informations specifiques (nom, services, localisation, tarifs).",
            "  • Modernise le wording, supprime les formulations obsoletes ou generiques.",
            "  • Optimise pour le SEO (titres, descriptions, structure).",
            "  • Enrichis les sections incompletes avec du contenu coherent.",
            "  • Ton professionnel et naturel — aucune formulation IA.",
            "═══════════════════════════════════════════════════",
        ]
        prompt_context = "\n".join(lines)
        return {
            "archive_url": archive_url,
            "raw": content,
            "prompt_context": prompt_context,
        }


# ═════════════════════════════════════════════════════════════════
#  COMPETITOR ANALYZER
# ═════════════════════════════════════════════════════════════════

class CompetitorAnalyzer:
    """
    Scrape 2-5 sites concurrents et genere un brief superieur via Claude.
    """

    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(self, claude_client: anthropic.Anthropic):
        self.client = claude_client

    def _scrape(self, url: str) -> dict:
        try:
            r = httpx.get(
                url, timeout=15, follow_redirects=True,
                headers={
                    "User-Agent": self.UA,
                    "Accept-Language": "fr-FR,fr;q=0.9",
                },
            )
            r.raise_for_status()
            html = r.text
        except Exception as e:
            return {"url": url, "error": str(e)}

        def strip(s):
            return re.sub(r"<[^>]+>", "", s or "").strip()

        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        meta_m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
            html, re.I,
        )
        if not meta_m:
            meta_m = re.search(
                r'<meta[^>]+content=["\']([^"\']{30,})["\'][^>]+'
                r'name=["\']description',
                html, re.I,
            )

        h1s = [strip(m) for m in re.findall(
            r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)][:3]
        h2s = [strip(m) for m in re.findall(
            r"<h2[^>]*>(.*?)</h2>", html, re.I | re.S)][:8]
        h3s = [strip(m) for m in re.findall(
            r"<h3[^>]*>(.*?)</h3>", html, re.I | re.S)][:6]

        nav_hrefs = re.findall(
            r'<a[^>]+href=["\']([^"\'#?]{2,80})["\'][^>]*>(.*?)</a>',
            html, re.I | re.S,
        )
        base = "/".join(url.split("/")[:3])
        pages = []
        for href, txt in nav_hrefs:
            clean = strip(txt)
            if (clean and len(clean) < 40
                    and (href.startswith("/") or base in href)):
                pages.append(clean)
        pages = list(dict.fromkeys(p for p in pages if len(p) > 2))[:12]

        raw_colors = re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", html)
        top_colors = ["#" + c for c, _ in Counter(raw_colors).most_common(8)]

        feat_kw = [
            "service", "product", "produit", "temoignage", "avis", "review",
            "faq", "equipe", "team", "galerie", "gallery", "tarif", "prix",
            "price", "hero", "banner", "cta", "contact", "about", "blog",
        ]
        features = list({k for k in feat_kw if re.search(k, html, re.I)})

        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
        texts = [strip(p)[:180] for p in paras if len(strip(p)) > 60][:6]

        return {
            "url": url,
            "title": strip(title_m.group(1)) if title_m else "",
            "meta": meta_m.group(1).strip() if meta_m else "",
            "h1": [t for t in h1s if t],
            "h2": [t for t in h2s if t],
            "h3": [t for t in h3s if t],
            "pages": pages,
            "colors": top_colors,
            "features": features,
            "content": texts,
        }

    def analyze(self, competitor_urls: list[str]) -> dict:
        """
        Scrape les concurrents et retourne un brief optimise.
        Retourne : {"brief": str, "analyses": list[dict], "errors": list[str]}
        """
        logger.info("Analyse de %d concurrent(s)", len(competitor_urls))
        analyses: list[dict] = []
        errors: list[str] = []

        for i, url in enumerate(competitor_urls):
            logger.info("[%d/%d] %s", i + 1, len(competitor_urls), url)
            try:
                data = self._scrape(url)
                if "error" in data:
                    errors.append(f"{url}: {data['error']}")
                    logger.warning("Echec scraping %s : %s", url, data["error"])
                else:
                    analyses.append(data)
                    logger.info("OK : %s", data["title"][:45] or "(sans titre)")
            except Exception as e:
                errors.append(f"{url}: {e}")
                logger.warning("Exception scraping %s : %s", url, e)

        if not analyses:
            raise RuntimeError(
                "Aucun concurrent n'a pu etre analyse — verifiez les URLs."
            )

        logger.info("Generation du brief concurrentiel via Claude")

        all_colors = ", ".join(set(
            c for a in analyses for c in a.get("colors", [])[:3]
        ))

        system = f"""Tu es un expert en strategie web, SEO et copywriting francais.
On t'a fourni l'analyse de sites concurrents.
Genere un BRIEF COMPLET pour creer un site WordPress qui les surpasse.

Le brief doit etre redige en texte naturel (pas de JSON), comme si tu decrivais le site a un developpeur.
Il doit contenir :
1. Nom + activite (inspire des concurrents, mais unique)
2. Palette de couleurs OBLIGATOIREMENT DIFFERENTE de toutes celles detectees chez les concurrents
3. 3 a 5 pages avec leur contenu DETAILLE (pas de placeholder)
4. Fonctionnalites presentes chez les concurrents a reproduire ou ameliorer
5. Fonctionnalites MANQUANTES chez eux (ton avantage competitif)
6. Sections par page : hero, services, temoignages, CTA, contact...
7. Ton editorial adapte au secteur
8. Instructions Yoast SEO : title + meta description pour chaque page (optimisees, non generiques)

{HUMANIZER_RULES}

IMPERATIF : Tout le contenu redige dans ce brief doit etre 100% humain et indetectable par les IA.
La palette de couleurs DOIT etre differente des couleurs : {all_colors}.
"""

        user = (
            "Analyse des concurrents :\n\n"
            + json.dumps(analyses, ensure_ascii=False, indent=2)
            + f"\n\nGenere le brief complet pour un site superieur "
              f"({len(analyses)} concurrents analyses)."
        )

        try:
            r = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=6000,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            brief = r.content[0].text.strip()
        except Exception as e:
            raise RuntimeError(f"Erreur Claude (brief concurrentiel) : {e}")

        logger.info("Brief genere — %d caracteres", len(brief))
        return {"brief": brief, "analyses": analyses, "errors": errors}


# ═════════════════════════════════════════════════════════════════
#  EAT GENERATOR — Blocs E-E-A-T Google
# ═════════════════════════════════════════════════════════════════

class EATGenerator:
    """
    Genere les blocs E-E-A-T pour renforcer la confiance et le SEO local :
    FAQ, avis, coordonnees, Google Maps, rassurance, HowTo.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg

    # ── Schemas JSON-LD ──────────────────────────────────────────

    def schema_faq(self, faqs: list[dict]) -> str:
        items = [
            {
                "@type": "Question",
                "name": f["q"],
                "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
            }
            for f in faqs
        ]
        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": items,
        }
        return (
            '<script type="application/ld+json">'
            f"{json.dumps(schema, ensure_ascii=False)}</script>"
        )

    def schema_reviews(self, reviews: list[dict], rating: float,
                       count: int) -> str:
        schema = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": self.cfg.get("name", ""),
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": str(rating),
                "reviewCount": str(count),
                "bestRating": "5",
            },
            "review": [
                {
                    "@type": "Review",
                    "author": {
                        "@type": "Person",
                        "name": r.get("author", "Client"),
                    },
                    "reviewRating": {
                        "@type": "Rating",
                        "ratingValue": str(r.get("rating", 5)),
                    },
                    "reviewBody": r.get("text", ""),
                    "datePublished": r.get("date", "2024-01-01"),
                }
                for r in reviews
            ],
        }
        return (
            '<script type="application/ld+json">'
            f"{json.dumps(schema, ensure_ascii=False)}</script>"
        )

    def schema_local_business(self) -> str:
        c = self.cfg
        hours_spec = []
        day_map = {
            "Lundi": "Monday", "Mardi": "Tuesday", "Mercredi": "Wednesday",
            "Jeudi": "Thursday", "Vendredi": "Friday",
            "Samedi": "Saturday", "Dimanche": "Sunday",
        }
        for day_fr, slot in c.get("hours", {}).items():
            if slot.lower() in ("ferme", "closed", ""):
                continue
            opens, _, closes = slot.partition("-")
            hours_spec.append({
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": f"https://schema.org/{day_map.get(day_fr, day_fr)}",
                "opens": opens.strip(),
                "closes": closes.strip(),
            })
        schema = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": c.get("name", ""),
            "address": {
                "@type": "PostalAddress",
                "streetAddress": c.get("address", ""),
            },
            "telephone": c.get("phone", ""),
            "email": c.get("email", ""),
            "url": c.get("domain", ""),
            "openingHoursSpecification": hours_spec,
        }
        if c.get("lat") and c.get("lng"):
            schema["geo"] = {
                "@type": "GeoCoordinates",
                "latitude": c["lat"],
                "longitude": c["lng"],
            }
        return (
            '<script type="application/ld+json">'
            f"{json.dumps(schema, ensure_ascii=False)}</script>"
        )

    def schema_how_to(self, steps: list[dict]) -> str:
        schema = {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": "Comment ca marche",
            "step": [
                {
                    "@type": "HowToStep",
                    "name": s.get("name", ""),
                    "text": s.get("text", ""),
                }
                for s in steps
            ],
        }
        return (
            '<script type="application/ld+json">'
            f"{json.dumps(schema, ensure_ascii=False)}</script>"
        )

    def schema_certification(self, label: str,
                             issued_by: str = "") -> str:
        c = self.cfg
        schema: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "EducationalOccupationalCredential",
            "name": f"Certification {label}",
            "credentialCategory": "certification",
            "description": (
                f"Certification professionnelle {label} "
                "— qualite et expertise reconnues."
            ),
        }
        if issued_by:
            schema["recognizedBy"] = {
                "@type": "Organization", "name": issued_by,
            }
        if c.get("name"):
            schema["about"] = {
                "@type": "LocalBusiness",
                "name": c["name"],
                "url": c.get("domain", ""),
            }
        return (
            '<script type="application/ld+json">'
            f"{json.dumps(schema, ensure_ascii=False)}</script>"
        )

    def schema_partners(self, partner_names: list[str]) -> str:
        c = self.cfg
        schema = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": c.get("name", ""),
            "url": c.get("domain", ""),
            "knowsAbout": partner_names,
            "member": [
                {"@type": "Organization", "name": p}
                for p in partner_names
            ],
        }
        return (
            '<script type="application/ld+json">'
            f"{json.dumps(schema, ensure_ascii=False)}</script>"
        )

    # ── Google Maps embed ────────────────────────────────────────

    def maps_iframe(self) -> str:
        addr = self.cfg.get("address", "")
        lat = self.cfg.get("lat", "")
        lng = self.cfg.get("lng", "")
        q = f"{lat},{lng}" if lat and lng else addr
        q_enc = q.replace(" ", "+").replace(",", "%2C")
        return (
            f'<iframe src="https://maps.google.com/maps?q={q_enc}'
            f'&output=embed&hl=fr" '
            f'width="100%" height="450" '
            f'style="border:0;border-radius:8px;" '
            f'allowfullscreen loading="lazy" '
            f'referrerpolicy="no-referrer-when-downgrade"></iframe>'
        )

    # ── Contexte pour Claude ─────────────────────────────────────

    def build_prompt_context(self) -> dict:
        """
        Construit le bloc de contexte EAT.
        Retourne : {"enabled": bool, "prompt_context": str, "schemas": dict}
        """
        c = self.cfg
        ena = c.get("enabled", False)
        if not ena:
            return {"enabled": False, "prompt_context": "", "schemas": {}}

        schemas: dict[str, str] = {}

        # Schema LocalBusiness toujours genere si enabled
        schemas["local_business"] = self.schema_local_business()

        lines = [
            "\n\n╔══ BLOCS E-E-A-T GOOGLE (OBLIGATOIRES) "
            "══════════════════════════╗",
            "║  Integre ces blocs dans les pages indiquees.  "
            "                   ║",
            "║  Chaque bloc doit avoir son schema JSON-LD "
            "dans <head> ou inline.║",
            "╚════════════════════════════════════════════"
            "══════════════════════╝\n",
        ]

        if c.get("name"):
            lines.append(f"ETABLISSEMENT : {c['name']}")
        if c.get("address"):
            lines.append(f"ADRESSE       : {c['address']}")
        if c.get("phone"):
            lines.append(f"TELEPHONE     : {c['phone']}")
        if c.get("email"):
            lines.append(f"EMAIL         : {c['email']}")
        if c.get("hours"):
            lines.append("HORAIRES      :")
            for day, slot in c["hours"].items():
                lines.append(f"  {day:10} {slot}")
        lines.append("")

        if c.get("faq"):
            lines.append(
                "─── BLOC FAQ (toutes les pages) "
                "─────────────────────────────────────"
            )
            lines.append(
                "→ Genere 5 a 8 questions/reponses SPECIFIQUES au metier."
            )
            lines.append(
                "→ Questions naturelles que pose un client (pas generiques)."
            )
            lines.append(
                "→ Reponses completes, concretes, humanisees (2-4 phrases)."
            )
            lines.append(
                "→ Integre le schema JSON-LD FAQPage dans la page."
            )
            lines.append("")

        if c.get("reviews"):
            lines.append(
                "─── BLOC AVIS CLIENTS (homepage uniquement) "
                "─────────────────────────"
            )
            lines.append(
                "→ Genere 4 avis clients realistes, varies, non generiques."
            )
            lines.append(
                "→ Prenoms + villes reels, notes 4-5 etoiles, textes humanises."
            )
            lines.append(
                "→ Integre le schema JSON-LD AggregateRating + Review."
            )
            lines.append("")

        if c.get("hours_block"):
            lines.append(
                "─── BLOC COORDONNEES + HORAIRES (page contact + footer) "
                "─────────────"
            )
            lines.append(
                "→ Affiche adresse, telephone (cliquable tel:), email, horaires."
            )
            lines.append(
                "→ Integre le schema JSON-LD LocalBusiness complet."
            )
            lines.append("")

        if c.get("map"):
            lines.append(
                "─── BLOC GOOGLE MAPS (page contact) "
                "─────────────────────────────────"
            )
            lines.append(f"→ Integre cet iframe Google Maps :")
            lines.append(f"  {self.maps_iframe()}")
            lines.append("")

        if c.get("reassurance"):
            lines.append(
                "─── BLOC RASSURANCE : POURQUOI NOUS CHOISIR "
                "(homepage + autres pages) ─"
            )
            lines.append(
                "→ 3 a 4 arguments forts, SPECIFIQUES au secteur."
            )
            lines.append(
                "→ Chaque argument : icone + titre court + texte 2-3 phrases."
            )
            lines.append("")

        if c.get("how_it_works"):
            lines.append(
                "─── BLOC COMMENT CA MARCHE (homepage) "
                "───────────────────────────────"
            )
            lines.append(
                "→ 3 a 5 etapes numerotees, claires, adaptees au parcours client."
            )
            lines.append("→ Integre le schema JSON-LD HowTo.")
            lines.append("")

        if c.get("partners"):
            names = c.get("partner_names", [])
            if names:
                names_hint = (
                    f"Partenaires a afficher : {', '.join(names)}."
                )
            else:
                names_hint = (
                    "Genere 6 a 8 noms de partenaires/marques reconnus "
                    "dans ce secteur."
                )
            lines.append(
                "─── BLOC PARTENAIRES DEFILANTS (homepage) "
                "──────────────────────────────"
            )
            lines.append(f"→ {names_hint}")
            lines.append("")

        if c.get("certification"):
            cert_url = c.get("certification_url", "")
            cert_label = c.get("certification_label", "")
            lines.append(
                "─── BADGE CERTIFICATION (footer col 3 + hero) "
                "──────────────────────────"
            )
            if cert_url:
                lines.append(
                    f"→ Badge deja genere — integre cette URL : {cert_url}"
                )
            else:
                lbl = cert_label if cert_label else "adapte au secteur"
                lines.append(
                    f"→ Genere un badge SVG inline ({lbl}) dans le footer."
                )
            lines.append("")

        lines.append(
            "REGLE ABSOLUE : Tout le texte de ces blocs doit respecter "
            "les regles d'humanisation."
        )
        lines.append(
            "══════════════════════════════════════════════"
            "════════════════════════\n"
        )
        prompt_context = "\n".join(lines)
        return {
            "enabled": True,
            "prompt_context": prompt_context,
            "schemas": schemas,
        }


# ═════════════════════════════════════════════════════════════════
#  THEME GENERATOR — Claude
# ═════════════════════════════════════════════════════════════════

class ThemeGenerator:
    """
    Genere un theme WordPress (custom PHP ou Divi Builder) via Claude.
    """

    def __init__(self, claude_client: anthropic.Anthropic):
        self.client = claude_client

    def generate_custom(
        self,
        user_prompt: str,
        archive_context: str | None = None,
        image_urls: dict | None = None,
        logo_url: str | None = None,
        eat_context: str | None = None,
    ) -> dict:
        """Genere un theme PHP custom complet. Retourne le dict JSON."""
        logger.info("Generation du theme custom avec Claude")
        system = """You are an expert WordPress theme developer and French copywriter.
Generate a complete WordPress theme. Return ONLY valid JSON — no markdown, no explanation:
{
  "theme_name": "string",
  "theme_slug": "lowercase-slug",
  "tagline": "string",
  "files": {
    "style.css": "/* Theme Name: ...\\nTheme URI:\\nDescription:\\nVersion: 1.0\\n */\\nbody{...}",
    "functions.php": "<?php\\n...",
    "header.php": "<!DOCTYPE html>...",
    "footer.php": "...",
    "front-page.php": "<?php get_header(); ?>...",
    "page.php": "...",
    "page-contact.php": "..."
  },
  "pages": [
    {
      "title": "Accueil", "slug": "accueil", "is_front_page": true,
      "content": "<h2>...</h2><p>...</p>",
      "yoast_title": "string", "yoast_description": "string"
    }
  ]
}
RULES:
- style.css MUST start with the WordPress theme file header comment
- functions.php: wp_enqueue_scripts + register_nav_menus + add_theme_support
- header.php: wp_head() before </head>, body_class() on <body>
- footer.php: wp_footer() before </body>. FOOTER MUST CONTAIN:
  * 3-column layout: (1) logo + tagline, (2) navigation links, (3) contact info + hours
  * Business name, full address, clickable phone (tel:), email
  * Copyright line with current year via date('Y')
  * Links: Mentions legales | Politique de confidentialite
- front-page.php: get_header() + get_footer()
- page-contact.php: PHP contact form using wp_mail()
- All text in natural, human-written French (no AI cliches)
- Content specific to the business (real details, not placeholders)
- Responsive design, mobile-first CSS
- Implement the exact color scheme requested
""" + HUMANIZER_RULES

        full_prompt = (
            f"{archive_context}\n\n{user_prompt}"
            if archive_context else user_prompt
        )
        if image_urls:
            img_block = (
                "\n\n═══ IMAGES GENEREES (utilise ces URLs dans le HTML/CSS) ═══\n"
            )
            for img_id, url in image_urls.items():
                if url:
                    img_block += f"  {img_id}: {url}\n"
            img_block += (
                "Integre ces images dans front-page.php/page.php "
                "via <img src=\"URL\">.\n"
                "═══════════════════════════════════════════════════\n"
            )
            full_prompt += img_block
        if logo_url:
            full_prompt += (
                f"\n\nLOGO DU SITE : {logo_url}\n"
                "Integre ce logo dans header.php.\n"
            )
        if eat_context:
            full_prompt += eat_context

        raw = self._call(system, full_prompt, max_tokens=8000)
        data = json.loads(raw)
        logger.info(
            "Theme '%s' genere (%d fichiers, %d pages)",
            data.get("theme_name", "?"),
            len(data.get("files", {})),
            len(data.get("pages", [])),
        )
        return data

    def generate_divi(
        self,
        user_prompt: str,
        archive_context: str | None = None,
        image_urls: dict | None = None,
        logo_url: str | None = None,
        eat_context: str | None = None,
    ) -> dict:
        """Genere des layouts Divi Builder. Retourne le dict JSON."""
        logger.info("Generation du layout Divi Builder avec Claude")
        system = """You are an expert Divi Builder developer and French copywriter.
Generate page layouts using Divi Builder shortcodes. Return ONLY valid JSON — no markdown:
{
  "theme_name": "Divi",
  "theme_slug": "Divi",
  "tagline": "string",
  "customizer": {
    "accent_color": "#hex",
    "header_bg": "#hex",
    "header_text_color": "#hex",
    "header_link_color": "#hex",
    "primary_nav_font": "Montserrat",
    "body_font": "Open Sans",
    "header_height": "80",
    "header_style": "left",
    "use_sticky_header": "on",
    "use_scrollup": "on",
    "logo_width": "150"
  },
  "footer": {
    "bg_color": "#hex", "text_color": "#hex", "link_color": "#hex",
    "col1": "Logo + tagline + description",
    "col2": "Liens navigation",
    "col3": "Adresse + tel + email + horaires",
    "bottom_bar": "copyright...",
    "divi_footer_shortcode": "[et_pb_section]...[/et_pb_section]"
  },
  "pages": [
    {
      "title": "Accueil",
      "slug": "accueil",
      "is_front_page": true,
      "divi_content": "[et_pb_section fb_built=\\"1\\" ...]...[/et_pb_section]",
      "yoast_title": "string",
      "yoast_description": "string"
    }
  ]
}
DIVI SHORTCODE RULES:
1. Always use _builder_version="4.24.0" on every module
2. Structure: et_pb_section > et_pb_row > et_pb_column > modules
3. Column types: "4_4" (full), "1_2" (half), "1_3" (third)
4. Apply colors via background_color, button_bg_color, text_color attributes
5. Homepage sections: hero (full-width, colored bg), services/products, CTA
6. Contact page: et_pb_contact_form with et_pb_contact_field
7. All text content in natural, human-written French — no AI cliches
8. Content specific to the business
9. FOOTER — 3 columns: logo+tagline, nav links, contact info+hours
10. SCHEMAS JSON-LD (SEO) in et_pb_code at bottom of homepage
""" + HUMANIZER_RULES

        full_prompt = (
            f"{archive_context}\n\n{user_prompt}"
            if archive_context else user_prompt
        )
        if image_urls:
            img_block = (
                "\n\n═══ IMAGES GENEREES (integre ces URLs) ═══\n"
            )
            for img_id, url in image_urls.items():
                if url:
                    img_block += f"  {img_id}: {url}\n"
            img_block += (
                "Utilise background_image=\"URL\" sur et_pb_section.\n"
                "═══════════════════════════════════════════════════\n"
            )
            full_prompt += img_block
        if logo_url:
            full_prompt += (
                f"\n\nLOGO DU SITE : {logo_url}\n"
                "Integre ce logo dans le customizer Divi.\n"
            )
        if eat_context:
            full_prompt += eat_context

        raw = self._call(system, full_prompt, max_tokens=8000)
        data = json.loads(raw)
        logger.info(
            "Layouts Divi generes (%d pages)", len(data.get("pages", [])),
        )
        return data

    def _call(self, system: str, user_prompt: str,
              max_tokens: int = 8000) -> str:
        """Appel Claude avec retry (3 tentatives) et nettoyage JSON."""
        for attempt in range(1, 4):
            try:
                r = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                )
            except Exception as e:
                logger.error("Erreur Claude (tentative %d/3) : %s",
                             attempt, e)
                if attempt == 3:
                    raise
                time.sleep(2)
                continue

            raw = r.content[0].text.strip()
            # Nettoyer les balises markdown eventuelles
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            # Extraire le JSON si precede de texte parasite
            m = re.search(r"(\{[\s\S]+\})", raw)
            if m:
                raw = m.group(1)
            try:
                json.loads(raw)
                return raw
            except json.JSONDecodeError as e:
                logger.warning(
                    "Tentative %d/3 — JSON invalide : %s", attempt, e,
                )
                if attempt == 3:
                    logger.error("Debut de la reponse : %s", raw[:400])
                    raise
        return raw  # jamais atteint


# ═════════════════════════════════════════════════════════════════
#  IMAGE GENERATOR — Flux via Replicate
# ═════════════════════════════════════════════════════════════════

class ImageGenerator:
    """
    Genere des visuels professionnels avec Flux (Replicate)
    et les uploade via FTP.
    """

    MODELS = {
        "schnell": "black-forest-labs/flux-schnell",
        "dev": "black-forest-labs/flux-dev",
        "pro": "black-forest-labs/flux-1.1-pro",
    }
    COSTS = {"schnell": 0.003, "dev": 0.025, "pro": 0.04}
    STEPS = {"schnell": 4, "dev": 28, "pro": 28}
    API = "https://api.replicate.com/v1"

    def __init__(self, api_key: str, model: str = "schnell"):
        self.model_name = model
        self.model_id = self.MODELS.get(model, self.MODELS["schnell"])
        self.cost_per = self.COSTS.get(model, 0.003)
        self.steps = self.STEPS.get(model, 4)
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }

    def build_prompts(self, claude_client: anthropic.Anthropic,
                      site_prompt: str, n: int = 5) -> list[dict]:
        """Claude cree des prompts Flux adaptes au site."""
        system = """You are an expert Flux image prompt writer for professional websites.
Return ONLY a valid JSON array — no markdown, no explanation:
[
  {"id": "hero", "filename": "hero.webp", "width": 1920, "height": 1080, "prompt": "..."},
  {"id": "section_1", "filename": "section-1.webp", "width": 1200, "height": 800, "prompt": "..."}
]
Rules:
- Prompts MUST be in English (better Flux quality)
- Photorealistic professional photography style
- Specific to the business type, sector and color palette requested
- NO text, NO logos, NO watermarks in images
- Use: "professional photography", "8k resolution", "natural lighting"
- hero: wide cinematic shot (1920x1080)
- section images: product shots, ambiance, team, services (1200x800)"""

        try:
            r = claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Business description: {site_prompt}\n"
                        f"Generate {n} images."
                    ),
                }],
            )
            raw = r.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            m = re.search(r"(\[[\s\S]+\])", raw)
            prompts = json.loads(m.group(1) if m else raw)
            logger.info("%d prompts images generes par Claude", len(prompts))
            return prompts
        except Exception as e:
            logger.error("Erreur generation prompts images : %s", e)
            raise

    def _generate_one(self, prompt: str, width: int,
                      height: int) -> bytes:
        url = f"{self.API}/models/{self.model_id}/predictions"
        payload = {
            "input": {
                "prompt": prompt,
                "width": width,
                "height": height,
                "num_outputs": 1,
                "output_format": "webp",
                "output_quality": 90,
                "num_inference_steps": self.steps,
            },
        }
        r = httpx.post(
            url, headers=self.headers, json=payload, timeout=180,
        )
        if not r.is_success:
            raise ConnectionError(
                f"Replicate ({r.status_code}): {r.text[:200]}"
            )

        data = r.json()

        # Polling si asynchrone
        if data.get("status") not in ("succeeded",):
            poll_url = data.get("urls", {}).get("get")
            if not poll_url:
                raise RuntimeError(
                    "Pas d'URL de polling dans la reponse Replicate"
                )
            for _ in range(90):
                time.sleep(2)
                pr = httpx.get(
                    poll_url, headers=self.headers, timeout=30,
                )
                data = pr.json()
                if data.get("status") == "succeeded":
                    break
                if data.get("status") == "failed":
                    raise RuntimeError(
                        f"Flux echoue : {data.get('error', 'unknown')}"
                    )

        output = data.get("output", [])
        img_url = output[0] if isinstance(output, list) else output
        if not img_url:
            raise ValueError("Flux n'a retourne aucune image")

        resp = httpx.get(img_url, timeout=60)
        resp.raise_for_status()
        return resp.content

    def run(self, claude_client: anthropic.Anthropic, site_prompt: str,
            n: int, ftp_cfg: dict, domain: str) -> dict[str, str]:
        """
        Pipeline complet : prompts -> generation -> FTP -> URLs.
        Retourne {image_id: url_publique}.
        """
        now = datetime.now()
        ym = f"{now.year}/{now.month:02d}"
        remote = f"{ftp_cfg['root']}/wp-content/uploads/{ym}"

        logger.info("Generation d'images avec Flux [%s]", self.model_name)
        specs = self.build_prompts(claude_client, site_prompt, n)

        # Creer les dossiers uploads FTP
        try:
            conn = _ftp_connect(ftp_cfg)
            for sub in [
                "wp-content/uploads",
                f"wp-content/uploads/{now.year}",
                f"wp-content/uploads/{ym}",
            ]:
                _ftp_mkd_safe(conn, f"{ftp_cfg['root']}/{sub}")
            conn.quit()
        except Exception as e:
            logger.error("Erreur creation dossier FTP uploads : %s", e)
            raise

        urls: dict[str, str] = {}
        total_cost = 0.0

        for i, spec in enumerate(specs):
            name = spec.get("filename", f"image-{i + 1}.webp")
            logger.info("[%d/%d] %s", i + 1, len(specs), name)
            try:
                img = self._generate_one(
                    spec["prompt"], spec["width"], spec["height"],
                )
                conn = _ftp_connect(ftp_cfg)
                conn.storbinary(f"STOR {remote}/{name}", io.BytesIO(img))
                conn.quit()
                pub = f"{domain}/wp-content/uploads/{ym}/{name}"
                urls[spec["id"]] = pub
                total_cost += self.cost_per
                logger.info(
                    "  %s : %d Ko -> %s", name, len(img) // 1024, pub,
                )
            except Exception as e:
                logger.warning("  %s : erreur — %s", name, e)
                urls[spec["id"]] = ""

        ok_count = sum(1 for v in urls.values() if v)
        logger.info(
            "Images : %d/%d generees, cout : $%.3f",
            ok_count, len(specs), total_cost,
        )
        return urls


# ═════════════════════════════════════════════════════════════════
#  LOGO GENERATOR — Ideogram v2 via Replicate
# ═════════════════════════════════════════════════════════════════

class LogoGenerator:
    """
    Genere un logo professionnel avec Ideogram v2 (Replicate).
    """

    MODEL = "ideogram-ai/ideogram-v2"
    API = "https://api.replicate.com/v1"

    def __init__(self, api_key: str, style: str = "DESIGN"):
        self.style = style
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }

    def build_prompt(self, claude_client: anthropic.Anthropic,
                     site_prompt: str) -> dict:
        """Claude cree un prompt Ideogram optimise pour le logo."""
        system = """You are an expert logo designer writing prompts for Ideogram v2.
Ideogram excels at rendering text inside images — always include the exact business name.
Return ONLY valid JSON:
{
  "business_name": "exact name to appear in the logo",
  "prompt": "detailed Ideogram prompt in English",
  "negative_prompt": "what to avoid"
}
Prompt rules:
- Always start with: "Logo for [BusinessName],"
- Specify text to render: "with text \\"BusinessName\\""
- Style: flat design, vector style, clean, professional, minimalist
- Colors: match the site palette (hex values)
- White or transparent background
- No gradients, no shadows
- High contrast, readable at small sizes"""

        try:
            r = claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Site description:\n{site_prompt}\n\n"
                        "Generate the logo prompt."
                    ),
                }],
            )
            raw = r.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            logger.info("Prompt logo : %s", data["prompt"][:80])
            return data
        except Exception as e:
            logger.error("Erreur generation prompt logo : %s", e)
            raise

    def _generate(self, prompt: str, negative_prompt: str = "") -> bytes:
        url = f"{self.API}/models/{self.MODEL}/predictions"
        payload = {
            "input": {
                "prompt": prompt,
                "negative_prompt": (
                    negative_prompt
                    or "blurry, low quality, distorted text, watermark"
                ),
                "style_type": self.style,
                "aspect_ratio": "ASPECT_1_1",
                "magic_prompt_option": "OFF",
                "rendering_speed": "QUALITY",
            },
        }
        r = httpx.post(url, headers=self.headers, json=payload, timeout=180)
        if not r.is_success:
            raise ConnectionError(
                f"Ideogram API ({r.status_code}): {r.text[:200]}"
            )

        data = r.json()

        # Polling si asynchrone
        if data.get("status") not in ("succeeded",):
            poll_url = data.get("urls", {}).get("get")
            if not poll_url:
                raise RuntimeError(
                    "Pas d'URL de polling dans la reponse Replicate"
                )
            for _ in range(60):
                time.sleep(2)
                pr = httpx.get(
                    poll_url, headers=self.headers, timeout=30,
                )
                data = pr.json()
                if data.get("status") == "succeeded":
                    break
                if data.get("status") == "failed":
                    raise RuntimeError(
                        f"Ideogram echoue : {data.get('error')}"
                    )

        output = data.get("output", {})
        if isinstance(output, dict):
            img_url = output.get("images", [{}])[0].get("url", "")
        elif isinstance(output, list):
            img_url = output[0] if output else ""
        else:
            img_url = str(output)

        if not img_url:
            raise ValueError("Ideogram n'a retourne aucune image")

        resp = httpx.get(img_url, timeout=60)
        resp.raise_for_status()
        return resp.content

    def run(self, claude_client: anthropic.Anthropic, site_prompt: str,
            ftp_cfg: dict, domain: str) -> str:
        """
        Genere le logo et l'uploade via FTP.
        Retourne l'URL publique du logo.
        """
        now = datetime.now()
        ym = f"{now.year}/{now.month:02d}"
        remote = f"{ftp_cfg['root']}/wp-content/uploads/{ym}"

        logger.info("Generation du logo avec Ideogram v2 [%s]", self.style)
        spec = self.build_prompt(claude_client, site_prompt)

        logger.info("Rendu en cours...")
        img = self._generate(spec["prompt"], spec.get("negative_prompt", ""))
        fname = "logo.png"

        conn = _ftp_connect(ftp_cfg)
        for sub in [
            "wp-content/uploads",
            f"wp-content/uploads/{now.year}",
            f"wp-content/uploads/{ym}",
        ]:
            _ftp_mkd_safe(conn, f"{ftp_cfg['root']}/{sub}")
        conn.storbinary(f"STOR {remote}/{fname}", io.BytesIO(img))
        conn.quit()

        logo_url = f"{domain}/wp-content/uploads/{ym}/{fname}"
        logger.info("Logo genere : %d Ko -> %s", len(img) // 1024, logo_url)
        return logo_url


# ═════════════════════════════════════════════════════════════════
#  DIVI INSTALLER
# ═════════════════════════════════════════════════════════════════

class DiviInstaller:
    """Installe le theme Divi depuis l'API Elegant Themes."""

    ET_API_URL = "https://api.elegantthemes.com/api/api_downloads.php"

    def __init__(self, ftp_host: str, ftp_user: str, ftp_pass: str,
                 ftp_root: str, et_username: str, et_api_key: str):
        self.ftp = dict(
            host=ftp_host, user=ftp_user, pwd=ftp_pass, root=ftp_root,
        )
        self.et_username = et_username
        self.et_api_key = et_api_key

    def _download_latest(self) -> bytes:
        logger.info("Connexion a l'API Elegant Themes")
        try:
            r = httpx.get(self.ET_API_URL, params={
                "api_key": self.et_api_key,
                "username": self.et_username,
                "theme_name": "Divi",
            }, timeout=60, follow_redirects=True)

            content_type = r.headers.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    err = r.json()
                except Exception:
                    err = {}
                raise PermissionError(
                    f"API ET : {err.get('error', r.text[:200])}\n"
                    "Verifiez ET_USERNAME et ET_API_KEY."
                )
            if r.status_code != 200:
                raise ConnectionError(f"HTTP {r.status_code}")

            data = r.content
            if data[:2] != b"PK":
                raise ValueError(f"Reponse non-ZIP : {data[:60]}")

            logger.info("Divi telecharge : %.1f Mo", len(data) / 1024 / 1024)
            return data

        except PermissionError:
            raise

        except Exception as e:
            raise RuntimeError(
                f"API Elegant Themes inaccessible : {e}"
            ) from e

    def install(self) -> dict:
        """Installe Divi. Retourne {"files_uploaded": int}."""
        zip_data = self._download_latest()

        logger.info("Extraction de Divi")
        dest = Path("/tmp/divi_theme")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        zipfile.ZipFile(io.BytesIO(zip_data)).extractall(dest)

        divi_dirs = [
            p for p in dest.iterdir()
            if p.is_dir() and "divi" in p.name.lower()
        ]
        if not divi_dirs:
            raise FileNotFoundError(
                "Dossier 'Divi' introuvable dans l'archive."
            )
        divi_local = divi_dirs[0]

        logger.info("Upload de Divi via FTP")
        conn = _ftp_connect(self.ftp)
        remote = f"{self.ftp['root']}/wp-content/themes/Divi"
        n = _ftp_upload_dir(conn, divi_local, remote, label="Divi ")
        conn.quit()

        # Cleanup
        shutil.rmtree(dest, ignore_errors=True)

        logger.info("Divi installe (%d fichiers)", n)
        return {"files_uploaded": n}


# ═════════════════════════════════════════════════════════════════
#  WP DEPLOYER (ex WPManager)
# ═════════════════════════════════════════════════════════════════

class WPDeployer:
    """
    Deploie un theme et du contenu sur un WordPress existant via l'API REST.
    Login via cookies HTTP, upload FTP, API REST pour les pages/meta.
    """

    def __init__(self, domain: str, admin_user: str, admin_pass: str,
                 ftp_host: str, ftp_user: str, ftp_pass: str,
                 ftp_root: str):
        self.domain = domain.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.ftp = dict(
            host=ftp_host, user=ftp_user, pwd=ftp_pass, root=ftp_root,
        )
        self._client: httpx.Client | None = None
        self.nonce: str | None = None

    def login(self) -> dict:
        """
        Login WP admin via HTTP. Retourne {"logged_in": bool, "nonce": str|None}.
        """
        self._client = httpx.Client(
            follow_redirects=True, timeout=30,
        )
        self._client.get(f"{self.domain}/wp-login.php")
        self._client.post(
            f"{self.domain}/wp-login.php",
            data={
                "log": self.admin_user,
                "pwd": self.admin_pass,
                "wp-submit": "Log In",
                "redirect_to": "/wp-admin/",
                "testcookie": "1",
            },
            cookies={"wordpress_test_cookie": "WP Cookie check"},
        )
        r2 = self._client.get(f"{self.domain}/wp-admin/")

        # Chercher le nonce
        for pattern in [
            r'"_wpnonce"\s*:\s*"([^"]+)"',
            r'"nonce"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, r2.text)
            if m:
                self.nonce = m.group(1)
                break

        if not self.nonce:
            try:
                r3 = self._client.post(
                    f"{self.domain}/wp-admin/admin-ajax.php",
                    data={"action": "rest-nonce"},
                )
                if r3.is_success and r3.text.strip():
                    self.nonce = r3.text.strip()
            except Exception:
                pass

        logged_in = (
            "/wp-admin/" in r2.url.path
            or "dashboard" in r2.text.lower()
        )
        if not logged_in:
            logger.warning(
                "Connexion WP possiblement echouee — verifiez les identifiants"
            )
        if self.nonce:
            logger.info("Connecte a WordPress admin (nonce: %s...)",
                        self.nonce[:8])
        else:
            logger.warning("Nonce introuvable — l'API REST pourra echouer")

        return {"logged_in": logged_in, "nonce": self.nonce}

    def _h(self) -> dict:
        return {
            "X-WP-Nonce": self.nonce or "",
            "Content-Type": "application/json",
        }

    def _api(self, method: str, path: str, **kw) -> httpx.Response:
        if not self._client:
            raise RuntimeError("Appelez login() d'abord")
        return self._client.request(
            method,
            f"{self.domain}/wp-json/wp/v2/{path}",
            headers=self._h(),
            **kw,
        )

    def upload_theme(self, theme_data: dict) -> dict:
        """Upload un theme custom via FTP. Retourne {"files": list}."""
        slug = theme_data["theme_slug"]
        conn = _ftp_connect(self.ftp)
        theme_dir = f"{self.ftp['root']}/wp-content/themes/{slug}"
        _ftp_mkd_safe(conn, theme_dir)
        uploaded_files = []
        for fname, content in theme_data["files"].items():
            conn.storbinary(
                f"STOR {theme_dir}/{fname}",
                io.BytesIO(content.encode("utf-8")),
            )
            uploaded_files.append(fname)
            logger.info("  Theme fichier uploade : %s", fname)
        conn.quit()
        logger.info("Theme '%s' uploade", slug)
        return {"files": uploaded_files}

    def activate_theme(self, theme_slug: str) -> dict:
        """Active un theme. Retourne {"activated": bool, "method": str}."""
        r = self._api("POST", f"themes/{theme_slug}",
                       json={"status": "active"})
        if r.is_success:
            logger.info("Theme '%s' active", theme_slug)
            return {"activated": True, "method": "themes_api"}

        r2 = self._api("POST", "settings",
                        json={"template": theme_slug,
                              "stylesheet": theme_slug})
        if r2.is_success:
            logger.info("Theme '%s' active (settings)", theme_slug)
            return {"activated": True, "method": "settings"}

        logger.warning(
            "Activation theme echouee (%d) — activez manuellement",
            r.status_code,
        )
        return {"activated": False, "method": "failed"}

    def apply_divi_customizer(self, customizer: dict,
                              logo_url: str = "") -> dict:
        """Applique les reglages Divi via un script PHP temporaire."""

        def _esc(v: str) -> str:
            return v.replace("'", "\\'").replace('"', '\\"')

        accent = _esc(customizer.get("accent_color", "#2EA3F2"))
        hdr_bg = _esc(customizer.get("header_bg", accent))
        hdr_tc = _esc(customizer.get("header_text_color", "#ffffff"))
        hdr_lc = _esc(customizer.get("header_link_color", "#ffffff"))
        nav_fnt = _esc(customizer.get("primary_nav_font", "Montserrat"))
        body_f = _esc(customizer.get("body_font", "Open Sans"))
        hdr_h = _esc(customizer.get("header_height", "80"))
        hdr_st = _esc(customizer.get("header_style", "left"))
        sticky = _esc(customizer.get("use_sticky_header", "on"))
        logo_w = _esc(customizer.get("logo_width", "150"))

        php = f"""<?php
require_once(dirname(dirname(__FILE__)).'/wp-load.php');
$opts = get_option('et_divi', array());
$opts['accent_color']          = '{accent}';
$opts['primary_nav_bg']        = '{hdr_bg}';
$opts['primary_nav_text_color']= '{hdr_tc}';
$opts['primary_nav_link_color']= '{hdr_lc}';
$opts['primary_nav_font']      = str_replace(' ', '+', '{nav_fnt}');
$opts['body_font']             = str_replace(' ', '+', '{body_f}');
$opts['header_height']         = '{hdr_h}';
$opts['header_style']          = '{hdr_st}';
$opts['use_sticky_header']     = '{sticky}';
$opts['use_scrollup']          = 'on';
$opts['logo_width']            = '{logo_w}';
$opts['all_buttons_bg_color']  = '{accent}';
$opts['all_buttons_text_size'] = '14';
$opts['footer_bg']             = get_option('_divi_footer_bg', '#1a1a1a');
update_option('et_divi', $opts);
$logo_url_php = '{logo_url}';
if ($logo_url_php) {{
    $logo_id = attachment_url_to_postid($logo_url_php);
    if ($logo_id) {{ set_theme_mod('custom_logo', $logo_id); }}
}}
echo json_encode(['status'=>'ok','accent'=>$opts['accent_color']]);
@unlink(__FILE__);
"""
        helper_remote = f"{self.ftp['root']}/wp-content/divi_setup_tmp.php"
        try:
            conn = _ftp_connect(self.ftp)
            conn.storbinary(
                f"STOR {helper_remote}",
                io.BytesIO(php.encode("utf-8")),
            )
            conn.quit()
            time.sleep(1)
            r = self._client.get(
                f"{self.domain}/wp-content/divi_setup_tmp.php", timeout=15,
            )
            if r.is_success and "ok" in r.text:
                logger.info("Divi customizer applique (accent: %s)", accent)
                return {"applied": True, "accent": accent}
        except Exception as e:
            logger.warning("Erreur application customizer Divi : %s", e)

        # Cleanup
        try:
            conn2 = _ftp_connect(self.ftp)
            conn2.delete(helper_remote)
            conn2.quit()
        except Exception:
            pass

        logger.warning(
            "Customizer Divi non applique — appliquez manuellement"
        )
        return {"applied": False, "accent": accent}

    def create_page(self, title: str, slug: str, content: str,
                    status: str = "publish") -> int | None:
        """Cree une page WP. Retourne le page ID ou None."""
        try:
            r = self._api("POST", "pages", json={
                "title": title, "slug": slug,
                "content": content, "status": status,
            })
            if not r.is_success:
                logger.error(
                    "Erreur creation page '%s' (%d) : %s",
                    title, r.status_code, r.text[:150],
                )
                return None
            pid = r.json().get("id")
            logger.info("Page '%s' creee (ID %s)", title, pid)
            return pid
        except Exception as e:
            logger.error("Exception creation page '%s' : %s", title, e)
            return None

    def set_front_page(self, front_id: int) -> bool:
        """Definit la page d'accueil statique."""
        try:
            r = self._api("POST", "settings", json={
                "show_on_front": "page", "page_on_front": front_id,
            })
            if r.is_success:
                logger.info("Page d'accueil statique (ID %d)", front_id)
                return True
        except Exception as e:
            logger.warning("Erreur set front page : %s", e)
        return False

    def install_yoast(self) -> dict:
        """Telecharge et installe Yoast SEO. Retourne un resume."""
        logger.info("Installation de Yoast SEO")
        try:
            r_yoast = httpx.get(
                "https://downloads.wordpress.org/plugin/"
                "wordpress-seo.latest-stable.zip",
                follow_redirects=True, timeout=120,
            )
            r_yoast.raise_for_status()
            data = r_yoast.content
        except Exception as e:
            logger.warning("Telechargement Yoast echoue : %s", e)
            return {"installed": False, "error": str(e)}

        dest = Path("/tmp/yoast")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(dest)

        conn = _ftp_connect(self.ftp)
        remote = f"{self.ftp['root']}/wp-content/plugins/wordpress-seo"
        n = _ftp_upload_dir(conn, dest / "wordpress-seo", remote,
                            label="Yoast ")
        conn.quit()

        shutil.rmtree(dest, ignore_errors=True)

        time.sleep(2)
        r = self._api(
            "POST", "plugins/wordpress-seo%2Fwp-seo.php",
            json={"status": "active"},
        )
        activated = r.is_success
        logger.info(
            "Yoast SEO installe (%d fichiers) — %s",
            n, "active" if activated else "a activer manuellement",
        )
        return {"installed": True, "files": n, "activated": activated}

    def set_yoast_meta(self, page_id: int | None, title: str,
                       desc: str) -> bool:
        if not page_id:
            return False
        try:
            r = self._api("POST", f"pages/{page_id}", json={
                "meta": {
                    "_yoast_wpseo_title": title,
                    "_yoast_wpseo_metadesc": desc,
                },
            })
            if r.is_success:
                logger.info("Yoast meta (ID %d)", page_id)
                return True
            logger.warning("Yoast meta echoue (ID %d) : %d",
                           page_id, r.status_code)
        except Exception as e:
            logger.warning("Yoast meta exception (ID %d) : %s", page_id, e)
        return False

    def create_nav_menu(self, pages_map: dict) -> bool:
        """Cree le menu de navigation via WP REST API."""
        try:
            r_create = self._api(
                "POST", "menus",
                json={"name": "Menu principal", "slug": "main-menu"},
            )
            if not r_create.is_success:
                raise ValueError(
                    f"POST /menus -> {r_create.status_code}"
                )
            menu_id = r_create.json().get("id")
            if not menu_id:
                raise ValueError("Pas de menu ID dans la reponse")
            for title, pid in pages_map.items():
                if pid:
                    self._api("POST", "menu-items", json={
                        "title": title, "object_id": pid,
                        "object": "page", "type": "post_type",
                        "menus": menu_id, "status": "publish",
                    })
            logger.info("Menu de navigation cree")
            return True
        except Exception as e:
            logger.info(
                "Menu REST non disponible (%s) — creez-le manuellement", e,
            )
            return False

    def _deploy_divi_footer(self, footer: dict):
        """Deploie le footer Divi via l'API WordPress."""
        try:
            self._api("POST", "settings", json={
                "et_divi[footer_bg]": footer.get("bg_color", "#1a1a1a"),
                "et_divi[footer_text]": footer.get("text_color", "#ffffff"),
                "et_divi[footer_link]": footer.get("link_color", "#cccccc"),
            })

            html = footer.get("divi_footer_shortcode", "")
            self._api("POST", "widgets", json={
                "id_base": "text", "sidebar": "footer-1",
                "instance": {"title": "", "text": html, "filter": True},
            })
            logger.info("Footer Divi deploye (%d chars)", len(html))
        except Exception as e:
            logger.warning("Footer Divi non deploye : %s", e)

    def deploy_custom(self, theme_data: dict) -> dict:
        """Deploie un theme custom complet. Retourne les pages creees."""
        self.login()
        self.upload_theme(theme_data)
        self.activate_theme(theme_data["theme_slug"])
        self.install_yoast()

        pages_map: dict[str, int | None] = {}
        front_id = None
        for p in theme_data["pages"]:
            pid = self.create_page(p["title"], p["slug"], p["content"])
            pages_map[p["title"]] = pid
            if p.get("is_front_page"):
                front_id = pid
            self.set_yoast_meta(
                pid,
                p.get("yoast_title", ""),
                p.get("yoast_description", ""),
            )
        if front_id:
            self.set_front_page(front_id)
        self.create_nav_menu(pages_map)
        return {"pages": pages_map, "front_page_id": front_id}

    def deploy_divi(self, theme_data: dict) -> dict:
        """Deploie du contenu Divi Builder. Retourne les pages creees."""
        self.login()
        self.activate_theme("Divi")
        if "customizer" in theme_data:
            self.apply_divi_customizer(
                theme_data["customizer"],
                logo_url=theme_data.get("logo_url", ""),
            )
        self.install_yoast()

        pages_map: dict[str, int | None] = {}
        front_id = None
        for p in theme_data["pages"]:
            content = p.get("divi_content", p.get("content", ""))
            pid = self.create_page(p["title"], p["slug"], content)
            pages_map[p["title"]] = pid
            if p.get("is_front_page"):
                front_id = pid
            self.set_yoast_meta(
                pid,
                p.get("yoast_title", ""),
                p.get("yoast_description", ""),
            )
        if front_id:
            self.set_front_page(front_id)
        self.create_nav_menu(pages_map)

        footer = theme_data.get("footer", {})
        if footer.get("divi_footer_shortcode"):
            self._deploy_divi_footer(footer)

        return {"pages": pages_map, "front_page_id": front_id}

    def close(self):
        """Ferme le client HTTP."""
        if self._client:
            self._client.close()
            self._client = None


# ═════════════════════════════════════════════════════════════════
#  FULL PIPELINE — Orchestrateur
# ═════════════════════════════════════════════════════════════════

class WPGeneratorPipeline:
    """
    Orchestre le pipeline complet de generation WordPress.

    config dict attendu :
    {
        "domain": "https://example.com",
        "ftp_host": "...", "ftp_user": "...", "ftp_pass": "...", "ftp_root": "/www",
        "db_host": "...", "db_name": "...", "db_user": "...", "db_pass": "...",
        "admin_user": "admin", "admin_pass": "...",
        "site_prompt": "Description du site...",
        "mode": "divi" | "custom",

        # Optionnels
        "reset": false,
        "install_wp": true,
        "install_divi": false,
        "et_username": "", "et_api_key": "",
        "webarchive_url": "",
        "competitor_urls": [],
        "eat": { "enabled": false, "name": "", "address": "", ... },
        "generate_images": false, "image_count": 5, "image_model": "schnell",
        "generate_logo": false,
        "wp_username": "admin",
    }
    """

    def __init__(self):
        self.settings = get_settings()

    def _log(self, msg: str, callback: Callable | None = None):
        logger.info(msg)
        if callback:
            try:
                callback(msg)
            except Exception:
                pass

    def run(self, config: dict,
            log_callback: Callable[[str], None] | None = None) -> dict:
        """
        Execute le pipeline complet.
        Retourne un dict avec le resultat de chaque etape.
        """
        result: dict[str, Any] = {
            "steps_completed": [],
            "errors": [],
        }

        domain = config["domain"].rstrip("/")
        ftp_cfg = dict(
            host=config["ftp_host"], user=config["ftp_user"],
            pwd=config["ftp_pass"], root=config["ftp_root"],
        )
        mode = config.get("mode", "custom")

        # Claude client
        claude_client = anthropic.Anthropic(
            api_key=self.settings.anthropic_api_key,
        )

        # ── Step 0: Reset (optionnel) ────────────────────────────
        if config.get("reset"):
            self._log("Etape 0 : Reset du site", log_callback)
            try:
                resetter = SiteResetter(
                    ftp_host=config["ftp_host"],
                    ftp_user=config["ftp_user"],
                    ftp_pass=config["ftp_pass"],
                    ftp_root=config["ftp_root"],
                    db_host=config["db_host"],
                    db_name=config["db_name"],
                    db_user=config["db_user"],
                    db_pass=config["db_pass"],
                    domain=domain,
                )
                reset_result = resetter.reset()
                result["reset"] = reset_result
                result["steps_completed"].append("reset")
            except Exception as e:
                logger.error("Erreur reset : %s", e)
                result["errors"].append({"step": "reset", "error": str(e)})

        # ── Step 1: Install WordPress (optionnel) ────────────────
        if config.get("install_wp", True):
            self._log("Etape 1 : Installation de WordPress", log_callback)
            try:
                from app.models.site import Site
                site = Site(
                    domain=domain.replace("https://", "").replace("http://", ""),
                    name=config.get("site_name", "Mon Site"),
                    ftp_host=config["ftp_host"],
                    ftp_user=config["ftp_user"],
                    ftp_password=config["ftp_pass"],
                    ftp_path=config["ftp_root"],
                    db_host=config["db_host"],
                    db_name=config["db_name"],
                    db_user=config["db_user"],
                    db_password=config["db_pass"],
                    wp_username=config.get("wp_username", "admin"),
                )
                from app.services.wp_installer import WPInstaller
                installer = WPInstaller(
                    site, log_callback=lambda m: self._log(m, log_callback),
                )
                wp_result = installer.install_wordpress()
                result["wordpress"] = wp_result
                result["steps_completed"].append("install_wp")
            except Exception as e:
                logger.error("Erreur installation WP : %s", e)
                result["errors"].append(
                    {"step": "install_wp", "error": str(e)},
                )

        # ── Step 2: Install Divi (optionnel) ─────────────────────
        if config.get("install_divi") and mode == "divi":
            self._log("Etape 2 : Installation de Divi", log_callback)
            try:
                divi = DiviInstaller(
                    ftp_host=config["ftp_host"],
                    ftp_user=config["ftp_user"],
                    ftp_pass=config["ftp_pass"],
                    ftp_root=config["ftp_root"],
                    et_username=config.get("et_username", ""),
                    et_api_key=config.get("et_api_key", ""),
                )
                divi_result = divi.install()
                result["divi"] = divi_result
                result["steps_completed"].append("install_divi")
            except Exception as e:
                logger.error("Erreur installation Divi : %s", e)
                result["errors"].append(
                    {"step": "install_divi", "error": str(e)},
                )

        # ── Step 3: Web Archive (optionnel) ──────────────────────
        archive_context: str | None = None
        if config.get("webarchive_url") or config.get("scrape_archive", False):
            self._log("Etape 3 : Scraping Wayback Machine", log_callback)
            try:
                scraper = WebArchiveScraper(
                    domain=domain,
                    custom_url=config.get("webarchive_url", ""),
                )
                archive_result = scraper.get_context()
                archive_context = archive_result.get("prompt_context")
                result["web_archive"] = archive_result
                result["steps_completed"].append("web_archive")
            except Exception as e:
                logger.error("Erreur scraping archive : %s", e)
                result["errors"].append(
                    {"step": "web_archive", "error": str(e)},
                )

        # ── Step 4: Competitor analysis (optionnel) ──────────────
        competitor_brief: str | None = None
        if config.get("competitor_urls"):
            self._log("Etape 4 : Analyse des concurrents", log_callback)
            try:
                analyzer = CompetitorAnalyzer(claude_client)
                comp_result = analyzer.analyze(config["competitor_urls"])
                competitor_brief = comp_result.get("brief")
                result["competitors"] = comp_result
                result["steps_completed"].append("competitors")
            except Exception as e:
                logger.error("Erreur analyse concurrents : %s", e)
                result["errors"].append(
                    {"step": "competitors", "error": str(e)},
                )

        # ── Step 5: EAT context (optionnel) ──────────────────────
        eat_context: str | None = None
        eat_cfg = config.get("eat", {})
        if eat_cfg.get("enabled"):
            self._log("Etape 5 : Construction contexte E-A-T", log_callback)
            try:
                eat_gen = EATGenerator(eat_cfg)
                eat_result = eat_gen.build_prompt_context()
                eat_context = eat_result.get("prompt_context")
                result["eat"] = eat_result
                result["steps_completed"].append("eat")
            except Exception as e:
                logger.error("Erreur EAT : %s", e)
                result["errors"].append({"step": "eat", "error": str(e)})

        # ── Step 6a: Generate logo (optionnel) ───────────────────
        logo_url: str | None = None
        if config.get("generate_logo"):
            self._log("Etape 6a : Generation du logo", log_callback)
            try:
                logo_gen = LogoGenerator(
                    api_key=self.settings.replicate_api_token,
                )
                site_prompt = competitor_brief or config.get("site_prompt", "")
                logo_url = logo_gen.run(
                    claude_client, site_prompt, ftp_cfg, domain,
                )
                result["logo_url"] = logo_url
                result["steps_completed"].append("logo")
            except Exception as e:
                logger.error("Erreur generation logo : %s", e)
                result["errors"].append({"step": "logo", "error": str(e)})

        # ── Step 6b: Generate images (optionnel) ─────────────────
        image_urls: dict[str, str] | None = None
        if config.get("generate_images"):
            self._log("Etape 6b : Generation des images", log_callback)
            try:
                img_gen = ImageGenerator(
                    api_key=self.settings.replicate_api_token,
                    model=config.get("image_model", "schnell"),
                )
                site_prompt = competitor_brief or config.get("site_prompt", "")
                image_urls = img_gen.run(
                    claude_client, site_prompt,
                    n=config.get("image_count", 5),
                    ftp_cfg=ftp_cfg, domain=domain,
                )
                result["image_urls"] = image_urls
                result["steps_completed"].append("images")
            except Exception as e:
                logger.error("Erreur generation images : %s", e)
                result["errors"].append({"step": "images", "error": str(e)})

        # ── Step 7: Generate theme/content via Claude ────────────
        self._log("Etape 7 : Generation du theme via Claude", log_callback)
        theme_data: dict | None = None
        try:
            generator = ThemeGenerator(claude_client)
            site_prompt = competitor_brief or config.get("site_prompt", "")

            if mode == "divi":
                theme_data = generator.generate_divi(
                    user_prompt=site_prompt,
                    archive_context=archive_context,
                    image_urls=image_urls,
                    logo_url=logo_url,
                    eat_context=eat_context,
                )
            else:
                theme_data = generator.generate_custom(
                    user_prompt=site_prompt,
                    archive_context=archive_context,
                    image_urls=image_urls,
                    logo_url=logo_url,
                    eat_context=eat_context,
                )

            if logo_url and theme_data:
                theme_data["logo_url"] = logo_url

            result["theme"] = {
                "name": theme_data.get("theme_name", ""),
                "pages_count": len(theme_data.get("pages", [])),
                "mode": mode,
            }
            result["steps_completed"].append("generate_theme")
        except Exception as e:
            logger.error("Erreur generation theme : %s", e)
            result["errors"].append(
                {"step": "generate_theme", "error": str(e)},
            )

        # ── Step 8: Deploy to WordPress ──────────────────────────
        if theme_data:
            self._log("Etape 8 : Deploiement sur WordPress", log_callback)
            deployer: WPDeployer | None = None
            try:
                admin_user = config.get(
                    "admin_user",
                    result.get("wordpress", {}).get("username", "admin"),
                )
                admin_pass = config.get(
                    "admin_pass",
                    result.get("wordpress", {}).get("password", ""),
                )
                deployer = WPDeployer(
                    domain=domain,
                    admin_user=admin_user,
                    admin_pass=admin_pass,
                    ftp_host=config["ftp_host"],
                    ftp_user=config["ftp_user"],
                    ftp_pass=config["ftp_pass"],
                    ftp_root=config["ftp_root"],
                )

                if mode == "divi":
                    deploy_result = deployer.deploy_divi(theme_data)
                else:
                    deploy_result = deployer.deploy_custom(theme_data)

                result["deploy"] = deploy_result
                result["steps_completed"].append("deploy")
            except Exception as e:
                logger.error("Erreur deploiement : %s", e)
                result["errors"].append(
                    {"step": "deploy", "error": str(e)},
                )
            finally:
                if deployer:
                    deployer.close()

        # ── Resume final ─────────────────────────────────────────
        result["url"] = domain
        result["admin_url"] = f"{domain}/wp-admin/"
        result["username"] = result.get("wordpress", {}).get(
            "username", config.get("admin_user", "admin"),
        )
        result["password"] = result.get("wordpress", {}).get(
            "password", config.get("admin_pass", ""),
        )
        result["pages_created"] = list(
            result.get("deploy", {}).get("pages", {}).keys()
        )

        self._log(
            f"Pipeline termine : {len(result['steps_completed'])} etapes, "
            f"{len(result['errors'])} erreurs",
            log_callback,
        )
        return result
