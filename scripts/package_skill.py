"""Package only the portable Skill, license and provenance note for distribution."""
from pathlib import Path
import shutil
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "skills" / "product-page-audit"
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    output = destination / "product-page-audit-skill.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(source.rglob("*")):
            if file.is_file() and not file.is_symlink():
                archive.write(file, Path("product-page-audit") / file.relative_to(source))
        for name in ("LICENSE", "NOTICE.md"):
            archive.write(root / name, Path("product-page-audit") / name)
    shutil.copyfile(output, destination / "product-page-audit.skill")
    print(output)


if __name__ == "__main__":
    main()
