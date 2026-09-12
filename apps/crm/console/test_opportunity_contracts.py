from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_SYSTEM_ADMIN
from console.opportunity_versions import opportunity_version_token
from leads.crm_services import (
    OPPORTUNITY_STAGE_TRANSITIONS,
    change_opportunity_stage,
)
from leads.models import (
    Activity,
    Company,
    Contact,
    CrmAttachment,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    OpportunityStageHistory,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from marketing.models import CanonicalEvent
from sitecore.models import Cta, MediaAsset, PageRoute, Site, SiteLocale


class OpportunityContractTests(TestCase):
    """Opportunity transitions and their HTTP audit are one domain contract."""

    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        MediaAsset,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        LeadSubmission,
        Company,
        Contact,
        Opportunity,
        Activity,
        Task,
        OpportunityStageHistory,
        CrmAttachment,
    )

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
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.admin = get_user_model().objects.create_user(
            username='opportunity-contract-admin',
            password='test-password',
        )
        self.admin.groups.add(admin_group)
        self.site = Site.objects.create(
            code='siteos_demo',
            name='SiteOS Demo',
            base_url='https://example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.team = SalesTeam.objects.create(
            site=self.site,
            code='opportunity-contracts',
            name='Opportunity contracts',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=self.admin,
            membership_role='manager',
        )
        self.company = Company.objects.create(
            site=self.site,
            owner_user=self.admin,
            team=self.team,
            name='Contract Beverage Co.',
            normalized_name='contract beverage co.',
            country='AE',
        )

    def _opportunity(self, stage: str, *, probability: int | None = None) -> Opportunity:
        defaults = {
            'qualification': 10,
            'discovery': 20,
            'solution': 40,
            'quotation': 60,
            'negotiation': 80,
            'on_hold': 35,
            'won': 100,
            'lost': 0,
        }
        is_closed = stage in {'won', 'lost'}
        return Opportunity.objects.create(
            site=self.site,
            company=self.company,
            owner_user=self.admin,
            team=self.team,
            name=f'{stage}-{Opportunity.objects.count() + 1}',
            stage=stage,
            probability=defaults[stage] if probability is None else probability,
            won_reason='Original win' if stage == 'won' else '',
            lost_reason='Original loss' if stage == 'lost' else '',
            closed_at=timezone.now() if is_closed else None,
        )

    @staticmethod
    def _reason(source: str, target: str) -> str:
        if target == 'won':
            return 'Commercial approval received.'
        if target == 'lost':
            return 'Customer selected another supplier.'
        if source in {'won', 'lost'}:
            return 'Customer restarted the project.'
        return ''

    def test_transition_matrix_allows_only_documented_business_moves(self):
        active = ('qualification', 'discovery', 'solution', 'quotation', 'negotiation')
        expected = {
            'qualification': {'discovery', 'on_hold', 'lost'},
            'discovery': {'qualification', 'solution', 'on_hold', 'lost'},
            'solution': {'discovery', 'quotation', 'on_hold', 'lost'},
            'quotation': {'solution', 'negotiation', 'on_hold', 'won', 'lost'},
            'negotiation': {'quotation', 'on_hold', 'won', 'lost'},
            'on_hold': {*active, 'lost'},
            'won': {*active, 'on_hold'},
            'lost': {*active, 'on_hold'},
        }
        self.assertEqual(
            {key: set(value) for key, value in OPPORTUNITY_STAGE_TRANSITIONS.items()},
            expected,
        )

        all_stages = (*active, 'on_hold', 'won', 'lost')
        for source in all_stages:
            for target in all_stages:
                with self.subTest(source=source, target=target):
                    opportunity = self._opportunity(source)
                    activity_count = Activity.objects.count()
                    if target == source or target in expected[source]:
                        changed = change_opportunity_stage(
                            opportunity_id=opportunity.id,
                            stage=target,
                            actor=self.admin,
                            reason=self._reason(source, target),
                        )
                        self.assertEqual(changed.stage, target)
                        expected_increment = 0 if target == source else 1
                        self.assertEqual(
                            Activity.objects.count(),
                            activity_count + expected_increment,
                        )
                    else:
                        with self.assertRaisesMessage(ValidationError, '不能从'):
                            change_opportunity_stage(
                                opportunity_id=opportunity.id,
                                stage=target,
                                actor=self.admin,
                                reason=self._reason(source, target),
                            )
                        opportunity.refresh_from_db()
                        self.assertEqual(opportunity.stage, source)
                        self.assertEqual(Activity.objects.count(), activity_count)

    def test_terminal_transitions_normalize_probability_reasons_and_closure(self):
        won = self._opportunity('quotation', probability=57)
        won.lost_reason = 'Stale loss'
        won.save(update_fields=['lost_reason'])
        change_opportunity_stage(
            opportunity_id=won.id,
            stage='won',
            actor=self.admin,
            reason='Purchase order signed.',
        )
        won.refresh_from_db()
        self.assertEqual(won.probability, 100)
        self.assertEqual(won.won_reason, 'Purchase order signed.')
        self.assertEqual(won.lost_reason, '')
        self.assertIsNotNone(won.closed_at)

        lost = self._opportunity('discovery', probability=24)
        lost.won_reason = 'Stale win'
        lost.save(update_fields=['won_reason'])
        change_opportunity_stage(
            opportunity_id=lost.id,
            stage='lost',
            actor=self.admin,
            reason='Capital budget canceled.',
        )
        lost.refresh_from_db()
        self.assertEqual(lost.probability, 0)
        self.assertEqual(lost.lost_reason, 'Capital budget canceled.')
        self.assertEqual(lost.won_reason, '')
        self.assertIsNotNone(lost.closed_at)

    def test_reopening_clears_terminal_fields_and_uses_target_default_probability(self):
        won = self._opportunity('won')
        change_opportunity_stage(
            opportunity_id=won.id,
            stage='solution',
            actor=self.admin,
            reason='Customer restarted technical review.',
        )
        won.refresh_from_db()
        self.assertEqual(won.stage, 'solution')
        self.assertEqual(won.probability, 40)
        self.assertEqual(won.won_reason, '')
        self.assertEqual(won.lost_reason, '')
        self.assertIsNone(won.closed_at)

        lost = self._opportunity('lost')
        change_opportunity_stage(
            opportunity_id=lost.id,
            stage='on_hold',
            actor=self.admin,
            reason='Budget review reopened but timing is not confirmed.',
        )
        lost.refresh_from_db()
        self.assertEqual(lost.stage, 'on_hold')
        self.assertEqual(lost.probability, 10)
        self.assertEqual(lost.won_reason, '')
        self.assertEqual(lost.lost_reason, '')
        self.assertIsNone(lost.closed_at)

    def test_nonterminal_transition_preserves_safe_probability_and_clears_stale_closure(self):
        opportunity = self._opportunity('discovery', probability=37)
        opportunity.won_reason = 'Stale win'
        opportunity.lost_reason = 'Stale loss'
        opportunity.closed_at = timezone.now()
        opportunity.save(update_fields=['won_reason', 'lost_reason', 'closed_at'])

        change_opportunity_stage(
            opportunity_id=opportunity.id,
            stage='solution',
            actor=self.admin,
        )

        opportunity.refresh_from_db()
        self.assertEqual(opportunity.probability, 37)
        self.assertEqual(opportunity.won_reason, '')
        self.assertEqual(opportunity.lost_reason, '')
        self.assertIsNone(opportunity.closed_at)

    def test_closing_and_reopening_require_a_reason_but_same_stage_is_idempotent(self):
        opportunity = self._opportunity('quotation')
        with self.assertRaisesMessage(ValidationError, '成交原因'):
            change_opportunity_stage(
                opportunity_id=opportunity.id,
                stage='won',
                actor=self.admin,
                reason='   ',
            )

        won = self._opportunity('won')
        with self.assertRaisesMessage(ValidationError, '重新开启'):
            change_opportunity_stage(
                opportunity_id=won.id,
                stage='on_hold',
                actor=self.admin,
                reason='',
            )
        unchanged = change_opportunity_stage(
            opportunity_id=won.id,
            stage='won',
            actor=self.admin,
            reason='',
        )
        self.assertEqual(unchanged.stage, 'won')

    def test_opportunity_update_rolls_back_business_record_and_activity_when_audit_fails(self):
        opportunity = self._opportunity('qualification')
        self.client.force_login(self.admin)
        payload = {
            'version': opportunity_version_token(opportunity.updated_at),
            'name': 'Changed project name',
            'owner_user': str(self.admin.id),
            'value_amount': '350000.00',
            'currency': 'USD',
            'probability': '35',
            'expected_close_date': '2027-01-30',
            'product_scope': 'Aseptic line',
            'capacity_target': '36,000 BPH',
            'packaging_format': 'PET',
            'next_step': 'Send revised quotation',
            'next_follow_up_at': '',
        }

        with patch(
            'console.sales_views.record_audit',
            side_effect=RuntimeError('audit unavailable'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'audit unavailable'):
                self.client.post(
                    reverse('console:opportunity_update', args=[opportunity.id]),
                    payload,
                )

        opportunity.refresh_from_db()
        self.assertNotEqual(opportunity.name, 'Changed project name')
        self.assertEqual(opportunity.probability, 10)
        self.assertFalse(Activity.objects.filter(opportunity=opportunity).exists())

    def test_opportunity_stage_rolls_back_transition_and_activity_when_audit_fails(self):
        opportunity = self._opportunity('qualification')
        self.client.force_login(self.admin)

        with patch(
            'console.sales_views.record_audit',
            side_effect=RuntimeError('audit unavailable'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'audit unavailable'):
                self.client.post(
                    reverse('console:opportunity_stage', args=[opportunity.id]),
                    {
                        'version': opportunity_version_token(opportunity.updated_at),
                        'stage': 'discovery',
                        'reason': '',
                    },
                )

        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, 'qualification')
        self.assertEqual(opportunity.probability, 10)
        self.assertFalse(Activity.objects.filter(opportunity=opportunity).exists())
