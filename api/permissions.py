# -*- coding: utf-8 -*-
from rest_framework.permissions import BasePermission, DjangoModelPermissions


class BabyBuddyDjangoModelPermissions(DjangoModelPermissions):
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": ["%(app_label)s.add_%(model_name)s"],
        "HEAD": [],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        # 'PUT': ['%(app_label)s.change_%(model_name)s'],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }


class TrashRestorePermission(BasePermission):
    """
    Restoring an entry undoes its deletion, so it rides on the same
    permission as deleting that kind of entry -- not on the add permission a
    plain POST would otherwise require.
    """

    def has_permission(self, request, view):
        model = view.queryset.model
        return request.user.has_perm(
            "{}.delete_{}".format(model._meta.app_label, model._meta.model_name)
        )
