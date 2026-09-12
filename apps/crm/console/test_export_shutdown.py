from io import StringIO
import signal
import threading
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from console.customer_pool_exports import process_pending_customer_exports, _download_response


class ExportShutdownTests(SimpleTestCase):
    def test_stop_before_batch_does_not_query_database(self):
        self.assertEqual(process_pending_customer_exports(stop_requested=lambda: True), (0, 0))

    def test_active_job_finishes_but_no_next_job_is_claimed(self):
        stop = threading.Event()
        with patch('console.customer_pool_exports.CustomerExportJob.objects') as objects:
            objects.filter.return_value.order_by.return_value.values_list.return_value.__getitem__.return_value = [1, 2, 3]
            def finish_current(job_id):
                stop.set()
                return True
            with patch('console.customer_pool_exports.process_customer_export_job', side_effect=finish_current) as process:
                self.assertEqual(process_pending_customer_exports(stop_requested=stop.is_set), (1, 1))
                process.assert_called_once_with(1)

    def test_default_batch_preserves_attempt_and_success_counts(self):
        with patch('console.customer_pool_exports.CustomerExportJob.objects') as objects:
            objects.filter.return_value.order_by.return_value.values_list.return_value.__getitem__.return_value = [1, 2]
            with patch('console.customer_pool_exports.process_customer_export_job', side_effect=[True, False]) as process:
                self.assertEqual(process_pending_customer_exports(limit=2), (2, 1))
                self.assertEqual(process.call_count, 2)

    def test_sigterm_during_work_finishes_current_batch_and_restores_handlers(self):
        original = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        output = StringIO()
        def process(**kwargs):
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(kwargs['stop_requested']())
            return 1, 1
        with patch('leads.management.commands.process_customer_exports.process_pending_customer_exports', side_effect=process) as worker:
            call_command('process_customer_exports', watch=True, interval_seconds=60, stdout=output)
            worker.assert_called_once()
        self.assertIn('processed 1/1', output.getvalue())
        self.assertIn('worker stopped', output.getvalue())
        for sig, handler in original.items():
            self.assertEqual(signal.getsignal(sig), handler)

    def test_sigint_during_idle_wait_stops_without_another_batch(self):
        event = threading.Event()
        with patch('leads.management.commands.process_customer_exports.threading.Event', return_value=event), \
                patch.object(event, 'wait', side_effect=lambda _: signal.raise_signal(signal.SIGINT)), \
                patch('leads.management.commands.process_customer_exports.process_pending_customer_exports', return_value=(0, 0)) as worker:
            call_command('process_customer_exports', watch=True, stdout=StringIO())
            worker.assert_called_once()
        self.assertTrue(event.is_set())

    def test_processing_error_restores_handlers_and_remains_an_error(self):
        original = signal.getsignal(signal.SIGTERM)
        with patch('leads.management.commands.process_customer_exports.process_pending_customer_exports', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                call_command('process_customer_exports', watch=True, stdout=StringIO())
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_customer_download_filename_uses_vorntek(self):
        self.assertIn('vorntek-customers.xlsx', _download_response(b'synthetic')['Content-Disposition'])
