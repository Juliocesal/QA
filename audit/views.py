from datetime import date

from django.conf import settings
from django.contrib.sessions.models import Session
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .sap import SapError, get_sap_rows, normalize_stock_id

AUDIT_VERSION = 5


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
        elif action == "scan":
            message, flash = register_scan(request)
        elif action == "reset":
            message = reset_audit(request)

    rows = request.session.get("audit_rows", [])
    total_units = sum(row["expected_qty"] for row in rows)
    scanned_units = sum(row["scanned_qty"] for row in rows)
    today = date.today().isoformat()
    context = {
        "rows": rows,
        "total_units": total_units,
        "scanned_units": scanned_units,
        "pending_units": total_units - scanned_units,
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
                "completed_rows": context["completed_rows"],
                "rows": [
                    {
                        "scanned_qty": row["scanned_qty"],
                        "pending_qty": row["pending_qty"],
                        "status": row["status"],
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
        if data.get("audit_version") == AUDIT_VERSION and data.get("audit_rows"):
            for key in ("audit_rows", "audit_version", "from_date", "to_date"):
                request.session[key] = data.get(key)
            request.session.modified = True
            return


def start_audit(request):
    try:
        from_date = date.fromisoformat(request.POST["from_date"])
        to_date = date.fromisoformat(request.POST["to_date"])
    except (KeyError, ValueError):
        return "Selecciona fechas válidas."

    if from_date > to_date:
        return "La fecha inicial no puede ser posterior a la fecha final."

    try:
        sap_rows = get_sap_rows(from_date, to_date)
    except SapError as exc:
        return f"Error de SAP: {exc}"

    audit_rows = group_sap_rows(sap_rows)
    request.session["audit_rows"] = audit_rows
    request.session["from_date"] = from_date.isoformat()
    request.session["to_date"] = to_date.isoformat()
    request.session["audit_version"] = AUDIT_VERSION
    request.session.modified = True
    units = sum(row["expected_qty"] for row in audit_rows)
    blocked = sum(row["mapping_status"] != "VÁLIDO" for row in audit_rows)
    message = f"Auditoría iniciada con {len(audit_rows)} materiales/variantes y {units} unidades."
    if blocked:
        message += f" {blocked} combinaciones quedaron bloqueadas por el mapeo de MEAN."
    return message


def register_scan(request):
    scanned = normalize_stock_id(request.POST.get("stock_id", ""))
    if not scanned:
        return "ERROR — Escanea o escribe un Stock ID.", "error"
    if not scanned.isascii() or not scanned.isdigit():
        return "ERROR — El Stock ID sólo puede contener números.", "error"

    rows = request.session.get("audit_rows", [])
    if not rows:
        return "ERROR — Primero inicia una auditoría.", "error"

    for row in rows:
        matches = any(
            normalize_stock_id(value).casefold() == scanned.casefold()
            for value in row["stock_ids"]
        )
        if not matches:
            continue
        if row["mapping_status"] != "VÁLIDO":
            return f"ERROR — Stock ID {scanned} bloqueado: {row['mapping_status']}.", "error"
        if row["scanned_qty"] < row["expected_qty"]:
            row["scanned_qty"] += 1
            refresh_status(row)
            request.session["audit_rows"] = rows
            request.session.modified = True
            return (
                f"ACEPTADO — {row['material']} — "
                f"{row['scanned_qty']}/{row['expected_qty']}",
                "success",
            )
        return (
            f"EXCEDENTE — {row['material']} — SAP esperaba "
            f"{row['expected_qty']} y ya se escanearon {row['scanned_qty']}. "
            "Esta pieza no se sumó.",
            "warning",
        )

    return f"ERROR — El Stock ID {scanned} no aparece en MEAN para esta auditoría.", "error"


def reset_audit(request):
    for key in ("audit_rows", "audit_version", "from_date", "to_date"):
        request.session.pop(key, None)
    request.session.modified = True
    return "Auditoría reiniciada."


def group_sap_rows(sap_rows):
    grouped = {}
    for sap_row in sap_rows:
        key = (
            sap_row["material"],
            sap_row["grid_value"],
            sap_row["stock_category"],
        )
        row = grouped.setdefault(
            key,
            {
                "material": sap_row["material"],
                "grid_value": sap_row["grid_value"],
                "stock_category": sap_row["stock_category"],
                "stock_ids": [],
                "users": [],
                "expected_qty": 0,
                "scanned_qty": 0,
            },
        )
        row["expected_qty"] += sap_row["quantity"]
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
            owners.setdefault(row["stock_ids"][0], []).append(row)
    for matching_rows in owners.values():
        if len(matching_rows) > 1:
            for row in matching_rows:
                row["mapping_status"] = "STOCK ID DUPLICADO"

    for row in grouped.values():
        refresh_status(row)
    return list(grouped.values())


def append_unique(target, values):
    target.extend(value for value in values if value and value not in target)


def refresh_status(row):
    row["pending_qty"] = max(row["expected_qty"] - row["scanned_qty"], 0)
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
