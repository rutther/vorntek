"""Full-SQL file/database failure recovery; all bytes and sites are synthetic."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import DataError, OperationalError, close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.forms import ASSET_DIR_BY_TYPE, _managed_asset_target, asset_filename, store_local_asset, store_uploaded_asset
from sitecore.models import MediaAsset, Site, SiteLocale
from console.access import GROUP_BY_ROLE,ROLE_SYSTEM_ADMIN


@skipUnless(connection.vendor == 'postgresql', 'Requires committed full-SQL asset recovery')
class AssetWriteRecoveryTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Each test owns a unique synthetic site in the disposable SQL database.
        return None

    def setUp(self):
        self.directory=TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)/'assets'; self.root.mkdir()
        self.imports=Path(self.directory.name)/'imports'; self.imports.mkdir()
        self.site=Site.objects.create(code='asset_'+uuid4().hex[:16],name='Synthetic asset recovery',default_locale='en')
        self.settings_guard=override_settings(SITEOS_ASSET_STORAGE_ROOTS=[str(self.root)],SITEOS_ASSET_IMPORT_ROOTS=[str(self.imports)])
        self.settings_guard.enable(); self.addCleanup(self.settings_guard.disable)
        self.bytes=b'Synthetic asset bytes, not customer content'
        self.source=self.imports/'synthetic.png'; self.source.write_bytes(self.bytes)

    def store(self,mode):
        args=dict(site=self.site,title='Synthetic',alt_text='',caption='',created_by='synthetic-test')
        if mode=='upload':
            return store_uploaded_asset(uploaded_file=SimpleUploadedFile('synthetic.png',self.bytes,content_type='text/html'),**args)
        return store_local_asset(source_path=self.source,**args)

    def fail_actual_sql(self,*args,**kwargs):
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1/0')

    def test_upload_database_failure_does_not_leave_untracked_final_file(self):
        with patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql):
            with self.assertRaises(DataError): self.store('upload')
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(),0)
        self.assertEqual(list(self.root.rglob('*.png')),[])

    def test_import_database_failure_does_not_leave_untracked_final_file(self):
        with patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql):
            with self.assertRaises(DataError): self.store('import')
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(),0)
        self.assertEqual(list(self.root.rglob('*.png')),[])
        self.assertEqual(self.source.read_bytes(),self.bytes)

    def legacy_collision(self):
        now=timezone.now()
        path=_managed_asset_target(directory_name=ASSET_DIR_BY_TYPE['image'],year=f'{now:%Y}',month=f'{now:%m}',
            filename=asset_filename('synthetic.png',hashlib.sha256(self.bytes).hexdigest(),'.png'))
        path.write_bytes(b'Existing independent bytes must not be overwritten')
        return path

    def test_failed_upload_never_replaces_or_deletes_preexisting_path(self):
        old=self.legacy_collision()
        with patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql):
            with self.assertRaises(DataError): self.store('upload')
        self.assertEqual(old.read_bytes(),b'Existing independent bytes must not be overwritten')

    def test_failed_import_never_replaces_or_deletes_preexisting_path(self):
        old=self.legacy_collision()
        with patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql):
            with self.assertRaises(DataError): self.store('import')
        self.assertEqual(old.read_bytes(),b'Existing independent bytes must not be overwritten')

    def recovery(self,**kwargs):
        from console.asset_upload_recovery import reconcile_pending_assets
        return reconcile_pending_assets(root=self.root,site_id=self.site.pk,**kwargs)

    def rollback_upload(self):
        with self.assertRaisesRegex(RuntimeError,'Synthetic outer rollback'):
            with transaction.atomic():
                row=self.store('upload')
                path=self.root/Path(row.storage_path).relative_to('storage/assets')
                raise RuntimeError('Synthetic outer rollback')
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(),0)
        return path

    def test_outer_rollback_keeps_durable_marker_until_explicit_reconciliation(self):
        path=self.rollback_upload()
        self.assertTrue(path.exists())
        preview=self.recovery()
        self.assertEqual(preview['pending'],1)
        self.assertTrue(path.exists())
        result=self.recovery(execute=True)
        self.assertEqual(result['removed'],1)
        self.assertFalse(path.exists())

    def test_failed_compensation_keeps_recovery_without_masking_database_error(self):
        with patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql),patch('pathlib.Path.unlink',side_effect=PermissionError('Synthetic access denied')):
            with self.assertRaises(DataError): self.store('upload')
        self.assertEqual(len(list(self.root.rglob('*.png'))),1)
        self.assertEqual(self.recovery(execute=True)['removed'],1)
        self.assertEqual(list(self.root.rglob('*.png')),[])

    def test_database_unavailable_during_compensation_preserves_file_and_journal(self):
        with patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql),patch('console.asset_upload_recovery.reconcile_marker',side_effect=OperationalError('Synthetic database outage')):
            with self.assertRaises(DataError): self.store('upload')
        self.assertEqual(len(list(self.root.rglob('*.png'))),1)
        self.assertEqual(self.recovery(execute=True)['removed'],1)

    def test_committed_file_survives_marker_cleanup_failure_and_reconciliation(self):
        with patch('console.asset_upload_recovery.clear_upload_marker',side_effect=PermissionError('Synthetic marker busy')):
            row=self.store('upload')
        path=self.root/Path(row.storage_path).relative_to('storage/assets')
        self.assertEqual(self.recovery(execute=True)['referenced'],1)
        self.assertEqual(path.read_bytes(),self.bytes)
        self.assertEqual(self.recovery(execute=True)['processed'],0)

    def test_same_site_retry_and_local_import_reuse_original_asset(self):
        first=self.store('upload');second=self.store('import')
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(),1)
        self.assertEqual(len(list(self.root.rglob('*.png'))),1)
        self.assertEqual(self.recovery(execute=True)['processed'],0)
        self.assertEqual(first.mime_type,'image/png')
        self.assertEqual(first.sha256,hashlib.sha256(self.bytes).hexdigest())

    def test_cross_site_equal_content_uses_independent_files(self):
        first=self.store('upload')
        other=Site.objects.create(code='asset_'+uuid4().hex[:16],name='Synthetic other asset site',default_locale='en')
        second=store_uploaded_asset(site=other,uploaded_file=SimpleUploadedFile('synthetic.png',self.bytes),title='',alt_text='',caption='',created_by='synthetic')
        self.assertNotEqual(first.storage_path,second.storage_path)
        self.assertEqual(len(list(self.root.rglob('*.png'))),2)

    def test_reconciliation_keeps_file_referenced_by_another_site(self):
        path=self.rollback_upload()
        other=Site.objects.create(code='asset_'+uuid4().hex[:16],name='Synthetic shared asset site',default_locale='en')
        relative=path.relative_to(self.root).as_posix()
        MediaAsset.objects.create(site=other,asset_type='image',original_name='shared.png',title='Synthetic shared',
            mime_type='image/png',file_ext='.png',file_size_bytes=len(self.bytes),sha256=hashlib.sha256(self.bytes).hexdigest(),
            storage_path='storage/assets/'+relative,public_path='/assets/'+relative)
        self.assertEqual(self.recovery(execute=True)['referenced'],1)
        self.assertEqual(path.read_bytes(),self.bytes)

    def test_unconfirmed_allocation_never_overwrites_or_deletes_colliding_file(self):
        token='a'*32;now=timezone.now()
        target=_managed_asset_target(directory_name='images',year=f'{now:%Y}',month=f'{now:%m}',filename=f'{token[:16]}-upload-{token}.png')
        target.write_bytes(b'Preexisting collision, never owned by this attempt')
        with patch('console.forms.uuid.uuid4',return_value=type('Token',(),{'hex':token})()):
            with self.assertRaises(FileExistsError): self.store('upload')
        self.assertEqual(self.recovery(execute=True)['uncertain'],1)
        self.assertEqual(target.read_bytes(),b'Preexisting collision, never owned by this attempt')

    def test_bounded_command_previews_then_requires_confirmation(self):
        from django.core.management.base import CommandError
        paths=[self.rollback_upload() for _ in range(3)]
        output=StringIO()
        call_command('retry_asset_upload_cleanup',site=self.site.code,limit=2,stdout=output)
        self.assertTrue(all(path.exists() for path in paths))
        with self.assertRaises(CommandError):
            call_command('retry_asset_upload_cleanup',site=self.site.code,execute=True,stdout=StringIO())
        result=self.recovery(limit=2,execute=True)
        self.assertEqual(result['processed'],2);self.assertEqual(result['removed'],2)
        self.assertTrue(result['next_cursor'])
        self.assertEqual(self.recovery(after=result['next_cursor'],execute=True)['removed'],1)

    def test_inflight_write_is_not_deleted_by_another_connection(self):
        from console import asset_upload_recovery as recovery
        entered=threading.Event();release=threading.Event()
        original=recovery.confirm_created
        def pause(marker,data):
            original(marker,data);entered.set()
            if not release.wait(10): raise RuntimeError('Synthetic test synchronization timeout')
        def worker():
            close_old_connections()
            try: return self.store('upload').pk
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool,patch.object(recovery,'confirm_created',side_effect=pause):
            future=pool.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(self.recovery(execute=True)['busy'],1)
            finally: release.set()
            pk=future.result(timeout=10)
        self.assertTrue(MediaAsset.objects.filter(pk=pk).exists())
        self.assertEqual(len(list(self.root.rglob('*.png'))),1)

    def test_two_equal_uploads_converge_to_one_asset_without_shared_file_deletion(self):
        from console import asset_upload_recovery as recovery
        barrier=threading.Barrier(2)
        original=recovery.confirm_created
        def both_allocated(marker,data):
            original(marker,data);barrier.wait(timeout=10)
        def worker(mode):
            close_old_connections()
            try: return self.store(mode).pk
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool,patch.object(recovery,'confirm_created',side_effect=both_allocated):
            upload=pool.submit(worker,'upload');local=pool.submit(worker,'import')
            first,second=upload.result(timeout=15),local.result(timeout=15)
        self.assertEqual(first,second)
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(),1)
        self.assertEqual(len(list(self.root.rglob('*.png'))),1)
        self.assertEqual(self.recovery(execute=True)['processed'],0)

    def test_commit_accepted_but_response_unknown_keeps_committed_asset(self):
        original=connection.commit
        def accepted_then_unknown():
            original()
            raise OperationalError('Synthetic commit response lost after acceptance')
        with patch.object(connection,'commit',side_effect=accepted_then_unknown):
            with self.assertRaises(OperationalError): self.store('upload')
        row=MediaAsset.objects.get(site=self.site)
        path=self.root/Path(row.storage_path).relative_to('storage/assets')
        self.assertEqual(self.recovery(execute=True)['referenced'],1)
        self.assertEqual(path.read_bytes(),self.bytes)

    def test_corrupt_marker_cannot_point_cleanup_outside_owned_asset_path(self):
        path=self.rollback_upload()
        marker=next((self.root/'.pending-assets').glob('*.json'))
        data=json.loads(marker.read_text())
        outside=Path(self.directory.name)/'outside.png';outside.write_bytes(b'Protected outside fixture')
        data['storage_path']='../outside.png';marker.write_text(json.dumps(data))
        self.assertEqual(self.recovery(execute=True)['invalid'],1)
        self.assertEqual(outside.read_bytes(),b'Protected outside fixture')
        self.assertTrue(path.exists())

    def assert_http_failure_is_recoverable(self,mode):
        locale=SiteLocale.objects.create(site=self.site,locale_code='en',label='English',enabled=True,is_default=True)
        user=get_user_model().objects.create_user(username='asset_'+uuid4().hex[:16],password='synthetic-only')
        user.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0])
        client=Client(raise_request_exception=False);client.force_login(user)
        payload={'title':'Synthetic failed title','alt_text':'Preserved synthetic note'}
        if mode=='upload': payload['file']=SimpleUploadedFile('synthetic.png',self.bytes)
        else: payload['file_path']=str(self.source)
        route='asset_upload' if mode=='upload' else 'asset_import_path'
        with patch('console.views._resolved_content_site_locale',return_value=(self.site,locale)),patch.object(MediaAsset.objects,'create',side_effect=self.fail_actual_sql):
            response=client.post(reverse('console:'+route),payload)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'未完成入库')
        self.assertContains(response,'Synthetic failed title')
        self.assertNotContains(response,'division by zero')
        self.assertEqual(list(self.root.rglob('*.png')),[])

    @override_settings(DEBUG=False,ALLOWED_HOSTS=['testserver'],SECURE_SSL_REDIRECT=False)
    def test_upload_http_database_failure_keeps_input_and_retry_instructions(self):
        self.assert_http_failure_is_recoverable('upload')

    @override_settings(DEBUG=False,ALLOWED_HOSTS=['testserver'],SECURE_SSL_REDIRECT=False)
    def test_import_http_database_failure_keeps_input_and_retry_instructions(self):
        self.assert_http_failure_is_recoverable('import')
