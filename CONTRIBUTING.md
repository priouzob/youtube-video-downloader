# Contributing

Thanks for contributing.

## Local setup

1. Install Python 3.12+
2. Install PyInstaller:

```powershell
python -m pip install pyinstaller
```

3. Build locally:

```powershell
python -m PyInstaller downloader_v2.spec
```

## Guidelines

- Keep changes focused and testable
- Never commit secrets
- Test executable startup before opening a PR

## Pull requests

Please include:
- problem summary
- implementation summary
- test steps and expected results
