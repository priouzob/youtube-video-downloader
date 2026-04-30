# YouTube Downloader v2

Downloader Windows portable base sur `yt-dlp` avec:
- auto-update de l'application via GitHub Releases
- auto-update de `yt-dlp.exe`
- auto-install de `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` si absents
- sortie video dans le dossier `video/`

## Utilisation (utilisateur final)

1. Telecharge la derniere release (`downloader_v2.exe` + fichiers de config).
2. Mets les fichiers dans un dossier local (ex: `C:\YoutubeDownloader`).
3. Lance `downloader_v2.exe`.
4. Colle un lien YouTube quand le programme le demande.

Le programme cree le dossier `video/` si besoin.
Si `ffmpeg` n'est pas present, il est telecharge automatiquement.

## Fichiers minimum a avoir a cote du .exe

- `downloader_v2.exe`
- `version.txt`
- `update_config.json`
- `runtime_config.json`

Optionnel:
- `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe`
  - sinon auto-download au premier lancement

## Auto-update: comment ca marche

### 1) Update de l'application
Au lancement, l'app lit `update_config.json`:
- appelle l'API GitHub Releases (latest)
- compare la version locale (`version.txt`) et le dernier tag
- si nouvelle version: telecharge le nouvel exe, remplace, redemarre

### 2) Update de yt-dlp
Au lancement (max 1 fois/jour):
- verification de la derniere version `yt-dlp.exe`
- telechargement automatique si necessaire

## Configuration

### `update_config.json`
Exemple:

```json
{
  "enabled": true,
  "owner": "priouzob",
  "repo": "youtubedownloader",
  "asset_name": "downloader_v2.exe",
  "auto_apply": true,
  "check_interval": "daily"
}
```

### `runtime_config.json`
Exemple:

```json
{
  "ffmpeg_auto_install": true,
  "ffmpeg_bundle_url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
  "min_free_space_mb": 500
}
```

## Release maintainer

1. Commit/push des changements sur `master`.
2. Cree un tag semver (ex: `v1.0.1`).
3. Push du tag.
4. Le workflow GitHub Actions build l'exe et publie la release.

Workflow: `.github/workflows/release.yml`

## Limite importante GitHub

Les fichiers >100 MiB ne peuvent pas etre pushes dans le repo Git classique.
Donc `ffmpeg.exe`, `ffplay.exe`, `ffprobe.exe` ne sont pas versionnes ici.
L'app les recupere automatiquement.

## Securite

- Voir `SECURITY.md` pour signaler une faille.
- Ne jamais partager de token/secret dans les issues.

## Roadmap courte

- checksum SHA256 des assets release
- signature code Windows
- logs optionnels (mode debug)
