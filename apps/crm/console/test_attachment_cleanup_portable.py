"""HTTP recovery controls on synthetic SQLite; native SQL is a separate gate."""
from django.apps import apps
from django.db import connection

from . import test_attachment_cleanup_http as native_cases


class AttachmentCleanupPortableTests(native_cases.AttachmentCleanupHttpTests):
    __unittest_skip__ = False
    __unittest_skip_why__ = ''

    @classmethod
    def setUpClass(cls):
        cls.created_models = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in apps.get_models():
                if not model._meta.managed and not model._meta.proxy and model._meta.db_table not in existing:
                    editor.create_model(model)
                    cls.created_models.append(model)
                    existing.add(model._meta.db_table)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
