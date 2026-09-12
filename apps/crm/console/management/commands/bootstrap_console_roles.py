from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from console.access import GROUP_BY_ROLE, ROLE_CONTENT_OPS, ROLE_SALES, ROLE_SALES_MANAGER
from console.audit import record_audit
from leads.models import SalesTeam, SalesTeamMember
from sitecore.models import Site


class Command(BaseCommand):
    help = 'Create SiteOS console role groups and optionally assign one user.'

    def add_arguments(self, parser):
        parser.add_argument('--user', help='Username to assign.')
        parser.add_argument('--role', choices=sorted(GROUP_BY_ROLE), help='Role key to assign.')
        parser.add_argument('--site-code', help='Site code used for sales team membership.')
        parser.add_argument('--team-code', default='default', help='Sales team code. Defaults to default.')
        parser.add_argument(
            '--keep-existing',
            action='store_true',
            help='Keep other SiteOS role groups already assigned to the user.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        groups = {
            role: Group.objects.get_or_create(name=group_name)[0]
            for role, group_name in GROUP_BY_ROLE.items()
        }
        self.stdout.write(self.style.SUCCESS(f'Role groups ready: {len(groups)}'))

        username = (options.get('user') or '').strip()
        role = (options.get('role') or '').strip()
        if not username and not role:
            return
        if not username or not role:
            raise CommandError('--user and --role must be supplied together.')

        user = get_user_model().objects.filter(username=username).first()
        if not user:
            raise CommandError(f'Unknown user: {username}')

        if not options['keep_existing']:
            user.groups.remove(*groups.values())
        user.groups.add(groups[role])

        if role in {ROLE_SALES, ROLE_SALES_MANAGER}:
            site_code = (options.get('site_code') or '').strip()
            if not site_code:
                raise CommandError('--site-code is required for sales roles.')
            site = Site.objects.filter(code=site_code).first()
            if not site:
                raise CommandError(f'Unknown site: {site_code}')
            team_code = (options.get('team_code') or 'default').strip() or 'default'
            team = SalesTeam.objects.filter(site=site, code=team_code, enabled=True).first()
            if not team:
                raise CommandError(f'Unknown enabled sales team: {site_code}/{team_code}')
            SalesTeamMember.objects.update_or_create(
                team=team,
                user=user,
                defaults={'membership_role': 'manager' if role == ROLE_SALES_MANAGER else 'member'},
            )
        elif role == ROLE_CONTENT_OPS or not options['keep_existing']:
            # A primary non-sales assignment must not retain stale sales-team
            # membership.  content_ops is always cleaned even when an operator
            # intentionally preserves other role groups with --keep-existing.
            SalesTeamMember.objects.filter(user=user).delete()

        record_audit(
            actor='bootstrap_console_roles',
            action='console_role_assigned',
            entity_table='auth_user',
            entity_id=user.id,
            after={'username': user.get_username(), 'role': role},
            source='management_command',
        )

        self.stdout.write(self.style.SUCCESS(f'Assigned {role} to {username}.'))
