from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase
from django.utils import timezone

from console.payloads import format_admin_datetime
from console.task_workspace_v2 import _format_datetime as task_datetime


class AdminDatetimeTests(SimpleTestCase):
    def test_same_task_instant_agrees_between_lead_and_task_pages(self):
        instant = datetime(2026, 10, 17, 5, 15, tzinfo=dt_timezone.utc)
        with timezone.override('Asia/Shanghai'):
            self.assertEqual(task_datetime(instant), '2026-10-17 13:15')
            self.assertEqual(format_admin_datetime(instant), '2026-10-17 13:15')
        with timezone.override('UTC'):
            self.assertEqual(format_admin_datetime(instant), '2026-10-17 05:15')

    def test_legacy_naive_value_and_empty_display_remain_supported(self):
        with timezone.override('Asia/Shanghai'):
            self.assertEqual(format_admin_datetime(datetime(2026, 10, 17, 5, 15)),
                             '2026-10-17 05:15')
            self.assertEqual(format_admin_datetime(None), '未设置')
