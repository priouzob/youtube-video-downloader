# Publication GitHub + Auto-update (app + yt-dlp + ffmpeg)

## Ce qui est en place
- Auto-update de `downloader_v2.exe` via GitHub Releases
- Auto-update de `yt-dlp.exe` (verification quotidienne)
- Auto-install de `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` si absents
- Controle d'espace disque libre avant gros telechargements
- Workflow GitHub Actions de build/release sur tag `v*.*.*`

## Fichiers de config
- `update_config.json` -> auto-update de l'application (GitHub)
- `runtime_config.json` -> comportement runtime (ffmpeg auto install + espace mini)
- `version.txt` -> version locale de l'app

## Important: limite GitHub
- GitHub bloque les fichiers > 100 MiB dans un repo Git classique.
- Donc ne commit pas `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe`.
- Ton app sait maintenant les recuperer automatiquement au premier lancement.

## Configuration minimale avant publication
Dans `update_config.json`:

```json
{
  "enabled": true,
  "owner": "TON_OWNER_GITHUB",
  "repo": "youtubedownloader",
  "asset_name": "downloader_v2.exe",
  "auto_apply": true,
  "check_interval": "daily"
}
```

## Publier une version
1. Commit/push du repo
2. Creer un tag (ex: `v1.0.0`)
3. Push du tag

Le workflow construit l'exe et publie la release avec:
- `downloader_v2.exe`
- `version.txt`
- `update_config.json`

## Ce que l'utilisateur final doit avoir
Minimum:
- `downloader_v2.exe`
- `version.txt`
- `update_config.json`
- `runtime_config.json`
- dossier `video/`

Optionnel:
- `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` (sinon auto-download au lancement)
