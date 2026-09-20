import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from siteos_admin.runtime_config import secret_value
from siteos_admin.schema_install import Migration, load_chain, pending_migrations, transaction_body


class PackagingTests(SimpleTestCase):
    def test_request_parsing_and_in_memory_uploads_are_bounded(self):
        self.assertEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 2 * 1024 * 1024)
        self.assertEqual(settings.FILE_UPLOAD_MAX_MEMORY_SIZE, 1 * 1024 * 1024)
        self.assertEqual(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, 1000)
        self.assertEqual(settings.DATA_UPLOAD_MAX_NUMBER_FILES, 20)

    def test_entire_real_chain_is_verified(self):
        chain = load_chain(settings.BASE_DIR / 'db' / 'migrations')
        self.assertEqual(len(chain), 32)

    def test_new_install_and_contiguous_upgrade(self):
        chain = [Migration('0001_a.sql', 'a', ''), Migration('0002_b.sql', 'b', '')]
        self.assertEqual(pending_migrations(chain, {}), chain)
        self.assertEqual(pending_migrations(chain, {'0001_a.sql':'a'}), chain[1:])
        self.assertEqual(pending_migrations(chain, {'0001_a.sql':'a','0002_b.sql':'b'}), [])

    def test_legacy_gap_unknown_version_and_tampering_are_refused(self):
        chain = [Migration('0001_a.sql', 'a', ''), Migration('0002_b.sql', 'b', '')]
        for recorded in ({'0002_b.sql':'b'}, {'9999_bad.sql':'x'}, {'0001_a.sql':'wrong'}):
            with self.subTest(recorded=recorded), self.assertRaises(CommandError):
                pending_migrations(chain, recorded)

    def test_only_outer_transaction_is_removed(self):
        sql = 'BEGIN;\nDO $$ BEGIN NULL; END $$;\nCOMMIT;'
        self.assertEqual(transaction_body(sql), 'DO $$ BEGIN NULL; END $$;')
        with self.assertRaises(CommandError):
            transaction_body('BEGIN;\nSELECT 1;')

    def test_changed_sql_and_missing_manifest_entry_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'0001_a.sql').write_text('SELECT 1;', encoding='utf-8')
            (root/'SHA256SUMS').write_text('0'*64+'  0001_a.sql\n', encoding='utf-8')
            with self.assertRaises(CommandError):
                load_chain(root)
            checksum = hashlib.sha256((root/'0001_a.sql').read_bytes()).hexdigest()
            (root/'SHA256SUMS').write_text(checksum+'  0001_a.sql\n', encoding='utf-8')
            self.assertEqual(len(load_chain(root)), 1)
            (root/'0002_b.sql').write_text('SELECT 2;', encoding='utf-8')
            with self.assertRaises(CommandError):
                load_chain(root)

    def test_secret_file_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory)/'secret'
            path.write_text('synthetic-test-value\n', encoding='utf-8')
            os.environ['UNIT_SECRET_FILE'] = str(path)
            self.assertEqual(secret_value('UNIT_SECRET'), 'synthetic-test-value')
            os.environ['UNIT_SECRET'] = 'another-test-value'
            with self.assertRaises(ImproperlyConfigured):
                secret_value('UNIT_SECRET')

    def test_missing_secret_file_does_not_disclose_path(self):
        with patch.dict(os.environ, {'UNIT_SECRET_FILE':'/does-not-exist/private-location'}, clear=True):
            with self.assertRaises(ImproperlyConfigured) as error:
                secret_value('UNIT_SECRET')
            self.assertNotIn('private-location', str(error.exception))

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_measurement_disabled_without_database_access(self):
        from marketing.public_config import public_measurement_config
        config = public_measurement_config()
        self.assertFalse(config['meta_pixel_enabled'])
        self.assertEqual(config['meta_pixel_id'], '')
        self.assertFalse(config['google_tag_enabled'])

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_external_dispatch_disabled_before_records_are_read_or_changed(self):
        from leads.services import dispatch_outbox_event, dispatch_pending_outbox, dispatch_meta_outbox_event
        from leads.notifications import send_submission_notification, send_smtp_test_notification
        from leads.inbound import retry_inbound_events
        self.assertFalse(dispatch_outbox_event(None))
        self.assertFalse(dispatch_meta_outbox_event(None))
        self.assertEqual(dispatch_pending_outbox(), (0, 0))
        self.assertEqual(retry_inbound_events(), (0, 0))
        self.assertFalse(send_submission_notification(None))
        self.assertFalse(send_smtp_test_notification('synthetic@example.invalid')[0])
