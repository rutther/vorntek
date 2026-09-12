from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from leads.lead_workflow_services import (
    ACTIVITY_SUBJECT_MAX_LENGTH,
    FOLLOW_UP_NOTES_MAX_LENGTH,
    ActivityInput,
    IDEMPOTENCY_DATABASE_ONLY,
    LeadIdempotencyConflictError,
    LeadOptimisticLockError,
    LeadWorkflowError,
    NextTaskInput,
    QUALIFICATION_NOTES_MAX_LENGTH,
    QualificationInput,
    TASK_TITLE_MAX_LENGTH,
    dispose_lead,
)
from scripts.prepare_candidate_demo import QUALIFICATION_KEYS, qualification_state_for_score


def make_submission(*, updated_at=None, stage='new'):
    updated_at = updated_at or timezone.now() - timedelta(minutes=5)
    site = SimpleNamespace(id=3, pk=3)
    assignee = SimpleNamespace(id=17, pk=17, is_active=True)
    team = SimpleNamespace(id=11, pk=11, site_id=3, enabled=True)
    return SimpleNamespace(
        id=41,
        pk=41,
        site=site,
        site_id=3,
        form=SimpleNamespace(
            capi_enabled=True,
            contacted_event_name='contacted_lead',
            qualified_event_name='qualified_lead',
            won_event_name='converted',
        ),
        consent_json={'marketing': True},
        stage=stage,
        team=team,
        team_id=team.id,
        assignee=assignee,
        assignee_id=assignee.id,
        source_channel='meta_ads',
        source_detail='campaign-1',
        next_follow_up_at=None,
        follow_up_notes='',
        qualification_score=0,
        qualification_json={},
        qualification_notes='',
        qualification_overridden=False,
        buyer_value=None,
        buyer_currency='USD',
        contacted_at=None,
        qualified_at=None,
        won_at=None,
        lost_at=None,
        stage_updated_at=updated_at,
        updated_at=updated_at,
        config_json={},
        save=Mock(),
    )


def qualification_four_of_five():
    return QualificationInput(
        contactable=True,
        company_verified=True,
        project_confirmed=True,
        technical_fit=True,
        next_step_confirmed=False,
    )


def activity(body='Customer confirmed the line scope.'):
    return ActivityInput(
        activity_type='call',
        direction='outbound',
        subject='需求确认电话',
        body=body,
    )


class CandidateDemoQualificationTests(SimpleTestCase):
    def test_demo_qualification_state_uses_canonical_keys_and_matches_score(self):
        for score in range(0, 101, 20):
            with self.subTest(score=score):
                state = qualification_state_for_score(score)
                self.assertEqual(tuple(state), QUALIFICATION_KEYS)
                self.assertEqual(sum(state.values()) * 20, score)
                self.assertEqual(
                    list(state.values()),
                    [index < score // 20 for index in range(len(QUALIFICATION_KEYS))],
                )

    def test_demo_qualification_state_rejects_noncanonical_scores(self):
        for score in (-20, 10, 120):
            with self.subTest(score=score):
                with self.assertRaises(ValueError):
                    qualification_state_for_score(score)


class LeadWorkflowV2UnitTests(SimpleTestCase):
    def setUp(self):
        self.actor = SimpleNamespace(pk=9, id=9, is_authenticated=True)
        self.now = timezone.now()
        self.submission = make_submission(updated_at=self.now - timedelta(minutes=5))
        self.activity_appender = Mock(return_value=SimpleNamespace(id=501, pk=501))
        self.task_creator = Mock(return_value=SimpleNamespace(id=601, pk=601))
        self.outbox = SimpleNamespace(id=701, pk=701)
        self.outbox_queuer = Mock(return_value=[self.outbox])
        self.dispatcher = Mock(return_value=True)
        self.audit_recorder = Mock(return_value=SimpleNamespace(id=801))

    def call_service(self, **overrides):
        values = {
            'submission_id': self.submission.id,
            'actor': self.actor,
            'expected_updated_at': self.submission.updated_at,
            'stage': 'contacted',
            'qualification': QualificationInput(),
            'activity': activity(),
            'next_task': NextTaskInput(
                title='发送工艺布局资料',
                due_at=self.now + timedelta(days=1),
            ),
            'activity_appender': self.activity_appender,
            'task_creator': self.task_creator,
            'outbox_queuer': self.outbox_queuer,
            'outbox_dispatcher': self.dispatcher,
            'audit_recorder': self.audit_recorder,
            'clock': lambda: self.now,
        }
        values.update(overrides)
        with (
            patch(
                'leads.lead_workflow_services.transaction.atomic',
                return_value=nullcontext(),
            ),
            patch(
                'leads.lead_workflow_services._lock_submission',
                return_value=self.submission,
            ) as locked,
            patch(
                'leads.lead_workflow_services.SalesTeamMember.objects.filter'
            ) as membership_filter,
            patch(
                'leads.lead_workflow_services.Task.objects.filter'
            ) as task_filter,
            patch('leads.lead_workflow_services.transaction.on_commit') as on_commit,
        ):
            membership_filter.return_value.exists.return_value = True
            task_filter.return_value.order_by.return_value.values_list.return_value.first.return_value = None
            result = dispose_lead(**values)
        return result, locked, on_commit

    def test_complete_disposition_writes_activity_task_stage_audit_then_schedules_dispatch(self):
        result, locked, on_commit = self.call_service()

        locked.assert_called_once_with(41)
        self.assertEqual(self.submission.stage, 'contacted')
        self.assertEqual(self.submission.contacted_at, self.now)
        self.assertEqual(
            self.submission.next_follow_up_at,
            self.now + timedelta(days=1),
        )
        self.assertEqual(self.submission.save.call_count, 1)
        self.activity_appender.assert_called_once()
        self.task_creator.assert_called_once()
        self.assertEqual(self.task_creator.call_args.kwargs['due_at'], self.submission.next_follow_up_at)
        self.audit_recorder.assert_called_once()
        self.assertEqual(self.audit_recorder.call_args.kwargs['action'], 'lead_disposed_v2')
        self.assertEqual(result.activity_ids, (501,))
        self.assertEqual(result.task_id, 601)
        self.assertEqual(result.outbox_ids, (701,))
        self.assertEqual(result.dispatch_scheduled, 1)
        self.assertFalse(result.replayed)

        self.dispatcher.assert_not_called()
        callback = on_commit.call_args.args[0]
        self.assertTrue(on_commit.call_args.kwargs['robust'])
        callback()
        self.dispatcher.assert_called_once_with(self.outbox)

    def test_assignment_change_updates_owner_and_team_and_records_assignment_activity(self):
        new_assignee = SimpleNamespace(id=22, pk=22, is_active=True)
        new_team = SimpleNamespace(id=12, pk=12, site_id=3, enabled=True)
        with patch(
            'leads.lead_workflow_services._resolve_assignment',
            return_value=(new_assignee, new_team),
        ):
            result, _, _ = self.call_service(assignee_id=22, team_id=12)

        self.assertEqual(self.submission.assignee.id, 22)
        self.assertEqual(self.submission.team.id, 12)
        self.assertEqual(self.activity_appender.call_count, 2)
        assignment_call = self.activity_appender.call_args_list[0]
        self.assertEqual(assignment_call.kwargs['activity_type'], 'assignment')
        self.assertEqual(
            assignment_call.kwargs['metadata'],
            {
                'from_assignee_id': 17,
                'to_assignee_id': 22,
                'from_team_id': 11,
                'to_team_id': 12,
            },
        )
        self.assertEqual(result.activity_ids, (501, 501))

    def test_optimistic_lock_conflict_is_detected_before_any_write(self):
        stale = self.submission.updated_at - timedelta(seconds=1)
        with self.assertRaises(LeadOptimisticLockError):
            self.call_service(expected_updated_at=stale)

        self.submission.save.assert_not_called()
        self.activity_appender.assert_not_called()
        self.task_creator.assert_not_called()
        self.outbox_queuer.assert_not_called()
        self.audit_recorder.assert_not_called()

    def test_locked_record_is_reauthorized_before_replay_or_any_write(self):
        authorizer = Mock(side_effect=PermissionDenied('scope changed'))

        with self.assertRaises(PermissionDenied):
            self.call_service(locked_submission_authorizer=authorizer)

        authorizer.assert_called_once_with(self.submission)
        self.submission.save.assert_not_called()
        self.activity_appender.assert_not_called()
        self.task_creator.assert_not_called()
        self.outbox_queuer.assert_not_called()
        self.audit_recorder.assert_not_called()

    def test_reposting_same_inactive_owner_is_unchanged_after_row_lock(self):
        self.submission.assignee.is_active = False

        with patch('leads.lead_workflow_services._active_user') as active_user:
            self.call_service(assignee_id=self.submission.assignee_id, next_task=None)

        active_user.assert_not_called()
        self.assertEqual(self.submission.assignee_id, 17)

    def test_inactive_current_owner_requires_reassignment_before_creating_task(self):
        self.submission.assignee.is_active = False

        with self.assertRaisesRegex(
            LeadWorkflowError,
            '请先重新分配负责人再创建任务',
        ) as raised:
            self.call_service(assignee_id=self.submission.assignee_id)

        self.assertEqual(raised.exception.code, 'task_owner_inactive_requires_reassignment')
        self.submission.save.assert_not_called()
        self.activity_appender.assert_not_called()
        self.task_creator.assert_not_called()
        self.outbox_queuer.assert_not_called()
        self.audit_recorder.assert_not_called()

    def test_invalid_qualification_is_rejected_before_lock_or_write(self):
        with self.assertRaisesRegex(LeadWorkflowError, '至少四项'):
            self.call_service(
                stage='qualified',
                qualification=QualificationInput(contactable=True),
            )

        self.submission.save.assert_not_called()
        self.activity_appender.assert_not_called()
        self.outbox_queuer.assert_not_called()

    def test_domain_text_limits_accept_the_exact_form_boundaries(self):
        self.call_service(
            qualification=QualificationInput(notes='合' * QUALIFICATION_NOTES_MAX_LENGTH),
            activity=ActivityInput(
                activity_type='call',
                direction='outbound',
                subject='沟' * ACTIVITY_SUBJECT_MAX_LENGTH,
                body='Boundary values are valid.',
            ),
            next_task=NextTaskInput(
                title='任' * TASK_TITLE_MAX_LENGTH,
                due_at=self.now + timedelta(days=1),
            ),
            follow_up_notes='跟' * FOLLOW_UP_NOTES_MAX_LENGTH,
        )

        self.assertEqual(
            len(self.activity_appender.call_args.kwargs['subject']),
            ACTIVITY_SUBJECT_MAX_LENGTH,
        )
        self.assertEqual(
            len(self.task_creator.call_args.kwargs['title']),
            TASK_TITLE_MAX_LENGTH,
        )
        self.assertEqual(
            len(self.submission.qualification_notes),
            QUALIFICATION_NOTES_MAX_LENGTH,
        )
        self.assertEqual(
            len(self.submission.follow_up_notes),
            FOLLOW_UP_NOTES_MAX_LENGTH,
        )

    def test_domain_text_overflow_is_rejected_before_any_write(self):
        cases = (
            (
                'activity subject',
                {'activity': ActivityInput(
                    activity_type='call',
                    direction='outbound',
                    subject='x' * (ACTIVITY_SUBJECT_MAX_LENGTH + 1),
                    body='Body',
                )},
                'activity_subject_too_long',
            ),
            (
                'task title',
                {'next_task': NextTaskInput(
                    title='x' * (TASK_TITLE_MAX_LENGTH + 1),
                    due_at=self.now + timedelta(days=1),
                )},
                'task_title_too_long',
            ),
            (
                'follow up notes',
                {'follow_up_notes': 'x' * (FOLLOW_UP_NOTES_MAX_LENGTH + 1)},
                'follow_up_notes_too_long',
            ),
            (
                'qualification notes',
                {'qualification': QualificationInput(
                    notes='x' * (QUALIFICATION_NOTES_MAX_LENGTH + 1),
                )},
                'qualification_notes_too_long',
            ),
        )
        for label, overrides, expected_code in cases:
            with self.subTest(label=label), self.assertRaises(LeadWorkflowError) as raised:
                self.call_service(**overrides)
            self.assertEqual(raised.exception.code, expected_code)
            self.submission.save.assert_not_called()
            self.activity_appender.assert_not_called()
            self.task_creator.assert_not_called()
            self.outbox_queuer.assert_not_called()
            self.audit_recorder.assert_not_called()

    def test_idempotency_token_replays_database_result_without_duplicate_writes(self):
        first, _, _ = self.call_service(idempotency_token='dispose-0001')
        first_save_count = self.submission.save.call_count
        first_activity_count = self.activity_appender.call_count
        first_task_count = self.task_creator.call_count
        first_queue_count = self.outbox_queuer.call_count
        first_audit_count = self.audit_recorder.call_count

        second, _, on_commit = self.call_service(
            idempotency_token='dispose-0001',
            expected_updated_at=self.now - timedelta(days=30),
        )

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(second.idempotency_mode, IDEMPOTENCY_DATABASE_ONLY)
        self.assertEqual(second.activity_ids, first.activity_ids)
        self.assertEqual(second.task_id, first.task_id)
        self.assertEqual(second.outbox_ids, first.outbox_ids)
        self.assertEqual(second.dispatch_scheduled, 0)
        self.assertEqual(self.submission.save.call_count, first_save_count)
        self.assertEqual(self.activity_appender.call_count, first_activity_count)
        self.assertEqual(self.task_creator.call_count, first_task_count)
        self.assertEqual(self.outbox_queuer.call_count, first_queue_count)
        self.assertEqual(self.audit_recorder.call_count, first_audit_count)
        on_commit.assert_not_called()

    def test_reusing_token_for_different_payload_is_a_conflict(self):
        self.call_service(idempotency_token='dispose-0002')
        save_count = self.submission.save.call_count

        with self.assertRaises(LeadIdempotencyConflictError):
            self.call_service(
                idempotency_token='dispose-0002',
                activity=activity('A materially different outcome.'),
                expected_updated_at=self.submission.updated_at,
            )

        self.assertEqual(self.submission.save.call_count, save_count)

    def test_no_token_explicitly_reports_that_retry_deduplication_is_absent(self):
        result, _, _ = self.call_service(next_task=None)

        self.assertEqual(result.idempotency_mode, 'none')
        self.assertEqual(result.task_id, None)


class LeadWorkflowV2RollbackTests(TransactionTestCase):
    reset_sequences = False

    def test_failure_after_a_submission_save_rolls_back_the_whole_transaction(self):
        actor = SimpleNamespace(pk=9, id=9, is_authenticated=True)
        now = timezone.now()
        submission = make_submission(updated_at=now - timedelta(minutes=5))
        probe_username = 'lead-workflow-rollback-probe'

        def save_probe(**_kwargs):
            get_user_model().objects.create_user(username=probe_username)

        submission.save = save_probe
        exploding_activity = Mock(side_effect=RuntimeError('activity write failed'))

        with (
            patch(
                'leads.lead_workflow_services._lock_submission',
                return_value=submission,
            ),
            self.assertRaisesRegex(RuntimeError, 'activity write failed'),
        ):
            dispose_lead(
                submission_id=submission.id,
                actor=actor,
                expected_updated_at=submission.updated_at,
                stage='contacted',
                qualification=QualificationInput(),
                activity=activity(),
                activity_appender=exploding_activity,
                task_creator=Mock(),
                outbox_queuer=Mock(return_value=[]),
                outbox_dispatcher=Mock(),
                audit_recorder=Mock(),
                clock=lambda: now,
            )

        self.assertFalse(get_user_model().objects.filter(username=probe_username).exists())
