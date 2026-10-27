from django.urls import path

from .views import delete_report, index, report_detail, report_list

urlpatterns = [
    path("", index, name="audit"),
    path("reportes/", report_list, name="audit-reports"),
    path("reportes/<int:pk>/", report_detail, name="audit-report"),
    path("reportes/<int:pk>/eliminar/", delete_report, name="audit-delete"),
]
