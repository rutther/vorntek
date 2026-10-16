"""Operator links must follow the configured standalone deployment origin."""
import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


class PublicOriginTests(SimpleTestCase):
    def preview_link(self, *, site_url=None, preview_url=None):
        environment = os.environ.copy()
        for name in ('NEWCROWN_SITE_URL', 'SITEOS_PREVIEW_BASE_URL'):
            environment.pop(name, None)
        if site_url is not None:
            environment['NEWCROWN_SITE_URL'] = site_url
        if preview_url is not None:
            environment['SITEOS_PREVIEW_BASE_URL'] = preview_url
        output = subprocess.check_output([
            sys.executable, '-c',
            'import django,json; django.setup(); '
            'from console.views import preview_site_url; '
            'print(json.dumps(preview_site_url("en")))',
        ], env=environment, text=True, timeout=20)
        return json.loads(output)

    def test_standalone_link_uses_configured_site_not_developer_port(self):
        self.assertEqual(
            self.preview_link(site_url='https://workspace.example.invalid/'),
            'https://workspace.example.invalid/?lang=en',
        )

    def test_explicit_separate_preview_origin_is_preserved(self):
        self.assertEqual(
            self.preview_link(site_url='https://workspace.example.invalid',
                              preview_url='https://preview.example.invalid/'),
            'https://preview.example.invalid/?lang=en',
        )

    def test_unconfigured_local_development_keeps_existing_preview(self):
        self.assertEqual(self.preview_link(), 'http://127.0.0.1:25000/?lang=en')
