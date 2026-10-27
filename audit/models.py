from django.db import models


class AuditReport(models.Model):
    IN_PROGRESS = "EN_CURSO"
    FINISHED = "FINALIZADA"
    CANCELED = "CANCELADA"
    STATUS_CHOICES = [
        (IN_PROGRESS, "En curso"),
        (FINISHED, "Finalizada"),
        (CANCELED, "Cancelada"),
    ]

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    from_date = models.DateField()
    to_date = models.DateField()
    source_shift = models.CharField(max_length=10, blank=True, default="")
    review_shift = models.CharField(max_length=10, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=IN_PROGRESS)
    rows_data = models.JSONField(default=list)
    total_units = models.PositiveIntegerField(default=0)
    scanned_units = models.PositiveIntegerField(default=0)
    missing_units = models.PositiveIntegerField(default=0)
    excess_units = models.PositiveIntegerField(default=0)
    blocked_rows = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]
