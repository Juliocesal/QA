import subprocess
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


class SapError(Exception):
    pass


def get_sap_rows(from_date, to_date, source_shift="ALL"):
    script = Path(__file__).with_name("sap_export.vbs")
    command = [
        "cscript.exe",
        "//Nologo",
        str(script),
        from_date.strftime("%Y%m%d"),
        to_date.strftime("%Y%m%d"),
        source_shift,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise SapError("SAP tardó más de 5 minutos en responder.") from exc
    except OSError as exc:
        raise SapError(f"No se pudo ejecutar la automatización de SAP: {exc}") from exc

    if result.returncode or result.stderr.strip():
        detail = result.stderr.strip() or result.stdout.strip()
        raise SapError(detail or "La automatización de SAP terminó con error.")

    rows = []
    stock_ids = defaultdict(list)
    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        parts = [value.strip() for value in line.split("\t")]
        if parts[0] == "ERROR":
            raise SapError("\t".join(parts[1:]) or "La automatización SAP reportó un error.")
        if parts[0] == "LTAP" and len(parts) in (6, 7):
            if len(parts) == 7:
                _, material, grid_value, stock_category, user, quantity, confirmation_time = parts
            else:
                _, material, grid_value, stock_category, user, quantity = parts
                confirmation_time = ""
            rows.append(
                {
                    "material": material,
                    "grid_value": grid_value,
                    "stock_category": stock_category,
                    "user": user,
                    "quantity": parse_quantity(quantity),
                    "confirmation_time": normalize_confirmation_time(confirmation_time),
                }
            )
        elif len(parts) == 5 and parts[0] == "MEAN":
            _, material, grid_value, category_value, stock_id = parts
            stock_ids[(material, grid_value, category_value.upper())].append(
                normalize_stock_id(stock_id)
            )
        else:
            raise SapError(f"SAP devolvió una fila inesperada: {line}")

    for row in rows:
        key = (
            row["material"],
            row["grid_value"],
            stock_category_code(row["stock_category"]),
        )
        row["stock_ids"] = list(dict.fromkeys(stock_ids[key]))
    return rows


def normalize_stock_id(value):
    value = value.strip()
    return f"00{value}" if value.startswith("200") else value


def normalize_confirmation_time(value):
    digits = re.sub(r"\D", "", value)
    if len(digits) == 6:
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:]}"
    if len(digits) == 4:
        return f"{digits[:2]}:{digits[2:]}:00"
    return value.strip()


def stock_category_code(value):
    match = re.match(r"[A-Za-z]+", value)
    return match.group(0).upper() if match else ""


def parse_quantity(value):
    try:
        quantity = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise SapError(f"SAP devolvió una cantidad inválida: {value}") from exc
    if quantity != quantity.to_integral_value() or quantity < 0:
        raise SapError(f"SAP devolvió una cantidad no auditable: {value}")
    return int(quantity)
