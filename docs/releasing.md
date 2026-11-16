# Releasing the Windows installer

The installer is built by GitHub Actions (`.github/workflows/release.yml`) on a Windows runner.

**To release:** push a version tag.

Beaux pushes the release tags; agents never do.

```
git tag vX.Y.Z
git push origin vX.Y.Z
```

The workflow then:

1. Checks out the repository.
2. Sets up Python 3.12.
3. Gets the version from the release tag (or assigns a development version to a test build) and writes it into the app.
4. Installs Tesseract and Inno Setup with Chocolatey.
5. Updates pip and installs the app's Python packages, PySide6 Essentials and PyInstaller.
6. Bundles Tesseract's program, libraries, licences, English and orientation-detection data, and output configurations, then checks that its tools can start and, where needed, supplies compatible training tools and their libraries from MSYS2.
7. Fetches EB Garamond (SIL OFL), the default substitute font.
8. Builds the program folder with PyInstaller (`packaging/ectype.spec`), including `Ectype OCR.exe` (the desktop app) and `ectype.exe` (the command line).
9. Smoke-tests the packaged command line on a synthetic page, checking traced, substitute, text-layer and IDML output, exercising short Greek and Hebrew training, and checking the bundled training tools.
10. Builds `Ectype-OCR-<version>-Setup.exe` with Inno Setup (`packaging/installer.iss`).
11. Uploads the installer as a workflow artifact.
12. Attaches the installer to a GitHub release for the tag and generates release notes.

The Kraken engine job:

1. Checks out the repository, sets up Python 3.12, and gets its version from the release tag or the `engine_for_release` workflow input.
2. Installs CPU-only PyTorch, Kraken and PyInstaller, then builds the standalone engine folder with `packaging/ectype-kraken.spec`.
3. Smoke-tests the engine's version command, reading with a model from the repository, and training a few lines.
4. Zips the engine folder as `Ectype-OCR-Kraken-<version>-win64.zip`, uploads it as a workflow artifact, and attaches it to the release tag (or the existing tag named in `engine_for_release`).

A tag-triggered release offers two files: the Windows installer and the Kraken engine ZIP. The app downloads the engine itself when the user asks to add Kraken through Languages → Add languages → Kraken models.

**Updates:** installed copies (0.3.0 on) find a new release by itself; nothing more is needed than the tag.
- **What the app needs from the release:** it reads the latest release (`/releases/latest`), so mark a release as a pre-release if it shouldn't be offered. It compares the tag with its own version and downloads the file named `Ectype-OCR-<version>-Setup.exe`, checking it against the SHA-256 digest GitHub publishes for each release file.
- **How the installer runs:** silently over the existing install (`/SILENT /UPDATE`, `/DIR=` its folder, and `/ALLUSERS` or `/CURRENTUSER` as Windows lists the install; 0.4.0 on). Installs on another drive stay there. With `/UPDATE` the installer opens the new version when it's done (`packaging/installer.iss`). Tasks chosen at the first install (desktop icon, PATH) are kept.

**For a test build without a release:** run the workflow by hand (Actions → Windows installer → Run workflow). The installer and Kraken engine ZIP are then under the run's artifacts.

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
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=X.Y.Z packaging\installer.iss
```

Pointing `ECTYPE_TESSERACT_DIR` at the whole Tesseract folder also bundles every language installed there.

`.github/workflows/ci.yml` runs the test suite on Linux for every push and pull request.
