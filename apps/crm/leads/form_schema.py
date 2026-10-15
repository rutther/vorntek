"""The field contract shared by configurable website inquiry forms."""
from __future__ import annotations

import re
from types import SimpleNamespace


CORE_FIELDS = frozenset({"full_name", "email", "phone", "company", "country", "message"})
DEFAULT_FIELDS = (
    {"name": "full_name", "type": "text", "label": "姓名", "enabled": True, "required": True},
    {"name": "email", "type": "email", "label": "邮箱", "enabled": True, "required": False},
    {"name": "phone", "type": "tel", "label": "电话", "enabled": True, "required": False},
    {"name": "company", "type": "text", "label": "公司", "enabled": True, "required": False},
    {"name": "country", "type": "text", "label": "国家或地区", "enabled": True, "required": False},
    {"name": "product_category", "type": "select", "label": "需求类别", "enabled": False, "required": False},
    {"name": "capacity", "type": "text", "label": "项目参数", "enabled": False, "required": False},
    {"name": "message", "type": "textarea", "label": "咨询内容", "enabled": True, "required": False},
)
SUPPORTED_TYPES = frozenset({"text", "textarea", "select", "email", "tel", "number", "date"})


class FormSchemaError(ValueError):
    pass


def effective_form_fields(form_definition):
    """Return validated configured fields, or None for a pre-schema legacy form.

    Existing non-general forms without schemas keep their old input contract.
    A configured schema always wins over its historical category label.
    """
    schema = getattr(form_definition, "form_schema", None)
    if schema is None or schema == {}:
        if getattr(form_definition, "category", None) != "general":
            return None
        fields = DEFAULT_FIELDS
    elif not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        raise FormSchemaError("表单字段配置无效，请联系管理员。")
    else:
        fields = schema["fields"]
    if not 1 <= len(fields) <= 64:
        raise FormSchemaError("表单字段配置无效，请联系管理员。")
    result = {}
    for field in fields:
        if not isinstance(field, dict):
            raise FormSchemaError("表单字段配置无效，请联系管理员。")
        name = field.get("name") or field.get("key")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", name) or name in result:
            raise FormSchemaError("表单字段配置无效，请联系管理员。")
        kind = field.get("type", "text")
        enabled, required = field.get("enabled", True), field.get("required", False)
        if (not isinstance(kind, str) or kind not in SUPPORTED_TYPES
                or not isinstance(enabled, bool) or not isinstance(required, bool)):
            raise FormSchemaError("表单字段配置无效，请联系管理员。")
        label = field.get("label") or name
        if not isinstance(label, str) or len(label) > 120:
            raise FormSchemaError("表单字段配置无效，请联系管理员。")
        options = field.get("options", [])
        if not isinstance(options, list) or len(options) > 200:
            raise FormSchemaError("表单选项配置无效，请联系管理员。")
        values = []
        for option in options:
            value = option.get("value") if isinstance(option, dict) else option
            if not isinstance(value, str) or not value.strip() or len(value) > 120 or value in values:
                raise FormSchemaError("表单选项配置无效，请联系管理员。")
            if isinstance(option, dict) and (not isinstance(option.get("label", value), str)
                                             or len(option.get("label", value)) > 120):
                raise FormSchemaError("表单选项配置无效，请联系管理员。")
            values.append(value)
        result[name] = dict(field, name=name, label=label, type=kind, enabled=enabled,
                            required=enabled and required, option_values=tuple(values))
    if not any(result.get(name, {}).get("enabled") for name in ("email", "phone")):
        raise FormSchemaError("表单至少需要启用一种联系方式。")
    return result


def public_form_fields(form_definition):
    """Expose the same enabled/required/choice contract without private settings."""
    rules = effective_form_fields(form_definition)
    if rules is None:
        required = {"full_name", "company", "country", "product_category", "capacity"}
        fields = [dict(field, enabled=True, required=field["name"] in required) for field in DEFAULT_FIELDS]
        if getattr(form_definition, "category", None) == "industrial":
            # Compatibility for existing records only; fresh bootstrap is general.
            from leads.industrial import BUSINESS_LINES
            for field in fields:
                if field["name"] == "capacity":
                    field.update(enabled=False, required=False)
                elif field["name"] == "message":
                    field["required"] = True
            fields.append({"name": "business_line", "type": "select", "label": "业务方向",
                           "enabled": True, "required": True,
                           "options": [{"value": key, "label": label} for key, label in BUSINESS_LINES.items()]})
        rules = effective_form_fields(SimpleNamespace(category="general", form_schema={"fields": fields}))
    return [
        {"name": field["name"], "type": field["type"], "label": field["label"],
         "placeholder": str(field.get("placeholder") or ""), "required": field["required"],
         "options": field.get("options", [])}
        for field in rules.values() if field["enabled"]
    ]


def configured_submission_fields(submission):
    """Project saved values through the configured field contract, not an industry.

    None identifies an explicit pre-schema legacy form. Hidden/historical values
    are not deleted; callers can retain their existing raw-record access.
    """
    rules = effective_form_fields(submission.form)
    if rules is None:
        return None
    rows = []
    payload = submission.payload_json or {}
    for name, field in rules.items():
        if not field['enabled']:
            continue
        raw = getattr(submission, name, '') if name in CORE_FIELDS else payload.get(name, '')
        value = '' if raw is None else str(raw).strip()
        labels = {option['value']: option.get('label', option['value'])
                  for option in field.get('options', []) if isinstance(option, dict)}
        rows.append({'name': name, 'label': field['label'], 'value': labels.get(value, value) or '未填写'})
    return rows
