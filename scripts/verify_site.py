"""Verify a complete HIPForm publication before deployment."""

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlsplit

from hipform.publishing import DOWNLOADS


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.local = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            target = dict(attrs).get("href", "")
            if target and not urlsplit(target).scheme:
                self.local.append(target)


def verify_site(site):
    manifest = json.loads((site / "publication.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "hipform-pages-v1":
        raise ValueError("Unknown publication format; export with hipform publish")
    names = manifest["files"]
    if set(names) != {"index.html", ".nojekyll", *DOWNLOADS}:
        raise ValueError("Publication must contain exactly the HIPForm export files")
    actual = {path.name for path in site.iterdir()}
    if actual != {*names, "publication.json"}:
        raise ValueError("Publication has unexpected or missing files")
    for name, digest in names.items():
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Publication filenames must be local basenames")
        path = site / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Invalid publication file: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Publication hash mismatch: {name}")
    result = json.loads((site / "result.json").read_text(encoding="utf-8"))
    if result.get("execution_status") != "completed":
        raise ValueError("Publication must contain a completed simulation")
    links = Links()
    links.feed((site / "index.html").read_text(encoding="utf-8"))
    if set(links.local) != set(DOWNLOADS):
        raise ValueError("Report must link to every HIPForm download and no other local files")
    print(f"Verified {len(names)} publication files and {len(links.local)} download links")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    verify_site(parser.parse_args().site)
