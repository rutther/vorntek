from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from leads.services import LeadCaptureError, clean_submission_input


class GenericFormContractTests(SimpleTestCase):
    def form(self, fields=None):
        if fields is None:
            fields = [
                {"name": "full_name", "type": "text", "enabled": True, "required": True, "label": "姓名"},
                {"name": "email", "type": "email", "enabled": True, "required": False},
                {"name": "phone", "type": "tel", "enabled": True, "required": False},
                {"name": "company", "type": "text", "enabled": False, "required": False},
                {"name": "country", "type": "text", "enabled": True, "required": False},
                {"name": "product_category", "type": "select", "enabled": False, "required": False},
                {"name": "capacity", "type": "text", "enabled": False, "required": False},
                {"name": "message", "type": "textarea", "enabled": True, "required": False},
            ]
        return SimpleNamespace(site_id=7, id=11, category="general", form_schema={"fields": fields})

    def payload(self):
        return {
            "full_name": "Example visitor", "email": "visitor@example.invalid",
            "message": "A general consultation request.",
            "extra_fields": {},
            "form_started_at": (timezone.now() - timedelta(seconds=5)).isoformat(),
            "consent": {"contact": True, "privacy_notice": True, "marketing": False},
        }

    def clean(self, payload=None, form=None):
        return clean_submission_input(payload or self.payload(), form_definition=form or self.form())

    def test_personal_consultation_needs_no_company_country_or_industry_parameters(self):
        cleaned = self.clean()
        self.assertEqual(cleaned["email"], "visitor@example.invalid")
        self.assertEqual(cleaned["company"], "")
        self.assertEqual(cleaned["country"], "")
        self.assertEqual(cleaned["payload_json"], {})

    def test_new_general_form_without_custom_schema_uses_neutral_defaults(self):
        form = SimpleNamespace(site_id=7, id=11, category="general", form_schema={})
        self.assertEqual(self.clean(form=form)["full_name"], "Example visitor")

    def test_required_company_is_enforced_only_when_configured(self):
        form = self.form()
        company = next(field for field in form.form_schema["fields"] if field["name"] == "company")
        company.update(enabled=True, required=True)
        with self.assertRaises(LeadCaptureError):
            self.clean(form=form)
        payload = self.payload()
        payload["company"] = "Example organization"
        self.assertEqual(self.clean(payload, form)["company"], "Example organization")

    def test_disabled_fields_do_not_collect_submitted_values(self):
        payload = self.payload()
        payload["company"] = "Should not be collected"
        payload["extra_fields"] = {"product_category": "disabled", "capacity": "disabled"}
        cleaned = self.clean(payload)
        self.assertEqual(cleaned["company"], "")
        self.assertEqual(cleaned["payload_json"], {})

    def test_custom_select_accepts_only_the_configured_choices(self):
        form = self.form()
        form.form_schema["fields"].append({
            "name": "request_kind", "type": "select", "label": "咨询类型",
            "enabled": True, "required": True,
            "options": [{"value": "consultation", "label": "咨询"}, {"value": "support", "label": "支持"}],
        })
        with self.assertRaises(LeadCaptureError):
            self.clean(form=form)
        payload = self.payload()
        payload["extra_fields"] = {"request_kind": "support"}
        self.assertEqual(self.clean(payload, form)["payload_json"], {"request_kind": "support"})
        payload["extra_fields"]["request_kind"] = "unconfigured"
        with self.assertRaises(LeadCaptureError):
            self.clean(payload, form)

    def test_disabled_email_cannot_satisfy_the_enabled_contact_route(self):
        form = self.form()
        next(field for field in form.form_schema["fields"] if field["name"] == "email")["enabled"] = False
        with self.assertRaises(LeadCaptureError):
            self.clean(form=form)
        payload = self.payload()
        payload.update(email="ignored-invalid-address", phone="+12025550123")
        cleaned = self.clean(payload, form)
        self.assertEqual(cleaned["email"], "")
        self.assertEqual(cleaned["phone"], "+12025550123")

    def test_explicit_optional_name_is_respected(self):
        form = self.form()
        next(field for field in form.form_schema["fields"] if field["name"] == "full_name")["required"] = False
        payload = self.payload()
        payload["full_name"] = ""
        self.assertEqual(self.clean(payload, form)["full_name"], "")

    def test_missing_contact_consent_and_invalid_email_still_fail(self):
        for change in ({"email": ""}, {"email": "invalid"}, {"consent": {"contact": False, "privacy_notice": True}}):
            with self.subTest(change=change), self.assertRaises(LeadCaptureError):
                payload = self.payload()
                payload.update(change)
                self.clean(payload)

    def test_malformed_schema_is_a_validation_error(self):
        for invalid in ("broken", [{"name": "email", "type": ["email"]}],
                        [{"name": "email", "enabled": "false"}],
                        [{"name": "email", "options": {"wrong": "shape"}}]):
            with self.subTest(invalid=invalid), self.assertRaises(LeadCaptureError):
                form = self.form()
                form.form_schema = {"fields": invalid}
                self.clean(form=form)

    def test_configured_number_and_date_are_validated_before_persistence(self):
        for kind, valid, invalids in (
            ("number", "0", ("not-a-number", "NaN", "Infinity")),
            ("date", "2026-10-16", ("2026-02-31", "tomorrow")),
        ):
            form = self.form()
            form.form_schema["fields"].append({"name": "project_parameter", "type": kind,
                                               "enabled": True, "required": True})
            payload = self.payload()
            payload["extra_fields"] = {"project_parameter": valid}
            self.assertEqual(self.clean(payload, form)["payload_json"], {"project_parameter": valid})
            for invalid in invalids:
                with self.subTest(kind=kind, value=invalid), self.assertRaises(LeadCaptureError):
                    payload["extra_fields"]["project_parameter"] = invalid
                    self.clean(payload, form)
