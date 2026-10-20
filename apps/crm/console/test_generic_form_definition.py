import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from console.lead_forms import LeadFormEditorForm, default_form_schema
from console.views import public_lead_form_definition


class GenericFormDefinitionTests(SimpleTestCase):
    def definition(self, schema):
        form = SimpleNamespace(code="general-inquiry", name="Example consultation", category="general",
                               id=7, site_id=1, locale_id=None, locale=None,
                               form_schema=schema, success_message="Received.")
        site = SimpleNamespace(pk=1)
        request = RequestFactory().get("/api/leads/forms/general-inquiry/definition/")
        with patch("console.views.default_site_locale", return_value=(site, None)), \
                patch("console.views.LeadFormDefinition.objects") as manager:
            queryset = manager.select_related.return_value.filter.return_value
            queryset.filter.return_value.first.return_value = form
            return public_lead_form_definition(request, form.code)

    def test_default_editor_and_public_contract_do_not_require_industry_fields(self):
        schema = default_form_schema()
        fields = {field["name"]: field for field in schema["fields"]}
        self.assertFalse(fields["company"]["required"])
        self.assertFalse(fields["country"]["required"])
        self.assertFalse(fields["product_category"]["enabled"])
        self.assertFalse(fields["capacity"]["enabled"])
        response = self.definition({})
        self.assertEqual(response.status_code, 200)
        names = {field["name"] for field in json.loads(response.content)["form"]["fields"]}
        self.assertNotIn("capacity", names)
        self.assertNotIn("product_category", names)

    def test_public_definition_keeps_choices_and_excludes_disabled_fields(self):
        response = self.definition({"fields": [
            {"name": "email", "type": "email", "enabled": True, "required": True},
            {"name": "company", "type": "text", "enabled": False},
            {"name": "request_kind", "type": "select", "enabled": True,
             "options": [{"value": "consultation", "label": "咨询"}]},
        ]})
        self.assertEqual(response.status_code, 200)
        fields = {field["name"]: field for field in json.loads(response.content)["form"]["fields"]}
        self.assertNotIn("company", fields)
        self.assertEqual(fields["request_kind"]["options"], [{"value": "consultation", "label": "咨询"}])

    def test_malformed_configuration_does_not_publish_an_unusable_form(self):
        response = self.definition({"fields": "broken"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.content)["ok"])

    def test_editing_an_existing_custom_field_keeps_its_choices(self):
        choices = [{"value": "consultation", "label": "咨询"}]
        record = SimpleNamespace(category="general", form_schema={"fields": [
            {"name": "full_name", "enabled": True, "required": True},
            {"name": "email", "enabled": True, "required": True},
            {"name": "request_kind", "type": "select", "enabled": True,
             "label": "咨询类型", "options": choices},
        ]})
        editor = SimpleNamespace(form_definition=record)
        specs = LeadFormEditorForm._resolved_field_specs(editor)
        custom_fields = LeadFormEditorForm._stored_custom_field_specs(editor)
        custom = next((item for item in custom_fields if item["name"] == "request_kind"), None)
        self.assertIsNotNone(custom)
        values = {}
        for spec in specs:
            values[f"field_{spec['name']}_enabled"] = spec["enabled"]
            values[f"field_{spec['name']}_required"] = spec["required"]
        values["custom_fields_json"] = custom_fields
        saved = LeadFormEditorForm.cleaned_form_schema(SimpleNamespace(field_specs=specs, cleaned_data=values))
        custom = next(field for field in saved["fields"] if field["name"] == "request_kind")
        self.assertEqual(custom["options"], choices)

    def test_contact_only_form_can_disable_name(self):
        specs = LeadFormEditorForm._resolved_field_specs(SimpleNamespace(form_definition=None))
        values = {"field_email_enabled": True, "field_email_required": True}
        saved = LeadFormEditorForm.cleaned_form_schema(SimpleNamespace(field_specs=specs, cleaned_data=values))
        self.assertFalse(next(field for field in saved["fields"] if field["name"] == "full_name")["enabled"])
