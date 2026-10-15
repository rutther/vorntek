from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from leads.crm_relationships import validate_crm_relationship_targets


class PersonalCrmRelationshipTests(SimpleTestCase):
    def targets(self):
        site = SimpleNamespace(pk=1)
        contact = SimpleNamespace(pk=10, site_id=1, team_id=2, company_id=None)
        opportunity = SimpleNamespace(pk=20, site_id=1, team_id=2, company_id=None, primary_contact_id=10)
        return dict(site=site, contact=contact, opportunity=opportunity)

    def test_explicit_primary_contact_proves_a_personal_opportunity_relationship(self):
        scope = validate_crm_relationship_targets(**self.targets())
        self.assertEqual((scope.site_id, scope.team_id, scope.target_count), (1, 2, 2))

    def test_empty_companies_do_not_join_unrelated_contacts_to_an_opportunity(self):
        targets = self.targets()
        targets['opportunity'].primary_contact_id = 99
        with self.assertRaises(ValidationError):
            validate_crm_relationship_targets(**targets)

    def test_personal_link_does_not_bypass_site_or_team_scope(self):
        for field, value in [('site_id', 3), ('team_id', 4), ('team_id', None)]:
            targets = self.targets()
            setattr(targets['contact'], field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                validate_crm_relationship_targets(**targets)
