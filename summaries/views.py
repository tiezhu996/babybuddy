# -*- coding: utf-8 -*-
import logging

from django.contrib import messages
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic.detail import DetailView
from django.views.generic.edit import DeleteView, FormView

from babybuddy.mixins import PermissionRequiredMixin
from core.models import Child

from .diff import diff_summary
from .forms import VisitSummaryForm
from .models import VisitSummary
from .snapshot import generate_summary

logger = logging.getLogger(__name__)

VIEW_PERMISSIONS = ("summaries.view_visitsummary", "core.view_child")


class SummaryList(PermissionRequiredMixin, DetailView):
    """All visit summaries of one child."""

    model = Child
    slug_url_kwarg = "slug"
    permission_required = VIEW_PERMISSIONS
    template_name = "summaries/summary_list.html"
    context_object_name = "child"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["summaries"] = self.object.visit_summaries.all()
        return context


class SummaryCreate(PermissionRequiredMixin, FormView):
    """Generate one frozen summary atomically."""

    form_class = VisitSummaryForm
    permission_required = ("summaries.add_visitsummary", "core.view_child")
    template_name = "summaries/summary_form.html"

    def get_child(self):
        # Resolved only after the permission checks in dispatch, so an
        # anonymous request for an unknown slug is redirected to login rather
        # than told whether the slug exists.
        if not hasattr(self, "child_obj"):
            self.child_obj = _get_child_or_404(self.kwargs["slug"])
        return self.child_obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["child"] = self.get_child()
        return context

    def form_valid(self, form):
        try:
            self.summary = generate_summary(
                self.get_child(),
                form.cleaned_data["start_date"],
                form.cleaned_data["end_date"],
                self.request.user,
                title=form.cleaned_data.get("title", ""),
            )
        except Exception:
            # The service rolls its transaction back, so nothing partial can
            # remain; surface the failure as a form error instead of a 500.
            logger.exception("Visit summary generation failed")
            form.add_error(
                None,
                _("The summary could not be generated. No summary was saved."),
            )
            return self.form_invalid(form)
        messages.success(self.request, _("Visit summary generated."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("summaries:summary-detail", args=[self.summary.pk])


class SummaryDetail(PermissionRequiredMixin, DetailView):
    """A frozen summary; only the stored JSON is rendered."""

    model = VisitSummary
    permission_required = VIEW_PERMISSIONS
    template_name = "summaries/summary_detail.html"
    context_object_name = "summary"


class SummaryCompare(PermissionRequiredMixin, DetailView):
    """Live comparison of current records against the frozen summary."""

    model = VisitSummary
    permission_required = VIEW_PERMISSIONS
    template_name = "summaries/summary_compare.html"
    context_object_name = "summary"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["comparison"] = diff_summary(self.object)
        return context


class SummaryDelete(PermissionRequiredMixin, DeleteView):
    model = VisitSummary
    permission_required = ("summaries.delete_visitsummary", "core.view_child")
    template_name = "summaries/summary_confirm_delete.html"
    context_object_name = "summary"

    def get_success_url(self):
        return reverse("summaries:summary-list", args=[self.object.child.slug])


def _get_child_or_404(slug):
    """Fetch the child by slug, raising 404 if it does not exist."""
    from django.shortcuts import get_object_or_404

    return get_object_or_404(Child, slug=slug)
