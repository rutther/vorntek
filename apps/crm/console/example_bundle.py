"""Optional, explicitly synthetic application examples; never production clones."""
import hashlib
import json
from pathlib import Path
from datetime import timedelta
from decimal import Decimal
import uuid
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from console.audit import record_audit
from console.flow_workspace_service import create_flow
from console.models import ExampleDataBundle
from leads.crm_services import (cancel_task, change_opportunity_stage,
    complete_task, convert_qualified_lead, create_task, normalize_company_name, normalize_email,
    synchronize_submission_next_follow_up)
from leads.form_schema import effective_form_fields
from leads.lead_workflow_services import ActivityInput, QualificationInput, dispose_lead, move_lead_to_trash
from leads.models import (Activity, Company, CompanyContactPoint, CompanyPoolState, ConsentRecord,
    Contact, CustomerSource, LeadConversion, LeadFormDefinition, LeadSubmission, Opportunity,
    OpportunityStageHistory, SalesTeam, SalesTeamMember, Task)
from marketing.models import (MarketingIntegration, MarketingLoop, MarketingLoopAssetBinding,
    MarketingLoopEdge, MarketingLoopNode, MarketingLoopTask, MarketingProvider)
from sitecore.models import Article, Category, FreePage, PageRoute, Site, SiteLocale


DATA_FILE = Path(__file__).parent / 'data/general_examples.json'
MODULES = ('forms', 'organizations', 'contacts', 'inquiries', 'opportunities', 'tasks', 'flows', 'articles', 'pages')


def load_bundle():
    payload = DATA_FILE.read_bytes()
    data = json.loads(payload)
    if data.get('format') != 'vorntek-example-bundle-v1' or data.get('pack') != 'vorntek-general-v1':
        raise ValidationError('Unsupported example bundle.')
    for section in MODULES:
        rows = data.get(section)
        if not isinstance(rows, list) or not rows or len({row['key'] for row in rows}) != len(rows):
            raise ValidationError(f'Invalid example section: {section}.')
    def check_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if any(word in key.lower() for word in ('password', 'secret', 'token', 'credential')):
                    raise ValidationError('Credentials are forbidden in example data.')
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)
    check_keys(data)
    for row in data['contacts']:
        email = row.get('email', '')
        if email.count('@') != 1 or not email.lower().endswith('@example.invalid') or row.get('phone'):
            raise ValidationError('Examples must use reserved, non-deliverable contact routes.')
    for row in data['organizations']:
        url = urlsplit(row.get('website', ''))
        host = (url.hostname or '').lower()
        if (url.scheme != 'https' or url.username or url.password
                or not (host == 'example.invalid' or host.endswith('.example.invalid'))):
            raise ValidationError('Example websites must use reserved origins.')
    return data, hashlib.sha256(payload).hexdigest()


OWNED_MODELS = (SalesTeam, SalesTeamMember, SiteLocale, LeadFormDefinition, Company, Contact,
    LeadSubmission, ConsentRecord, CompanyPoolState, CompanyContactPoint, CustomerSource,
    LeadConversion, Opportunity, OpportunityStageHistory, Activity, Task, MarketingLoop,
    MarketingLoopNode, MarketingLoopEdge, MarketingLoopTask, MarketingLoopAssetBinding,
    Category, PageRoute, Article, FreePage)


def _fingerprint(obj):
    values = type(obj).objects.filter(pk=obj.pk).values().get()
    return hashlib.sha256(json.dumps(values, cls=DjangoJSONEncoder, sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _silent():
    if (settings.NEWCROWN_ALLOW_EXTERNAL_IO or settings.NEWCROWN_RUN_SCHEDULED_TASKS
            or settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND):
        raise CommandError('External delivery and scheduled tasks must remain disabled.')


def _check_physical_references(by_model, present):
    """Include real FK consumers not represented by an installed Django model."""
    owned = {model._meta.db_table:(model, ids) for model, ids in by_model.items()}
    references = []
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute('''SELECT ns.nspname, child.relname, ca.attname, parent.relname, pa.attname
                FROM pg_constraint c
                JOIN pg_class child ON child.oid = c.conrelid
                JOIN pg_namespace ns ON ns.oid = child.relnamespace
                JOIN pg_class parent ON parent.oid = c.confrelid
                JOIN pg_namespace pn ON pn.oid = parent.relnamespace
                JOIN LATERAL unnest(c.conkey, c.confkey) AS cols(child_key, parent_key) ON true
                JOIN pg_attribute ca ON ca.attrelid = child.oid AND ca.attnum = cols.child_key
                JOIN pg_attribute pa ON pa.attrelid = parent.oid AND pa.attnum = cols.parent_key
                WHERE c.contype = 'f' AND pn.nspname = current_schema() AND parent.relname = ANY(%s)''',
                [list(owned)])
            references = cursor.fetchall()
        else:
            for table in present:
                for info in connection.introspection.get_constraints(cursor, table).values():
                    foreign = info.get('foreign_key')
                    if foreign and foreign[0] in owned:
                        for column in info['columns']:
                            references.append((None, table, column, foreign[0], foreign[1]))
        quote = connection.ops.quote_name
        for schema, table, column, parent, parent_column in references:
            model, ids = owned[parent]
            if parent_column != model._meta.pk.column:
                continue
            target = f'{quote(schema)}.{quote(table)}' if schema else quote(table)
            placeholders = ','.join(['%s'] * len(ids))
            sql = f'SELECT 1 FROM {target} WHERE {quote(column)} IN ({placeholders})'
            params = list(ids)
            if table in owned:
                child, child_ids = owned[table]
                sql += f' AND {quote(child._meta.pk.column)} NOT IN ({",".join(["%s"] * len(child_ids))})'
                params.extend(child_ids)
            cursor.execute(sql+' LIMIT 1', params)
            if cursor.fetchone():
                raise CommandError(f'Examples are referenced by non-example {table}; reset refused.')


def _reset(bundle, actor):
    allowed = {model._meta.label_lower:model for model in OWNED_MODELS}
    manifest = bundle.manifest_json
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise CommandError('Unsupported example ownership manifest; no rows were deleted.')
    resources, by_model = {}, {}
    for entry in manifest.get('resources', []):
        model = allowed.get(entry.get('model'))
        if model is None:
            raise CommandError('Unknown example resource; no rows were deleted.')
        obj = model.objects.select_for_update().filter(pk=entry['pk']).first()
        if obj is None or _fingerprint(obj) != entry.get('fingerprint'):
            raise CommandError(f'Example resource missing or modified: {model._meta.label}; reset refused.')
        key = (model._meta.label_lower, str(obj.pk))
        if key in resources:
            raise CommandError('Duplicate ownership entry; reset refused.')
        resources[key] = obj
        by_model.setdefault(type(obj), []).append(obj.pk)
    if not resources:
        raise CommandError('Empty ownership manifest; reset refused.')

    # Check all installed FK consumers, not merely the models seeded here.
    # A reset must not modify an example already adopted into later work.
    models = list(apps.get_models(include_auto_created=True))
    present = set(connection.introspection.table_names())
    for child in models:
        if child._meta.proxy or child._meta.db_table not in present:
            continue
        for field in child._meta.concrete_fields:
            target = field.remote_field.model if field.is_relation else None
            if target not in by_model:
                continue
            referenced = child.objects.filter(**{field.attname+'__in':by_model[target]})
            if child in by_model:
                referenced = referenced.exclude(pk__in=by_model[child])
            if referenced.exists():
                raise CommandError(f'Examples are referenced by non-example {child._meta.label}; reset refused.')
    _check_physical_references(by_model, present)

    # CRM timelines are append-only even for synthetic data. Reset is therefore
    # an in-place refresh of pristine scenario dates, not an uninstall/purge.
    # Identities, configured values and immutable history are retained. All
    # later edits/references were refused above, before the first update.
    permitted_dates = {('leads.leadsubmission','submitted_at'), ('leads.task','due_at')}
    dates = []
    for item in manifest.get('relative_dates', []):
        key = (item.get('model'), str(item.get('pk')))
        offset = item.get('days')
        if (key not in resources or (key[0], item.get('field')) not in permitted_dates
                or not isinstance(offset, int) or isinstance(offset, bool) or not -365 <= offset <= 365):
            raise CommandError('Invalid relative date ownership; reset refused.')
        dates.append((resources[key], item['field'], offset))
    now = timezone.now()
    for obj, field, offset in dates:
        setattr(obj, field, now + timedelta(days=offset))
        obj.save(update_fields=[field, 'updated_at'])
    _sync_next_steps(resources.values())
    manifest['resources'] = [{'model':key[0], 'pk':key[1], 'fingerprint':_fingerprint(obj)}
                             for key, obj in sorted(resources.items())]
    manifest['reset_count'] = int(manifest.get('reset_count', 0)) + 1
    bundle.manifest_json = manifest
    bundle.save(update_fields=['manifest_json', 'updated_at'])
    record_audit(actor=actor, action='example_bundle_reset', entity_table=bundle._meta.db_table,
        entity_id=bundle.pk, after={'pack':bundle.pack_key, 'relative_dates':len(dates),
                                   'timelines_retained':True, 'reset_count':manifest['reset_count']})
    return {'result':'RESET', 'resources':len(resources), 'timelines_retained':True}


def _sync_next_steps(resources):
    for obj in resources:
        if isinstance(obj, LeadSubmission):
            synchronize_submission_next_follow_up(submission_id=obj.pk)
        elif isinstance(obj, Opportunity):
            task = Task.objects.filter(opportunity=obj, status__in={'open','in_progress'}).order_by('due_at','pk').first()
            obj.next_step = task.title if task else ''
            obj.next_follow_up_at = task.due_at if task else None
            obj.save(update_fields=['next_step', 'next_follow_up_at', 'updated_at'])


def _create_content(data, site, add):
    locales = {}
    for code in {row['locale'] for row in data['articles'] + data['pages']}:
        locale = SiteLocale.objects.filter(site=site, locale_code=code).first()
        if locale is None:
            locale = add(SiteLocale, site=site, locale_code=code, label=code)
        locales[code] = locale
    categories = {code:add(Category, site=site, locale=locale, code='demo_examples',
        name='虚构示例 / Synthetic examples', slug='demo-examples', status='draft')
        for code, locale in locales.items()}
    for row in data['articles']:
        route = add(PageRoute, site=site, locale=locales[row['locale']], path=f'/{row["locale"]}/articles/{row["slug"]}/',
            route_type='article', page_title=row['title'], status='draft', robots='noindex,nofollow')
        add(Article, route=route, category=categories[row['locale']], slug=row['slug'],
            title=row['title'], body_markdown=row['body'], excerpt=data['notice'], status='draft',
            config_json={'synthetic_pack':data['pack'], 'contentKey':row['slug'].replace('-', '_'),
                         'publicSlug':row['slug']})
    for row in data['pages']:
        route = add(PageRoute, site=site, locale=locales[row['locale']], path=row['path'],
            route_type='free_page', page_title=row['title'], status='draft', robots='noindex,nofollow')
        add(FreePage, route=route, template_key='standard', title=row['title'], summary=row['summary'],
            hero_json={'title':row['title'], 'summary':row['summary']}, config_json={'synthetic_pack':data['pack']})


def _create_examples(data, site, actor):
    created, relative_dates = {}, []
    def remember(obj):
        created[(obj._meta.label_lower, str(obj.pk))] = obj
        return obj
    def add(model, **values):
        return remember(model.objects.create(**values))
    now = timezone.now()
    team = add(SalesTeam, site=site, code='demo_examples', name='虚构示例协作组')
    add(SalesTeamMember, team=team, user=actor, membership_role='member')
    forms = {row['key']:add(LeadFormDefinition, site=site, code=row['code'], name=row['name'],
        category='general', form_schema={'fields':row['fields']}, capi_enabled=False, notify_emails='',
        config_json={'synthetic_pack':data['pack']}) for row in data['forms']}
    for form in forms.values():
        effective_form_fields(form)
    organizations = {row['key']:add(Company, site=site, owner_user=actor, team=team, name=row['name'],
        normalized_name=normalize_company_name(row['name']), country=row['country'], website=row['website'],
        source_channel='other', notes=data['notice']) for row in data['organizations']}
    contacts = {row['key']:add(Contact, site=site, company=organizations.get(row['organization']),
        owner_user=actor, team=team, full_name=row['name'], email=row['email'],
        email_normalized=normalize_email(row['email']), notes=data['notice']) for row in data['contacts']}
    inquiries = {}
    for row in data['inquiries']:
        form, contact = forms[row['form']], contacts[row['contact']]
        fields = effective_form_fields(form)
        source = {'full_name':contact.full_name, 'email':contact.email,
                  'company':contact.company.name if contact.company_id else '',
                  'country':contact.company.country if contact.company_id else '', 'message':row['message']}
        core = {name:value for name, value in source.items() if fields.get(name, {}).get('enabled')}
        lead = add(LeadSubmission, site=site, form=form, assignee=actor, team=team, **core,
            payload_json=row['payload'], source_url=site.base_url.rstrip('/')+'/contact/',
            source_channel='website', source_detail=data['notice'], submitted_at=now-timedelta(days=row['age_days']),
            stage_updated_at=now, notification_status='disabled', consent_json={'contact':True, 'privacy_notice':True,
            'marketing':False}, config_json={'synthetic_pack':data['pack'], 'example_key':row['key']})
        relative_dates.append({'model':lead._meta.label_lower, 'pk':str(lead.pk),
                               'field':'submitted_at', 'days':-row['age_days']})
        for purpose, decision in [('contact','granted'), ('privacy_notice','granted'), ('marketing','denied')]:
            add(ConsentRecord, submission=lead, actor_user=actor, purpose=purpose, decision=decision,
                source='synthetic_example', policy_version='synthetic-only', evidence_json={'notice':data['notice']})
        if row['stage'] != 'new':
            result = dispose_lead(submission_id=lead.pk, actor=actor, expected_updated_at=lead.updated_at,
                stage=row['stage'], qualification=QualificationInput(contactable=True, project_confirmed=True,
                technical_fit=True, next_step_confirmed=True, notes=data['notice']),
                activity=ActivityInput(activity_type='note', direction='internal',
                    subject='虚构示例阶段', body=data['notice']))
            lead = result.submission
        if row.get('trashed'):
            lead = move_lead_to_trash(submission_id=lead.pk, actor=actor,
                                     reason=data['notice'], clock=lambda:now).submission
        inquiries[row['key']] = lead
    opportunities = {}
    stages = ['discovery', 'solution', 'quotation', 'negotiation']
    for row in data['opportunities']:
        result = convert_qualified_lead(submission_id=inquiries[row['inquiry']].pk, actor=actor,
                                        opportunity_name=row['name'])
        opportunity = result.opportunity
        opportunity.value_amount = Decimal(row['amount'])
        opportunity.save(update_fields=['value_amount', 'updated_at'])
        if row['stage'] == 'won':
            targets = stages + ['won']
        else:
            targets = stages[:stages.index(row['stage'])+1] if row['stage'] in stages else [row['stage']]
        for stage in targets:
            change_opportunity_stage(opportunity_id=opportunity.pk, stage=stage, actor=actor, reason=data['notice'])
        opportunity.refresh_from_db()
        opportunities[row['key']] = remember(opportunity)
    for row in data['tasks']:
        lead = inquiries[row['inquiry']]
        opportunity = opportunities.get(row.get('opportunity'))
        task = create_task(site=site, owner_user=actor, team=team, submission=lead,
            company=opportunity.company if opportunity else None,
            contact=opportunity.primary_contact if opportunity else None, opportunity=opportunity,
            created_by_user=actor, title=row['title'], task_type=row['type'],
            due_at=now+timedelta(days=row['days']), description=data['notice'])
        if row['status'] in {'completed','canceled'}:
            action = complete_task if row['status'] == 'completed' else cancel_task
            task = action(task_id=task.pk, actor=actor, outcome=row['outcome'])
        elif row['status'] == 'in_progress':
            task.status = 'in_progress'
            task.save(update_fields=['status', 'updated_at'])
        if row['status'] in {'open','in_progress'}:
            relative_dates.append({'model':task._meta.label_lower, 'pk':str(task.pk),
                                   'field':'due_at', 'days':row['days']})
        remember(task)
    loops = []
    for row in data['flows']:
        loops.append(create_flow(site=site, actor=actor, data={'requestId':str(uuid.uuid4()),
            'name':row['name'], 'environment':row['environment'], 'note':data['notice']}))
    _create_content(data, site, add)
    for model, query in [
        (CompanyPoolState, Q(company__in=organizations.values())),
        (CompanyContactPoint, Q(company__in=organizations.values())),
        (CustomerSource, Q(company__in=organizations.values())),
        (LeadConversion, Q(submission__in=inquiries.values())),
        (Activity, Q(submission__in=inquiries.values()) | Q(opportunity__in=opportunities.values())),
        (OpportunityStageHistory, Q(opportunity__in=opportunities.values())),
        (MarketingLoop, Q(pk__in=[loop.pk for loop in loops])),
        (MarketingLoopNode, Q(loop__in=loops)), (MarketingLoopEdge, Q(loop__in=loops)),
        (MarketingLoopTask, Q(loop__in=loops)), (MarketingLoopAssetBinding, Q(loop__in=loops)),
    ]:
        for obj in model.objects.filter(query):
            remember(obj)
    _sync_next_steps(created.values())
    return ([{'model':key[0], 'pk':key[1], 'fingerprint':_fingerprint(obj)}
             for key, obj in sorted(created.items())], relative_dates)


@transaction.atomic
def apply_bundle(*, site_code, owner_name, reset=False):
    _silent()
    data, digest = load_bundle()
    site = Site.objects.select_for_update().filter(code=site_code, enabled=True).first()
    if site is None:
        raise CommandError('An existing enabled site is required.')
    actor = get_user_model().objects.filter(username=owner_name, is_active=True).first()
    if actor is None:
        raise CommandError('An existing active owner account is required.')
    from console.access import SYSTEM_ROLES, user_has_any_role
    if not user_has_any_role(actor, SYSTEM_ROLES):
        raise CommandError('Example operations require an existing system administrator.')
    if MarketingIntegration.objects.filter(site=site, enabled=True).exists():
        raise CommandError('Site integrations must remain disabled for synthetic examples.')
    bundle = ExampleDataBundle.objects.select_for_update().filter(site=site, pack_key=data['pack']).first()
    if reset:
        if bundle is None or bundle.state == 'reset':
            return {'result':'ALREADY RESET', 'resources':0}
        return _reset(bundle, actor)
    if bundle is not None and bundle.state == 'active':
        if bundle.source_digest != digest:
            raise CommandError('An installed example version differs; no data was overwritten.')
        return {'result':'ALREADY INSTALLED', 'resources':len(bundle.manifest_json['resources'])}
    if not MarketingProvider.objects.filter(code='meta', enabled=True).exists():
        raise CommandError('The Meta software provider must be available; no live integration is activated.')
    if (LeadFormDefinition.objects.filter(site=site, code__in=[r['code'] for r in data['forms']]).exists()
            or SalesTeam.objects.filter(site=site, code='demo_examples').exists()
            or Company.objects.filter(site=site, normalized_name__in=[normalize_company_name(r['name'])
                for r in data['organizations']]).exists()
            or PageRoute.objects.filter(site=site, path__in=[r['path'] for r in data['pages']]).exists()
            or Category.objects.filter(site=site, code='demo_examples').exists()):
        raise CommandError('Example identities conflict with untracked existing data; no data was overwritten.')
    try:
        resources, relative_dates = _create_examples(data, site, actor)
    except ValidationError as exc:
        raise CommandError('; '.join(exc.messages)) from exc
    if bundle is None:
        bundle = ExampleDataBundle(site=site, pack_key=data['pack'])
    bundle.source_digest, bundle.state = digest, 'active'
    bundle.manifest_json = {'version':1, 'resources':resources, 'relative_dates':relative_dates,
                            'reset_count':0, 'notice':data['notice']}
    bundle.save()
    record_audit(actor=actor, action='example_bundle_imported', entity_table=bundle._meta.db_table,
        entity_id=bundle.pk, after={'pack':bundle.pack_key, 'resources':len(resources)})
    return {'result':'IMPORTED', 'resources':len(resources)}
