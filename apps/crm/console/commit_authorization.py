"""Fresh role and login-session authority at a protected write boundary."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from console.access import user_has_any_role


class CommitAuthorizationError(PermissionDenied):
    """The authenticated request lost authority before its next write."""


def current_actor_for_commit(actor_id, session_key, roles, *, related_user_ids=()):
    # Non-key actor edits (active/password) still serialize with this check,
    # while ordinary audit/assignee FK references may take KEY SHARE. A full
    # UPDATE lock can deadlock two same-role actors assigning records to each other.
    # Owner changes also reference another user. Lock both before role/business
    # rows, in the same user-id order as the existing system-user editor.
    user_ids = {actor_id, *related_user_ids}
    users = list(get_user_model().objects.select_for_update(no_key=True)
        .filter(pk__in=user_ids).order_by('pk'))
    actor = next((user for user in users if user.pk == actor_id), None)
    if actor is None or not actor.is_active:
        raise CommitAuthorizationError('账号已停用，请重新登录。')
    memberships = list(actor.groups.through.objects.select_for_update().filter(user_id=actor.pk)
        .order_by('pk').values_list('group_id', flat=True))
    list(Group.objects.select_for_update().filter(pk__in=memberships).order_by('pk'))
    session = Session.objects.select_for_update().filter(session_key=session_key, expire_date__gt=timezone.now()).first()
    decoded = session.get_decoded() if session else {}
    if (str(decoded.get('_auth_user_id')) != str(actor.pk)
            or decoded.get('_auth_user_hash') != actor.get_session_auth_hash()
            or not user_has_any_role(actor, roles)):
        raise CommitAuthorizationError('登录或操作权限已变化，请刷新并重新登录。')
    return actor
