from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from console.storage_security import (
    PRIVATE_FILE_RESPONSE_POLICY,
    StorageSecurityError,
    configured_import_roots,
    configured_storage_roots,
    private_file_response_headers,
    resolve_import_source_path,
    resolve_storage_asset_path,
)


class StorageSecurityTests(SimpleTestCase):
    def setUp(self):
        # Keep fixtures inside the isolated candidate workspace.  Some managed
        # Windows test runners intentionally deny writes to the host temp tree.
        self.root_directory = TemporaryDirectory(dir=settings.BASE_DIR)
        self.outside_directory = TemporaryDirectory(dir=settings.BASE_DIR)
        self.root = Path(self.root_directory.name).resolve()
        self.outside = Path(self.outside_directory.name).resolve()
        self.asset = self.root / 'images' / '2026' / '08' / 'sample.png'
        self.asset.parent.mkdir(parents=True)
        self.asset.write_bytes(b'not-a-production-file')
        self.outside_file = self.outside / 'outside.png'
        self.outside_file.write_bytes(b'outside')

    def tearDown(self):
        self.root_directory.cleanup()
        self.outside_directory.cleanup()

    def test_storage_logical_path_resolves_inside_explicit_root(self):
        resolved = resolve_storage_asset_path(
            'storage/assets/images/2026/08/sample.png',
            roots=[self.root],
        )

        self.assertEqual(resolved, self.asset.resolve())

    def test_storage_root_relative_path_accepts_alternate_separator(self):
        resolved = resolve_storage_asset_path(
            r'images\2026\08\sample.png',
            roots=[self.root],
        )

        self.assertEqual(resolved, self.asset.resolve())

    def test_absolute_storage_and_import_paths_must_be_inside_their_allowlists(self):
        self.assertEqual(
            resolve_storage_asset_path(self.asset, roots=[self.root]),
            self.asset.resolve(),
        )
        self.assertEqual(
            resolve_import_source_path(self.asset, roots=[self.root]),
            self.asset.resolve(),
        )

        for resolver in (resolve_storage_asset_path, resolve_import_source_path):
            with self.subTest(resolver=resolver.__name__):
                with self.assertRaises(StorageSecurityError) as raised:
                    resolver(self.outside_file, roots=[self.root])
                self.assertEqual(raised.exception.code, 'outside_allowlist')

    def test_no_allowlist_fails_closed(self):
        for resolver in (resolve_storage_asset_path, resolve_import_source_path):
            with self.subTest(resolver=resolver.__name__):
                with self.assertRaises(StorageSecurityError) as raised:
                    resolver(self.asset, roots=[])
                self.assertEqual(raised.exception.code, 'allowlist_not_configured')

    @override_settings(SITEOS_ASSET_STORAGE_ROOTS=[], SITEOS_ASSET_IMPORT_ROOTS=[])
    def test_empty_settings_do_not_infer_project_or_host_roots(self):
        with self.assertRaises(StorageSecurityError) as storage_error:
            configured_storage_roots()
        with self.assertRaises(StorageSecurityError) as import_error:
            configured_import_roots()

        self.assertEqual(storage_error.exception.code, 'allowlist_not_configured')
        self.assertEqual(import_error.exception.code, 'allowlist_not_configured')

    def test_explicit_settings_are_validated_and_resolved(self):
        with override_settings(
            SITEOS_ASSET_STORAGE_ROOTS=[str(self.root)],
            SITEOS_ASSET_IMPORT_ROOTS=(self.outside,),
        ):
            self.assertEqual(configured_storage_roots(), (self.root,))
            self.assertEqual(configured_import_roots(), (self.outside,))

    def test_legacy_singular_storage_root_is_accepted_only_as_explicit_configuration(self):
        with patch.dict(os.environ, {'SITEOS_ASSET_STORAGE_ROOT': str(self.root)}, clear=False):
            self.assertEqual(configured_storage_roots(), (self.root,))

    def test_explicit_plural_storage_setting_takes_precedence_over_legacy_single_root(self):
        with override_settings(
            SITEOS_ASSET_STORAGE_ROOTS=[],
            SITEOS_ASSET_STORAGE_ROOT=str(self.root),
        ):
            with self.assertRaises(StorageSecurityError) as raised:
                configured_storage_roots()

        self.assertEqual(raised.exception.code, 'allowlist_not_configured')

    def test_relative_import_path_is_rejected(self):
        with self.assertRaises(StorageSecurityError) as raised:
            resolve_import_source_path('images/sample.png', roots=[self.root])

        self.assertEqual(raised.exception.code, 'absolute_path_required')

    def test_parent_references_are_rejected_with_both_separator_styles(self):
        candidates = (
            'storage/assets/../outside.png',
            r'storage\assets\..\outside.png',
            r'storage/assets\../outside.png',
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(StorageSecurityError) as raised:
                    resolve_storage_asset_path(candidate, roots=[self.root])
                self.assertEqual(raised.exception.code, 'parent_reference_not_allowed')

    def test_windows_stream_reserved_and_ambiguous_components_are_rejected(self):
        candidates = (
            r'images\sample.png:Zone.Identifier',
            r'images\CON.png',
            r'images\sample.png.',
            r'images\sample.png ',
        )
        expected_codes = (
            'alternate_stream_not_allowed',
            'reserved_windows_name',
            'ambiguous_windows_path',
            'invalid_path',
        )
        for candidate, expected_code in zip(candidates, expected_codes, strict=True):
            with self.subTest(candidate=candidate):
                with self.assertRaises(StorageSecurityError) as raised:
                    resolve_storage_asset_path(candidate, roots=[self.root])
                self.assertEqual(raised.exception.code, expected_code)

    def test_missing_paths_and_directories_are_not_returned(self):
        with self.assertRaises(StorageSecurityError) as missing:
            resolve_storage_asset_path('images/missing.png', roots=[self.root])
        with self.assertRaises(StorageSecurityError) as directory:
            resolve_storage_asset_path('images', roots=[self.root])

        self.assertEqual(missing.exception.code, 'file_unavailable')
        self.assertEqual(directory.exception.code, 'file_unavailable')

    def test_prefix_collision_does_not_count_as_containment(self):
        sibling = self.root.parent / f'{self.root.name}-collision'
        sibling.mkdir(exist_ok=True)
        sibling_file = sibling / 'collision.png'
        sibling_file.write_bytes(b'collision')
        self.addCleanup(sibling.rmdir)
        self.addCleanup(sibling_file.unlink, missing_ok=True)

        with self.assertRaises(StorageSecurityError) as raised:
            resolve_storage_asset_path(sibling_file, roots=[self.root])

        self.assertEqual(raised.exception.code, 'outside_allowlist')

    def test_exception_text_never_discloses_candidate_or_allowlist_paths(self):
        with self.assertRaises(StorageSecurityError) as raised:
            resolve_import_source_path(self.outside_file, roots=[self.root])

        rendered = f'{raised.exception!s} {raised.exception!r}'
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn(str(self.outside_file), rendered)
        self.assertEqual(str(raised.exception), '请求的文件不可用。')
        self.assertIsNone(raised.exception.__cause__)

    def test_symlink_or_reparse_point_below_root_is_rejected(self):
        link = self.root / 'images' / 'linked-outside.png'
        try:
            link.symlink_to(self.outside_file)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f'platform cannot create a file symlink/reparse point: {type(exc).__name__}')

        with self.assertRaises(StorageSecurityError) as raised:
            resolve_storage_asset_path('images/linked-outside.png', roots=[self.root])

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')

    def test_directory_symlink_or_reparse_point_escape_is_rejected(self):
        linked_directory = self.root / 'linked-directory'
        try:
            linked_directory.symlink_to(self.outside, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f'platform cannot create a directory symlink/reparse point: {type(exc).__name__}')

        with self.assertRaises(StorageSecurityError) as raised:
            resolve_storage_asset_path('linked-directory/outside.png', roots=[self.root])

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')

    def test_windows_paths_are_case_insensitive_when_the_host_is_windows(self):
        if os.name != 'nt':
            self.skipTest('Windows case-folding semantics require a Windows filesystem.')

        differently_cased = Path(str(self.asset).swapcase())
        resolved = resolve_storage_asset_path(differently_cased, roots=[self.root])

        self.assertEqual(os.path.normcase(resolved), os.path.normcase(self.asset.resolve()))

    def test_private_read_policy_requires_no_store_and_nosniff(self):
        headers = private_file_response_headers()

        self.assertEqual(
            headers,
            {
                'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff',
            },
        )
        self.assertEqual(PRIVATE_FILE_RESPONSE_POLICY.cache_control, 'no-store')
        self.assertEqual(PRIVATE_FILE_RESPONSE_POLICY.content_type_options, 'nosniff')
        headers['Cache-Control'] = 'public'
        self.assertEqual(private_file_response_headers()['Cache-Control'], 'no-store')
