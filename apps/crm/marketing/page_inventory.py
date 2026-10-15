"""Inventory the actual serving release, without network calls or DB writes."""
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time

from django.core.exceptions import ValidationError

from .runtime_contract import normalize_path


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ''
        self.in_title = False
        self.locale = ''
        self.forms = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'html':
            self.locale = attrs.get('lang', '') or ''
        elif tag == 'title':
            self.in_title = True
        elif tag == 'form':
            code = attrs.get('data-form-code', '') or ''
            if re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', code):
                self.forms.add(code)

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, text):
        if self.in_title:
            self.title += text


def inventory(root):
    # Resolve ``current`` once so an atomic deployment cannot mix releases.
    root = Path(root).resolve(strict=True)
    if not (root / 'index.html').is_file():
        raise ValueError('Published site root missing')
    pages = []
    for file in sorted(root.rglob('index.html')):
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            continue
        rel = file.relative_to(root)
        if any(part.startswith('.') or part in ('admin', 'api', 'node_modules') for part in rel.parts):
            continue
        if file.stat().st_size > 3_000_000:
            continue
        path = '/' if len(rel.parts) == 1 else '/' + rel.parent.as_posix() + '/'
        try:
            if normalize_path(path) != path:
                continue
        except ValidationError:
            continue
        content = file.read_bytes()
        page = Page()
        page.feed(content.decode('utf-8'))
        pages.append({'path': path, 'title': page.title.strip() or path,
            'locale': page.locale, 'formCodes': sorted(page.forms),
            'releaseHash': hashlib.sha256(content).hexdigest()})
        if len(pages) > 20_000:
            raise ValueError('Published catalogue is too large')
    return sorted(pages, key=lambda page: page['path'])


def refresh_catalog(root, output, site_code):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', site_code):
        raise ValueError('Invalid site code')
    pages = inventory(root)
    data = json.dumps({'siteCode': site_code, 'generatedAt': time.time(), 'pages': pages},
        ensure_ascii=False).encode('utf-8')
    if len(data) > 5_000_000:
        raise ValueError('Published catalogue is too large')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(pages)
