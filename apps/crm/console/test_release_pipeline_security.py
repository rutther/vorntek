from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase

from sitecore.models import MediaAsset, Release, ReleaseBuild, Site

from .release_pipeline import (
    PreviewBuildConfigurationError,
    PreviewBuildError,
    PreviewBuildInProgress,
    asset_destination,
    copy_managed_assets,
    prepare_snapshot_file_path,
    preview_release_key,
    resolve_asset_source,
    run_astro_build,
    run_preview_build,
    secure_snapshot_output_dir,
    snapshot_output_dir,
)
from marketing.management.commands.export_release_snapshot import atomic_write_json


class ReleasePipelinePathSecurityTests(SimpleTestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent / '.test-release-pipeline'
        self.test_root.mkdir(exist_ok=True)
        self.root = (self.test_root / secrets.token_hex(12)).resolve()
        self.root.mkdir()
        self.storage = self.root / 'private-assets'
        self.storage.mkdir()
        for site_code in ('alpha', 'beta'):
            (self.root / site_code / 'apps' / 'web').mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def configured(self):
        return self.settings(
            SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root),
            SITEOS_ASSET_STORAGE_ROOTS=(str(self.storage),),
        )

    def test_preview_workspace_is_required_and_error_does_not_disclose_host_path(self):
        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=''):
            with patch.dict(os.environ, {'SITEOS_PREVIEW_WORKSPACE_ROOT': ''}):
                with self.assertRaises(PreviewBuildConfigurationError) as raised:
                    snapshot_output_dir('alpha')

        self.assertEqual(str(raised.exception), '预览构建环境尚未安全配置。')
        self.assertNotIn(str(Path.cwd()), str(raised.exception))

    def test_each_site_has_a_distinct_snapshot_tree(self):
        with self.configured():
            alpha = snapshot_output_dir('alpha')
            beta = snapshot_output_dir('beta')

        self.assertNotEqual(alpha, beta)
        self.assertIn('alpha', alpha.parts)
        self.assertIn('beta', beta.parts)

    def test_asset_copy_requires_an_explicit_site_scope(self):
        with self.assertRaises(TypeError):
            copy_managed_assets()

    def test_explicit_snapshot_output_cannot_use_another_site_tree(self):
        beta_output = self.root / 'beta' / 'apps' / 'web' / 'release' / 'current'
        with self.configured():
            with self.assertRaises(PreviewBuildConfigurationError) as raised:
                secure_snapshot_output_dir(beta_output, site_code='alpha')

        self.assertEqual(raised.exception.code, 'snapshot_output_outside_site')
        self.assertNotIn(str(beta_output), str(raised.exception))

    def test_snapshot_writer_rejects_indirect_child_directory(self):
        output_dir = self.root / 'alpha' / 'apps' / 'web' / 'release' / 'current'
        output_dir.mkdir(parents=True)
        outside = self.root / 'beta' / 'escaped-snapshot'
        outside.mkdir()
        indirect = output_dir / 'articles'
        try:
            indirect.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('This environment does not permit directory symlinks/reparse points.')

        with self.configured():
            with self.assertRaises(PreviewBuildConfigurationError) as raised:
                atomic_write_json(
                    indirect / 'index.json',
                    {'articles': []},
                    output_dir=output_dir,
                    site_code='alpha',
                )

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')
        self.assertFalse((outside / 'index.json').exists())

    def test_snapshot_writer_uses_reparse_guard_for_every_nested_parent(self):
        output_dir = self.root / 'alpha' / 'apps' / 'web' / 'release' / 'current'
        articles_dir = output_dir / 'articles'
        articles_dir.mkdir(parents=True)

        def is_indirect(path: Path) -> bool:
            return path == articles_dir

        with self.configured():
            with patch('console.release_pipeline._is_link_or_reparse_point', side_effect=is_indirect):
                with self.assertRaises(PreviewBuildConfigurationError) as raised:
                    atomic_write_json(
                        articles_dir / 'index.json',
                        {'articles': []},
                        output_dir=output_dir,
                        site_code='alpha',
                    )

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')
        self.assertFalse((articles_dir / 'index.json').exists())

    def test_snapshot_file_path_cannot_escape_its_selected_output_root(self):
        output_dir = self.root / 'alpha' / 'apps' / 'web' / 'release' / 'current'
        output_dir.mkdir(parents=True)
        sibling = output_dir.parent / 'other' / 'manifest.json'

        with self.configured():
            with self.assertRaises(PreviewBuildConfigurationError) as raised:
                prepare_snapshot_file_path(
                    sibling,
                    output_dir=output_dir,
                    site_code='alpha',
                )

        self.assertEqual(raised.exception.code, 'snapshot_file_outside_output')

    def test_asset_source_must_be_an_existing_regular_file_in_the_storage_allowlist(self):
        source = self.storage / 'images' / 'safe.png'
        source.parent.mkdir()
        source.write_bytes(b'safe')
        asset = SimpleNamespace(storage_path='storage/assets/images/safe.png')

        with self.configured():
            self.assertEqual(resolve_asset_source(asset), source)

        outside = self.root / 'outside.png'
        outside.write_bytes(b'outside')
        asset.storage_path = str(outside)
        with self.configured():
            with self.assertRaises(Exception) as raised:
                resolve_asset_source(asset)
        self.assertNotIn(str(outside), str(raised.exception))

    def test_asset_destination_uses_real_containment_not_string_prefixes(self):
        site = SimpleNamespace(code='alpha')
        with self.configured():
            safe = asset_destination(SimpleNamespace(site=site, public_path='/assets/catalog/item.png'))
            self.assertTrue(str(safe).endswith(os.path.join('assets', 'catalog', 'item.png')))

            unsafe_values = (
                '/assets/../escape.png',
                '/assets-evil/escape.png',
                'assets\\..\\escape.png',
                'https://example.invalid/assets/item.png',
                '/assets/%2e%2e/escape.png',
                '//assets/catalog/item.png',
            )
            for unsafe in unsafe_values:
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(PreviewBuildError) as raised:
                        asset_destination(SimpleNamespace(site=site, public_path=unsafe))
                    self.assertNotIn(unsafe, str(raised.exception))

    def test_asset_destination_rejects_symlink_or_reparse_hops(self):
        public = self.root / 'alpha' / 'apps' / 'web' / 'public'
        outside = self.root / 'outside-assets'
        public.mkdir()
        outside.mkdir()
        try:
            (public / 'assets').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('This environment does not permit symlink creation.')

        with self.configured():
            with self.assertRaises(PreviewBuildConfigurationError) as raised:
                asset_destination(
                    SimpleNamespace(site=SimpleNamespace(code='alpha'), public_path='/assets/escape.png')
                )
        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')

    def test_asset_destination_fails_when_a_reparse_component_is_detected(self):
        public = self.root / 'alpha' / 'apps' / 'web' / 'public'
        (public / 'assets').mkdir(parents=True)

        def is_indirect(path: Path) -> bool:
            return path.name == 'assets'

        with self.configured():
            with patch('console.release_pipeline._is_link_or_reparse_point', side_effect=is_indirect):
                with self.assertRaises(PreviewBuildConfigurationError) as raised:
                    asset_destination(
                        SimpleNamespace(site=SimpleNamespace(code='alpha'), public_path='/assets/escape.png')
                    )
        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')

    def test_astro_entry_rejects_an_indirect_parent_outside_the_site_tree(self):
        web_dir = self.root / 'alpha' / 'apps' / 'web'
        (web_dir / 'package.json').write_text('{}', encoding='utf-8')
        outside_node_modules = self.root / 'outside-node-modules'
        outside_astro = outside_node_modules / 'astro' / 'bin' / 'astro.mjs'
        outside_astro.parent.mkdir(parents=True)
        outside_astro.write_text('// outside test stub', encoding='utf-8')
        try:
            (web_dir / 'node_modules').symlink_to(outside_node_modules, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('This environment does not permit directory symlinks/reparse points.')

        with self.configured():
            with patch('console.release_pipeline.subprocess.run') as runner:
                with self.assertRaises(PreviewBuildConfigurationError) as raised:
                    run_astro_build(site_code='alpha')

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')
        runner.assert_not_called()

    def test_astro_entry_checks_every_parent_for_reparse_points(self):
        web_dir = self.root / 'alpha' / 'apps' / 'web'
        (web_dir / 'package.json').write_text('{}', encoding='utf-8')
        astro = web_dir / 'node_modules' / 'astro' / 'bin' / 'astro.mjs'
        astro.parent.mkdir(parents=True)
        astro.write_text('// test stub', encoding='utf-8')
        indirect_parent = web_dir / 'node_modules'

        def is_indirect(path: Path) -> bool:
            return path == indirect_parent

        with self.configured():
            with (
                patch('console.release_pipeline._is_link_or_reparse_point', side_effect=is_indirect),
                patch('console.release_pipeline.subprocess.run') as runner,
            ):
                with self.assertRaises(PreviewBuildConfigurationError) as raised:
                    run_astro_build(site_code='alpha')

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')
        runner.assert_not_called()

    def test_preview_uses_the_local_astro_builder_not_an_arbitrary_deploy_command(self):
        web_dir = self.root / 'alpha' / 'apps' / 'web'
        (web_dir / 'package.json').write_text('{}', encoding='utf-8')
        astro = web_dir / 'node_modules' / 'astro' / 'bin' / 'astro.mjs'
        astro.parent.mkdir(parents=True)
        astro.write_text('// test stub', encoding='utf-8')
        node = self.root / 'trusted-runtime' / 'node.exe'
        node.parent.mkdir()
        node.write_bytes(b'test node stub')
        completed = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        inherited = {
            'NODE_OPTIONS': '--require=untrusted-hook.js',
            'PATH': 'host-path-must-not-be-inherited',
            'SITEOS_ADMIN_DATABASE_URL': 'postgresql://secret-user:secret-password@database.invalid/siteos',
            'SITEOS_ADMIN_SECRET_KEY': 'server-secret-key-must-not-leak',
            'SITEOS_ASSET_IMPORT_ROOTS': 'D:/private/import-root',
            'SITEOS_ASSET_STORAGE_ROOTS': 'D:/private/storage-root',
            'SITEOS_PREVIEW_WORKSPACE_ROOT': 'D:/private/preview-root',
            'UNRELATED_API_TOKEN': 'unrelated-token-must-not-leak',
        }

        with self.settings(
            SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root),
            SITEOS_WEB_BUILD_COMMAND='npm run deploy-production',
        ):
            with patch.dict(os.environ, inherited, clear=False):
                with (
                    patch('console.release_pipeline.shutil.which', return_value=str(node)),
                    patch('console.release_pipeline.subprocess.run', return_value=completed) as runner,
                ):
                    run_astro_build(site_code='alpha')

        command = runner.call_args.args[0]
        self.assertEqual(command[0], str(node))
        self.assertEqual(command[1:], [str(astro), 'build'])
        self.assertNotIn('deploy', ' '.join(command))
        self.assertEqual(runner.call_args.kwargs['cwd'], web_dir)
        process_environment = runner.call_args.kwargs['env']
        self.assertIsInstance(process_environment, dict)
        self.assertEqual(process_environment['PATH'], str(node.parent))
        self.assertEqual(process_environment['NODE_ENV'], 'production')
        self.assertEqual(process_environment['ASTRO_TELEMETRY_DISABLED'], '1')
        for key in inherited:
            if key == 'PATH':
                continue
            self.assertNotIn(key, process_environment)
        self.assertNotIn('host-path-must-not-be-inherited', process_environment.values())


class ReleasePipelineDatabaseSecurityTests(TestCase):
    unmanaged_models = (Site, MediaAsset, Release, ReleaseBuild)

    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in cls.unmanaged_models:
                    if model._meta.db_table not in existing:
                        editor.create_model(model)
                        cls.created_models.append(model)
                        existing.add(model._meta.db_table)
            super().setUpClass()
        except Exception:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.test_root = Path(__file__).resolve().parent / '.test-release-pipeline'
        self.test_root.mkdir(exist_ok=True)
        self.root = (self.test_root / secrets.token_hex(12)).resolve()
        self.root.mkdir()
        self.storage = self.root / 'private-assets'
        self.storage.mkdir()
        for site_code in ('alpha', 'beta'):
            (self.root / site_code / 'apps' / 'web').mkdir(parents=True)
        self.alpha = self._site('alpha')
        self.beta = self._site('beta')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _site(self, code: str) -> Site:
        return Site.objects.create(
            code=code,
            name=code.title(),
            base_url=f'https://{code}.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )

    def _asset(
        self,
        *,
        name: str,
        content: bytes,
        create_source: bool = True,
        stored_digest: str | None = None,
    ) -> tuple[MediaAsset, Path]:
        source = self.storage / 'images' / name
        if create_source:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(content)
        digest = stored_digest or hashlib.sha256(content).hexdigest()
        asset = MediaAsset.objects.create(
            site=self.alpha,
            asset_type='image',
            original_name=name,
            title=name,
            mime_type='image/png',
            file_ext='.png',
            file_size_bytes=len(content),
            sha256=digest,
            storage_path=f'storage/assets/images/{name}',
            public_path=f'/assets/images/{name}',
            status='active',
        )
        return asset, source

    def _run(self, site_code: str = 'alpha'):
        process = subprocess.CompletedProcess(['npm', 'run', 'build'], 0, stdout='built', stderr='')
        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root)):
            with (
                patch('console.release_pipeline.call_command') as export,
                patch(
                    'console.release_pipeline.copy_managed_assets',
                    return_value={'copied': 0, 'skipped': 0, 'missingAssetIds': []},
                ),
                patch('console.release_pipeline.run_astro_build', return_value=process),
            ):
                result = run_preview_build(created_by='tester', site_code=site_code)
        return result, export

    def test_running_builds_are_deduplicated_by_site_not_globally(self):
        beta_release = Release.objects.create(
            site=self.beta,
            release_key='manual:beta',
            status='draft',
            snapshot_manifest={},
        )
        beta_build = ReleaseBuild.objects.create(
            release=beta_release,
            build_key='running-beta',
            status='running',
        )

        result, export = self._run('alpha')

        self.assertTrue(result.succeeded)
        self.assertEqual(result.release.site_id, self.alpha.id)
        self.assertEqual(result.build.release.site_id, self.alpha.id)
        beta_build.refresh_from_db()
        self.assertEqual(beta_build.status, 'running')
        self.assertIn('alpha', Path(export.call_args.kwargs['output_dir']).parts)
        self.assertNotIn('beta', Path(export.call_args.kwargs['output_dir']).parts)

    def test_second_running_build_for_same_site_is_rejected_before_creation(self):
        release = Release.objects.create(
            site=self.alpha,
            release_key='manual:alpha',
            status='draft',
            snapshot_manifest={},
        )
        ReleaseBuild.objects.create(release=release, build_key='running-alpha', status='running')

        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root)):
            with self.assertRaises(PreviewBuildInProgress):
                run_preview_build(created_by='tester', site_code='alpha')

        self.assertEqual(ReleaseBuild.objects.filter(release__site=self.alpha).count(), 1)

    def test_preview_release_keys_are_site_scoped_and_cannot_move_existing_rows(self):
        self.assertNotEqual(preview_release_key('alpha'), preview_release_key('beta'))
        conflicting = Release.objects.create(
            site=self.beta,
            release_key=preview_release_key('alpha'),
            status='draft',
            snapshot_manifest={},
        )

        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root)):
            with self.assertRaises(PreviewBuildError) as raised:
                run_preview_build(created_by='tester', site_code='alpha')

        conflicting.refresh_from_db()
        self.assertEqual(conflicting.site_id, self.beta.id)
        self.assertEqual(raised.exception.code, 'release_key_owned_by_other_site')

    def test_persisted_logs_and_artifacts_do_not_disclose_workspace_paths(self):
        database_secret = 'postgresql://secret-user:secret-password@database.invalid/siteos'
        application_secret = 'server-secret-key-must-not-leak'
        process = subprocess.CompletedProcess(
            ['npm', 'run', 'build'],
            0,
            stdout=(
                f'generated {self.root / "alpha" / "apps" / "web" / "dist"}\n'
                f'database={database_secret}\nsecret={application_secret}'
            ),
            stderr='',
        )
        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root)):
            with patch.dict(
                os.environ,
                {
                    'SITEOS_ADMIN_DATABASE_URL': database_secret,
                    'SITEOS_ADMIN_SECRET_KEY': application_secret,
                },
                clear=False,
            ):
                with (
                    patch('console.release_pipeline.call_command'),
                    patch(
                        'console.release_pipeline.copy_managed_assets',
                        return_value={'copied': 0, 'skipped': 0, 'missingAssetIds': []},
                    ),
                    patch('console.release_pipeline.run_astro_build', return_value=process),
                ):
                    result = run_preview_build(created_by='tester', site_code='alpha')

        result.build.refresh_from_db()
        self.assertNotIn(str(self.root), result.log_excerpt)
        self.assertNotIn(str(self.root), result.build.log_excerpt)
        self.assertNotIn(database_secret, result.log_excerpt)
        self.assertNotIn(application_secret, result.log_excerpt)
        self.assertNotIn(database_secret, result.build.log_excerpt)
        self.assertNotIn(application_secret, result.build.log_excerpt)
        self.assertIn('[redacted]', result.build.log_excerpt)
        self.assertEqual(result.build.artifact_path, 'preview://alpha/dist')
        self.assertNotIn(str(self.root), str(result.build.config_json))

    def test_unexpected_export_errors_are_recorded_without_exception_paths(self):
        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root)):
            with patch(
                'console.release_pipeline.call_command',
                side_effect=RuntimeError(f'failed at {self.root / "alpha" / "secret"}'),
            ):
                result = run_preview_build(created_by='tester', site_code='alpha')

        self.assertFalse(result.succeeded)
        self.assertNotIn(str(self.root), result.log_excerpt)
        self.assertIn('RuntimeError: preview step failed', result.log_excerpt)

    def test_missing_active_asset_fails_before_copy_or_astro_and_marks_build_failed(self):
        valid_asset, _source = self._asset(name='a-valid.png', content=b'valid')
        self._asset(name='z-missing.png', content=b'missing', create_source=False)

        with self.settings(
            SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root),
            SITEOS_ASSET_STORAGE_ROOTS=(str(self.storage),),
        ):
            valid_destination = asset_destination(valid_asset)
            with (
                patch('console.release_pipeline.call_command'),
                patch('console.release_pipeline.run_astro_build') as astro,
            ):
                result = run_preview_build(created_by='tester', site_code='alpha')

        self.assertFalse(result.succeeded)
        result.build.refresh_from_db()
        result.release.refresh_from_db()
        self.assertEqual(result.build.status, 'failed')
        self.assertEqual(result.release.status, 'failed')
        self.assertFalse(valid_destination.exists())
        astro.assert_not_called()

    def test_source_checksum_mismatch_fails_before_astro_and_marks_build_failed(self):
        self._asset(
            name='mismatch.png',
            content=b'actual-content',
            stored_digest=hashlib.sha256(b'expected-content').hexdigest(),
        )

        with self.settings(
            SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root),
            SITEOS_ASSET_STORAGE_ROOTS=(str(self.storage),),
        ):
            with (
                patch('console.release_pipeline.call_command'),
                patch('console.release_pipeline.run_astro_build') as astro,
            ):
                result = run_preview_build(created_by='tester', site_code='alpha')

        self.assertFalse(result.succeeded)
        result.build.refresh_from_db()
        self.assertEqual(result.build.status, 'failed')
        self.assertIn('asset_checksum_mismatch', result.build.log_excerpt)
        astro.assert_not_called()

    def test_same_size_stale_destination_is_atomically_overwritten_then_hash_match_skips(self):
        asset, _source = self._asset(name='same-size.png', content=b'fresh-value')
        stale = b'stale-value'
        self.assertEqual(len(stale), len(b'fresh-value'))

        with self.settings(
            SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root),
            SITEOS_ASSET_STORAGE_ROOTS=(str(self.storage),),
        ):
            destination = asset_destination(asset)
            destination.parent.mkdir(parents=True)
            destination.write_bytes(stale)

            first = copy_managed_assets(site_code='alpha')
            second = copy_managed_assets(site_code='alpha')

        self.assertEqual(destination.read_bytes(), b'fresh-value')
        self.assertEqual(first['copied'], 1)
        self.assertEqual(first['skipped'], 0)
        self.assertEqual(second['copied'], 0)
        self.assertEqual(second['skipped'], 1)
