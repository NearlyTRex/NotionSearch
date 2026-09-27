<!-- GitHub shows the release title above these notes, so they start at level 2. -->
<!-- markdownlint-disable-file MD041 -->
## Installing on Windows

1. Download `NotionSearch-{version}-Setup.exe` below.
2. Run it. Windows SmartScreen warns that the publisher is unknown, because the installer is not
   code-signed. Choose **More info**, then **Run anyway**.
3. Launch **NotionSearch** from the Start Menu.

You also need [Docker Desktop](https://www.docker.com/products/docker-desktop/). If it's
missing, the shortcut says so, and `scripts\install-windows.ps1` installs it for you.

## Installing on Linux or macOS

```bash
git clone --branch {tag} https://github.com/{repository}.git
cd NotionSearch/docker
docker compose up -d
```

Then open <http://localhost:8080>.

## Verifying your download

Compare against `SHA256SUMS.txt`:

```powershell
Get-FileHash NotionSearch-{version}-Setup.exe -Algorithm SHA256
```
