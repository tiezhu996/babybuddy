# -*- coding: utf-8 -*-
from django.urls import path

from . import views

app_name = "summaries"

urlpatterns = [
    path(
        "children/<str:slug>/summaries/",
        views.SummaryList.as_view(),
        name="summary-list",
    ),
    path(
        "children/<str:slug>/summaries/add/",
        views.SummaryCreate.as_view(),
        name="summary-add",
    ),
    path(
        "summaries/<int:pk>/",
        views.SummaryDetail.as_view(),
        name="summary-detail",
    ),
    path(
        "summaries/<int:pk>/changes/",
        views.SummaryCompare.as_view(),
        name="summary-compare",
    ),
    path(
        "summaries/<int:pk>/delete/",
        views.SummaryDelete.as_view(),
        name="summary-delete",
    ),
]
