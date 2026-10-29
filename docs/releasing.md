# Releasing the Windows installer

The installer is built by GitHub Actions (`.github/workflows/release.yml`) on a Windows runner.

**To release:** push a version tag.

```
git tag v0.1.0
git push origin v0.1.0
```

The workflow then:

1. Writes the version into the app.
2. Installs Tesseract and Inno Setup.
3. Copies Tesseract into the app: the program, its libraries and licences, and the English and orientation-detection data.
4. Fetches EB Garamond (SIL OFL), the default substitute font.
5. Builds the program folder with PyInstaller (`packaging/ectype.spec`). The folder holds `Ectype OCR.exe` (the desktop app) and `ectype.exe` (the command line).
6. Smoke-tests the packaged command line on a synthetic page in both modes.
7. Builds `Ectype-OCR-<version>-Setup.exe` with Inno Setup (`packaging/installer.iss`) and attaches it to a GitHub release for the tag.

**Updates:** installed copies (0.3.0 on) find a new release by itself; nothing more is needed than the tag.
- **What the app needs from the release:** it reads the latest release (`/releases/latest`), so mark a release as a pre-release if it shouldn't be offered. It compares the tag with its own version and downloads the file named `Ectype-OCR-<version>-Setup.exe`, checking it against the SHA-256 digest GitHub publishes for each release file.
- **How the installer runs:** silently over the existing install (`/SILENT /UPDATE`, plus `/ALLUSERS` or `/CURRENTUSER` to match it). With `/UPDATE` the installer opens the new version when it's done (`packaging/installer.iss`). Tasks chosen at the first install (desktop icon, PATH) are kept.

**For a test build without a release:** run the workflow by hand (Actions → Windows installer → Run workflow). The installer is then under the run's artifacts.

**The installer:**
- Installs into Program Files (or for the current user only, if chosen), with a Start-menu entry and an optional desktop icon.
- Optionally adds `ectype` to the user's PATH.
- Uninstalls from Windows Settings like any other program.
- Keeps Qt's libraries as separate files in the program folder, as its LGPL licence expects.

**Building locally on Windows** (with Python 3.12, Tesseract installed, and Inno Setup 6):

```
pip install . PySide6-Essentials pyinstaller
python packaging/fetch_fonts.py packaging/build/fonts
set ECTYPE_TESSERACT_DIR=C:\Program Files\Tesseract-OCR
set ECTYPE_FONTS_DIR=packaging\build\fonts
pyinstaller packaging/ectype.spec --noconfirm --distpath dist --workpath build/pyi
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\installer.iss
```

Pointing `ECTYPE_TESSERACT_DIR` at the whole Tesseract folder also bundles every language installed there.

`.github/workflows/ci.yml` runs the test suite on Linux for every push and pull request.
