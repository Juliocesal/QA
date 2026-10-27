from datetime import datetime, time
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.sessions.models import Session
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AuditReport
from .sap import SapError, get_sap_rows, normalize_stock_id, stock_category_code

AUDIT_VERSION = 5


def normalize_session_date(value):
    if not value:
        return None

    value = str(value).strip()

    for fmt in (
        "%Y-%m-%d",  # 2026-10-21
        "%d.%m.%Y",  # 21.10.2026
        "%d/%m/%Y",  # 21/10/2026
        "%Y%m%d",    # 20261021
        "%m/%d/%Y",  # 10/21/2026
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def index(request):
    recover_audit_session(request)
    message = ""
    flash = ""
    previous_rows = request.session.get("audit_rows", [])
    if previous_rows and request.session.get("audit_version") != AUDIT_VERSION:
        request.session.pop("audit_rows", None)
        message = "La auditoría anterior fue descartada; inicia una nueva."

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "start":
            message = start_audit(request)
            if "ERROR —" in message:
                flash = "error"
        elif action == "scan":
            message, flash = register_scan(request)
        elif action == "reset":
            message = reset_audit(request)
        elif action == "finish":
            report, message = finish_audit(request)
            if report:
                return redirect("audit-report", pk=report.pk)
            if message.startswith("ERROR —"):
                flash = "error"

    rows = ensure_audit_row_fields(request.session.get("audit_rows", []))
    total_units = sum(row["expected_qty"] for row in rows)
    scanned_units = sum(row["scanned_qty"] for row in rows)
    today = timezone.localdate().isoformat()
    context = {
        "rows": rows,
        "total_units": total_units,
        "scanned_units": scanned_units,
        "pending_units": total_units - scanned_units,
        "excess_units": sum(row["excess_qty"] for row in rows),
        "progress_percent": round(scanned_units * 100 / total_units) if total_units else 0,
        "audit_active": bool(rows),
        "completed_rows": sum(row["status"] == "COMPLETO" for row in rows),
        "blocked_rows": sum(row["mapping_status"] != "VÁLIDO" for row in rows),
        "message": message,
        "flash": flash,
        "from_date": request.session.get("from_date", today),
        "to_date": request.session.get("to_date", today),
    }
    if (
        request.POST.get("action") == "scan"
        and request.headers.get("x-requested-with") == "XMLHttpRequest"
    ):
        return JsonResponse(
            {
                "message": message,
                "flash": flash,
                "scanned_units": scanned_units,
                "pending_units": total_units - scanned_units,
                "total_units": total_units,
                "excess_units": context["excess_units"],
                "progress_percent": context["progress_percent"],
                "completed_rows": context["completed_rows"],
                "rows": [
                    {
                        "scanned_qty": row["scanned_qty"],
                        "pending_qty": row["pending_qty"],
                        "excess_qty": row.get("excess_qty", 0),
                        "status": row["status"],
                        "status_indicator": row["status_indicator"],
                    }
                    for row in rows
                ],
            }
        )
    return render(request, "audit/index.html", context)


def recover_audit_session(request):
    if settings.SESSION_COOKIE_NAME in request.COOKIES:
        return

    candidates = []
    legacy_key = request.COOKIES.get("sessionid")
    if legacy_key:
        legacy = Session.objects.filter(
            session_key=legacy_key, expire_date__gt=timezone.now()
        ).first()
        if legacy:
            candidates.append(legacy)

    candidates.extend(
        Session.objects.filter(expire_date__gt=timezone.now()).order_by("-expire_date")[:10]
    )
    for saved_session in dict.fromkeys(candidates):
        data = saved_session.get_decoded()
        if data.get("audit_version") == AUDIT_VERSION and (
            data.get("audit_rows")
        ):
            for key in (
                "audit_rows", "audit_version", "from_date", "to_date",
                "audit_record_id", "audit_started_at", "source_shift", "review_shift",
            ):
                if key in data:
                    request.session[key] = data[key]
            request.session.modified = True
            return


def start_audit(request):
    if not request.POST.get("from_date"):
        return "ERROR — Falta la fecha inicial."
    if not request.POST.get("to_date"):
        return "ERROR — Falta la fecha final."

    try:
        from_date = normalize_session_date(request.POST["from_date"])
        to_date = normalize_session_date(request.POST["to_date"])
        if not from_date or not to_date:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return "ERROR — Selecciona fechas válidas."

    if from_date > to_date:
        return "ERROR — La fecha inicial no puede ser posterior a la fecha final."

    started_at = timezone.now()
    local_start = timezone.localtime(started_at)
    review_shift = "A" if time(6) <= local_start.time() < time(18) else "B"
    source_shift = "B" if review_shift == "A" else "A"

    try:
        sap_rows = get_sap_rows(from_date, to_date, source_shift)
    except SapError as exc:
        return f"ERROR — SAP: {exc}"

    if not sap_rows:
        return "ERROR — SAP no encontró registros con las fechas y el turno seleccionados."

    audit_rows = group_sap_rows(sap_rows)
    if not audit_rows:
        return "ERROR — SAP devolvió datos, pero no encontró materiales auditables."

    clear_active_audit(request)
    request.session["audit_rows"] = audit_rows
    request.session["from_date"] = from_date.isoformat()
    request.session["to_date"] = to_date.isoformat()
    request.session["audit_version"] = AUDIT_VERSION
    request.session["audit_started_at"] = started_at.isoformat()
    request.session["source_shift"] = source_shift
    request.session["review_shift"] = review_shift
    request.session.modified = True
    units = sum(row["expected_qty"] for row in audit_rows)
    blocked = sum(row["mapping_status"] != "VÁLIDO" for row in audit_rows)
    message = (
        f"Auditoría iniciada con {len(audit_rows)} materiales/variantes y {units} unidades. "
        f"El turno {review_shift} revisa las entregas del turno {source_shift}."
    )
    if blocked:
        reasons = ", ".join(sorted({
            row["mapping_status"] for row in audit_rows
            if row["mapping_status"] != "VÁLIDO"
        }))
        message += f" ERROR — {blocked} combinaciones bloqueadas en MEAN: {reasons}. Revisa el estado en la tabla."
    return message


def register_scan(request):
    scanned = normalize_stock_id(request.POST.get("stock_id", ""))
    if not scanned:
        return "RECHAZADO — Escanea o escribe un Stock ID.", "error"
    if not scanned.isascii() or not scanned.isdigit():
        return "RECHAZADO — El Stock ID sólo puede contener números.", "error"

    rows = ensure_audit_row_fields(request.session.get("audit_rows", []))
    if not rows:
        return "RECHAZADO — Primero inicia una auditoría.", "error"
    request.session["audit_rows"] = rows

    matching_rows = [
        row for row in rows
        if any(normalize_stock_id(value).casefold() == scanned.casefold()
               for value in row["stock_ids"])
    ]
    if not matching_rows:
        return f"RECHAZADO — {scanned[-4:]}: el Stock ID no aparece en MEAN para esta auditoría.", "error"

    blocked_row = next(
        (row for row in matching_rows if row["mapping_status"] != "VÁLIDO"), None
    )
    if blocked_row:
        return f"RECHAZADO — {scanned[-4:]}: mapeo bloqueado ({blocked_row['mapping_status']}).", "error"

    available_row = next(
        (row for row in matching_rows if row["scanned_qty"] < row["expected_qty"]), None
    )
    if available_row:
        available_row["scanned_qty"] += 1
        refresh_status(available_row)
        request.session.modified = True
        return (
            f"ACEPTADO — {scanned[-4:]} — "
            f"{available_row['scanned_qty']}/{available_row['expected_qty']}",
            "success",
        )

    excess_row = matching_rows[-1]
    excess_row["excess_qty"] += 1
    request.session.modified = True
    return (
        f"AMARILLO — {scanned[-4:]} — EXCESO: SAP esperaba {excess_row['expected_qty']} "
        f"y ya se escanearon {excess_row['scanned_qty']}. No se sumó.",
        "warning",
    )


def reset_audit(request):
    clear_active_audit(request)
    return "Auditoría reiniciada."


def finish_audit(request):
    rows = ensure_audit_row_fields(request.session.get("audit_rows", []))
    if not rows:
        return None, "ERROR — No hay una auditoría activa para terminar."
    if not any(row["scanned_qty"] or row.get("excess_qty", 0) for row in rows):
        return None, "ERROR — No se guardó: escanea al menos una pieza antes de terminar."

    started_at = datetime.fromisoformat(
        request.session.get("audit_started_at", timezone.now().isoformat())
    )
    report = AuditReport.objects.filter(
        pk=request.session.get("audit_record_id"), status=AuditReport.IN_PROGRESS
    ).first()
    report_values = {
        "started_at": started_at,
        "from_date": normalize_session_date(request.session.get("from_date")) or timezone.localdate(),
        "to_date": normalize_session_date(request.session.get("to_date")) or timezone.localdate(),
        "source_shift": request.session.get("source_shift", ""),
        "review_shift": request.session.get("review_shift", ""),
        "rows_data": rows,
        "status": AuditReport.FINISHED,
        "finished_at": timezone.now(),
        **audit_totals(rows),
    }
    if report:
        for field, value in report_values.items():
            setattr(report, field, value)
        report.save()
    else:
        report = AuditReport.objects.create(**report_values)
    clear_active_audit(request)
    return report, ""


def clear_active_audit(request):
    for key in (
        "audit_rows", "audit_version", "from_date", "to_date", "source_shift", "review_shift",
    ):
        request.session.pop(key, None)
    request.session.pop("audit_record_id", None)
    request.session.pop("audit_started_at", None)
    request.session.modified = True


def audit_totals(rows):
    return {
        "total_units": sum(row["expected_qty"] for row in rows),
        "scanned_units": sum(row["scanned_qty"] for row in rows),
        "missing_units": sum(row["pending_qty"] for row in rows),
        "excess_units": sum(row.get("excess_qty", 0) for row in rows),
        "blocked_rows": sum(row["mapping_status"] != "VÁLIDO" for row in rows),
    }


def ensure_audit_row_fields(rows):
    for row in rows:
        row.setdefault("excess_qty", 0)
        row.setdefault("confirmation_times", [])
        row.setdefault("source_shift", "SIN HORA")
        row.setdefault("review_shift", "POR DEFINIR")
        refresh_status(row)
    return rows


def group_sap_rows(sap_rows):
    grouped = {}
    for sap_row in sap_rows:
        confirmation_time = sap_row.get("confirmation_time", "")
        source_shift = shift_for_confirmation_time(confirmation_time)
        key = (
            sap_row["material"],
            sap_row["grid_value"],
            sap_row["stock_category"],
            source_shift,
        )
        row = grouped.setdefault(
            key,
            {
                "material": sap_row["material"],
                "grid_value": sap_row["grid_value"],
                "stock_category": sap_row["stock_category"],
                "source_shift": source_shift,
                "review_shift": {"A": "B", "B": "A"}.get(source_shift, "POR DEFINIR"),
                "confirmation_times": [],
                "stock_ids": [],
                "users": [],
                "expected_qty": 0,
                "scanned_qty": 0,
                "excess_qty": 0,
            },
        )
        row["expected_qty"] += sap_row["quantity"]
        append_unique(row["confirmation_times"], [confirmation_time])
        append_unique(row["stock_ids"], sap_row["stock_ids"])
        append_unique(row["users"], [sap_row["user"]])

    for row in grouped.values():
        if not row["stock_ids"]:
            row["mapping_status"] = "SIN STOCK ID"
        elif len(row["stock_ids"]) > 1:
            row["mapping_status"] = "STOCK ID AMBIGUO"
        else:
            row["mapping_status"] = "VÁLIDO"

    owners = {}
    for row in grouped.values():
        if row["mapping_status"] == "VÁLIDO":
            identity = (
                row["material"],
                row["grid_value"],
                stock_category_code(row["stock_category"]),
            )
            for stock_id in row["stock_ids"]:
                owners.setdefault(stock_id, set()).add(identity)
    for row in grouped.values():
        if row["mapping_status"] == "VÁLIDO" and any(
            len(owners[stock_id]) > 1 for stock_id in row["stock_ids"]
        ):
            row["mapping_status"] = "STOCK ID DUPLICADO"

    for row in grouped.values():
        refresh_status(row)
    return list(grouped.values())


def append_unique(target, values):
    target.extend(value for value in values if value and value not in target)


def refresh_status(row):
    row["pending_qty"] = max(row["expected_qty"] - row["scanned_qty"], 0)
    if row["expected_qty"] > 0 and row["scanned_qty"] >= row["expected_qty"]:
        row["status_indicator"] = "🟢"
    elif row["scanned_qty"] > 0:
        row["status_indicator"] = "🟡"
    else:
        row["status_indicator"] = "🔴"

    if row["mapping_status"] != "VÁLIDO":
        row["status"] = row["mapping_status"]
    elif row["expected_qty"] <= 0:
        row["status"] = "SIN CANTIDAD"
    elif row["pending_qty"] == 0:
        row["status"] = "COMPLETO"
    elif row["scanned_qty"]:
        row["status"] = "PARCIAL"
    else:
        row["status"] = "PENDIENTE"


def shift_for_confirmation_time(value):
    try:
        confirmed = time.fromisoformat(value)
    except (TypeError, ValueError):
        return "SIN HORA"
    return "A" if time(6) <= confirmed < time(18) else "B"


def report_list(request):
    return render(request, "audit/reports.html", {
        "reports": AuditReport.objects.all(),
    })


def report_detail(request, pk):
    report = get_object_or_404(AuditReport, pk=pk)
    return render(request, "audit/report_detail.html", {
        "report": report,
        "rows": ensure_audit_row_fields(report.rows_data),
        "progress_percent": round(report.scanned_units * 100 / report.total_units)
        if report.total_units else 0,
    })


@require_POST
def delete_report(request, pk):
    delete_password = settings.AUDIT_DELETE_PASSWORD
    if not delete_password:
        messages.error(request, "El borrado está deshabilitado: configura AUDIT_DELETE_PASSWORD en .env.")
    elif not secrets.compare_digest(request.POST.get("password", ""), delete_password):
        messages.error(request, "Contraseña incorrecta. No se borró la auditoría.")
    else:
        report = get_object_or_404(AuditReport, pk=pk)
        report.delete()
        messages.success(request, "Auditoría borrada.")
    return redirect("audit-reports")
