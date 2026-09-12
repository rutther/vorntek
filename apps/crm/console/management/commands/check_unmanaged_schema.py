from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


TEXT_TYPES = {'CharField', 'TextField', 'SlugField', 'EmailField', 'URLField'}
INTEGER_TYPES = {'AutoField', 'IntegerField', 'PositiveIntegerField', 'PositiveSmallIntegerField'}
BIGINT_TYPES = {'BigAutoField', 'BigIntegerField', 'PositiveBigIntegerField'}


def field_type_family(internal_type: str) -> str:
    if internal_type in TEXT_TYPES:
        return 'text'
    if internal_type in INTEGER_TYPES:
        return 'integer'
    if internal_type in BIGINT_TYPES:
        return 'bigint'
    return internal_type


def concrete_type(field) -> str:
    target = field.target_field if field.is_relation else field
    return field_type_family(target.get_internal_type())


class Command(BaseCommand):
    help = 'Verify every unmanaged Django model against the active PostgreSQL schema.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        alias = options['database']
        connection = connections[alias]
        if connection.vendor != 'postgresql':
            raise CommandError('check_unmanaged_schema requires PostgreSQL.')

        errors: list[str] = []
        checked_models = 0
        checked_fields = 0
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))
            for model in apps.get_models():
                if model._meta.managed or model._meta.proxy:
                    continue

                table = model._meta.db_table
                checked_models += 1
                if table not in table_names:
                    errors.append(f'{model._meta.label}: missing table {table}')
                    continue

                description = {
                    column.name: column
                    for column in connection.introspection.get_table_description(cursor, table)
                }
                expected_fields = {
                    field.column: field
                    for field in model._meta.local_fields
                    if field.concrete and not field.many_to_many
                }
                checked_fields += len(expected_fields)

                missing = sorted(set(expected_fields) - set(description))
                unexpected = sorted(set(description) - set(expected_fields))
                if missing:
                    errors.append(f'{model._meta.label}: missing columns {missing}')
                if unexpected:
                    errors.append(f'{model._meta.label}: unmapped columns {unexpected}')

                for column_name, field in expected_fields.items():
                    column = description.get(column_name)
                    if column is None:
                        continue
                    db_type = field_type_family(
                        connection.introspection.get_field_type(column.type_code, column)
                    )
                    model_type = concrete_type(field)
                    if db_type != model_type:
                        errors.append(
                            f'{model._meta.label}.{field.name}: model={model_type}, database={db_type}'
                        )
                    if bool(field.null) != bool(column.null_ok):
                        errors.append(
                            f'{model._meta.label}.{field.name}: '
                            f'model null={field.null}, database null={column.null_ok}'
                        )
                    target = field.target_field if field.is_relation else field
                    if target.get_internal_type() == 'DecimalField':
                        if column.precision != target.max_digits or column.scale != target.decimal_places:
                            errors.append(
                                f'{model._meta.label}.{field.name}: model numeric '
                                f'({target.max_digits},{target.decimal_places}), database '
                                f'({column.precision},{column.scale})'
                            )

                constraints = connection.introspection.get_constraints(cursor, table)
                foreign_keys = {
                    constraint['columns'][0]: constraint['foreign_key']
                    for constraint in constraints.values()
                    if constraint.get('foreign_key') and len(constraint.get('columns') or []) == 1
                }
                for field in expected_fields.values():
                    if not field.is_relation or field.many_to_many:
                        continue
                    expected_target = (
                        field.remote_field.model._meta.db_table,
                        field.target_field.column,
                    )
                    if foreign_keys.get(field.column) != expected_target:
                        errors.append(
                            f'{model._meta.label}.{field.name}: model FK={expected_target}, '
                            f'database FK={foreign_keys.get(field.column)}'
                        )

        if errors:
            raise CommandError('Unmanaged schema drift detected:\n- ' + '\n- '.join(errors))

        self.stdout.write(
            self.style.SUCCESS(
                f'Unmanaged schema contract passed: {checked_models} models, {checked_fields} fields.'
            )
        )
