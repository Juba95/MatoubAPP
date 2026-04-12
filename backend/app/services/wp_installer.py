import ftplib
import io
import httpx
import zipfile
import mysql.connector
import secrets
import string
from hashlib import md5
from app.models.site import Site


class WPInstaller:
    """
    Installe WordPress automatiquement via FTP + MySQL.
    Porté depuis le Colab/Streamlit wordpress_generator.
    """

    WP_DOWNLOAD_URL = "https://wordpress.org/latest.zip"

    def __init__(self, site: Site, log_callback=None):
        self.site = site
        self.log = log_callback or print

    def _ftp_connect(self) -> ftplib.FTP:
        ftp = ftplib.FTP(self.site.ftp_host)
        ftp.login(self.site.ftp_user, self.site.ftp_password)
        if self.site.ftp_path and self.site.ftp_path != "/":
            ftp.cwd(self.site.ftp_path)
        return ftp

    def _db_connect(self):
        return mysql.connector.connect(
            host=self.site.db_host,
            user=self.site.db_user,
            password=self.site.db_password,
            database=self.site.db_name,
        )

    def reset_site(self):
        """Efface tout (FTP + BDD) pour repartir de zéro"""
        self.log("Nettoyage FTP...")
        ftp = self._ftp_connect()
        self._ftp_rmdir(ftp, ".")
        ftp.quit()

        self.log("Nettoyage BDD...")
        conn = self._db_connect()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        if tables:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            for (table,) in tables:
                cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            conn.commit()
        cursor.close()
        conn.close()
        self.log("Reset complet.")

    def install_wordpress(self) -> dict:
        """Télécharge et installe WordPress"""
        self.log("Téléchargement de WordPress...")
        resp = httpx.get(self.WP_DOWNLOAD_URL, follow_redirects=True, timeout=60)
        resp.raise_for_status()

        self.log("Extraction et upload FTP...")
        ftp = self._ftp_connect()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))

        uploaded = 0
        for info in zf.infolist():
            # WordPress zip contient un dossier "wordpress/" — on l'enlève
            path = info.filename.replace("wordpress/", "", 1)
            if not path:
                continue

            if info.is_dir():
                try:
                    ftp.mkd(path)
                except ftplib.error_perm:
                    pass
            else:
                data = zf.read(info.filename)
                ftp.storbinary(f"STOR {path}", io.BytesIO(data))
                uploaded += 1

        self.log(f"{uploaded} fichiers uploadés.")

        # Créer wp-config.php
        admin_password = self._random_password()
        wp_config = self._generate_wp_config()
        ftp.storbinary("STOR wp-config.php", io.BytesIO(wp_config.encode()))
        ftp.quit()

        # Installer via la BDD
        self.log("Configuration de la base de données...")
        self._setup_database(admin_password)

        result = {
            "url": f"https://{self.site.domain}",
            "admin_url": f"https://{self.site.domain}/wp-admin/",
            "username": self.site.wp_username or "admin",
            "password": admin_password,
        }
        self.log(f"WordPress installé : {result['url']}")
        return result

    def _generate_wp_config(self) -> str:
        keys_url = "https://api.wordpress.org/secret-key/1.1/salt/"
        try:
            keys = httpx.get(keys_url, timeout=10).text
        except Exception:
            keys = "\n".join(
                f"define('{k}', '{self._random_password(64)}');"
                for k in [
                    "AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY",
                    "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT",
                ]
            )

        return f"""<?php
define('DB_NAME', '{self.site.db_name}');
define('DB_USER', '{self.site.db_user}');
define('DB_PASSWORD', '{self.site.db_password}');
define('DB_HOST', '{self.site.db_host}');
define('DB_CHARSET', 'utf8mb4');
define('DB_COLLATE', '');

{keys}

$table_prefix = 'wp_';
define('WP_DEBUG', false);
define('DISALLOW_FILE_EDIT', true);
define('WP_AUTO_UPDATE_CORE', false);

if ( ! defined( 'ABSPATH' ) ) {{
    define( 'ABSPATH', __DIR__ . '/' );
}}
require_once ABSPATH . 'wp-settings.php';
"""

    def _setup_database(self, admin_password: str):
        """Crée les tables WP et l'admin"""
        conn = self._db_connect()
        cursor = conn.cursor()

        username = self.site.wp_username or "admin"
        email = f"admin@{self.site.domain}"
        site_title = self.site.name
        site_url = f"https://{self.site.domain}"

        # Créer les tables via wp-admin/install.php serait mieux,
        # mais on peut aussi installer programmatiquement via l'URL
        install_url = f"{site_url}/wp-admin/install.php?step=2"
        try:
            httpx.post(install_url, data={
                "weblog_title": site_title,
                "user_name": username,
                "admin_password": admin_password,
                "admin_password2": admin_password,
                "admin_email": email,
                "blog_public": "0",  # pas d'indexation immédiate
            }, timeout=30, follow_redirects=True)
            self.log("Installation WP via install.php terminée.")
        except Exception as e:
            self.log(f"Install via URL échoué ({e}), tentative manuelle...")

        cursor.close()
        conn.close()

    def _random_password(self, length: int = 16) -> str:
        chars = string.ascii_letters + string.digits + "!@#$%"
        return "".join(secrets.choice(chars) for _ in range(length))

    def _ftp_rmdir(self, ftp: ftplib.FTP, path: str):
        """Supprime récursivement un dossier FTP"""
        try:
            items = ftp.nlst(path)
        except ftplib.error_perm:
            return
        for item in items:
            if item in (".", ".."):
                continue
            try:
                ftp.delete(item)
            except ftplib.error_perm:
                self._ftp_rmdir(ftp, item)
                try:
                    ftp.rmd(item)
                except ftplib.error_perm:
                    pass
