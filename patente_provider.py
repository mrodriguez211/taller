"""
Búsqueda de datos de vehículo por patente (Chile).

¿De dónde saca la información un sistema de taller?
  - Marca, modelo, año, chasis (VIN), n° motor, color: Registro de Vehículos
    Motorizados (RVM) del Registro Civil. Los sistemas no consultan al Registro
    Civil directo: usan APIs de terceros que agregan esos datos
    (Boostr/Autodata, Verifik, GetAPI, actores de Apify, etc.).
  - Vencimiento de revisión técnica y última lectura de kilometraje: registros
    que las Plantas de Revisión Técnica (PRT) informan al Ministerio de
    Transportes (MTT). Proveedores como Autofact los obtienen de esa fuente.

Este módulo deja el proveedor intercambiable:
  PATENTE_PROVIDER=mock     -> datos de prueba (por defecto, sin internet)
  PATENTE_PROVIDER=verifik  -> API real de Verifik (requiere VERIFIK_TOKEN)
"""
import os
import re
from datetime import date

# Mes de vencimiento de revisión técnica según último dígito de la patente (MTT)
MES_REVISION = {0: 2, 1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10, 8: 11, 9: 1}

# Datos inventados para probar sin conexión
DEMO = {
    "ABCD12": dict(marca="TOYOTA", modelo="YARIS SPORT 1.5", anio=2021, tipo="Turismo / Monovolumen",
                   chasis="DEMO0TOYOTA0000012", motor="2NR1234567", combustible="Gasolina",
                   color="BLANCO", km=48210, rev_tecnica="2027-05-31"),
    "XYZW34": dict(marca="NISSAN", modelo="VERSA CVT 1.6", anio=2023, tipo="Turismo / Monovolumen",
                   chasis="DEMO0NISSAN0000034", motor="HR16-998877", combustible="Gasolina",
                   color="GRIS", km=29280, rev_tecnica="2027-07-31"),
    "KLMN56": dict(marca="CHEVROLET", modelo="SAIL 1.5 LT", anio=2019, tipo="Turismo / Monovolumen",
                   chasis="DEMO0CHEVRO0000056", motor="L2C556677", combustible="Gasolina",
                   color="ROJO", km=102340, rev_tecnica="2027-09-30"),
}


def normalizar(patente: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (patente or "").upper())


def es_patente_valida(p: str) -> bool:
    # Formato nuevo BBBB12 o antiguo AA1234
    return bool(re.fullmatch(r"[A-Z]{4}\d{2}|[A-Z]{2}\d{4}", p))


def vencimiento_estimado(p: str) -> str:
    """Estima el vencimiento según el calendario por último dígito."""
    digito = int(p[-1])
    mes = MES_REVISION[digito]
    hoy = date.today()
    anio = hoy.year if mes >= hoy.month else hoy.year + 1
    # último día del mes
    from calendar import monthrange
    return date(anio, mes, monthrange(anio, mes)[1]).isoformat()


def _mock(p):
    d = DEMO.get(p)
    if not d:
        return None
    return {"patente": p, **d, "fuente": "demo"}


def _verifik(p):
    import requests
    token = os.environ.get("VERIFIK_TOKEN")
    if not token:
        raise RuntimeError("Falta VERIFIK_TOKEN")
    r = requests.get("https://api.verifik.co/v2/cl/vehicle", params={"plate": p},
                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                     timeout=15)
    r.raise_for_status()
    d = r.json().get("data", {})
    return {
        "patente": p,
        "marca": d.get("make") or d.get("brand", ""),
        "modelo": d.get("model", ""),
        "anio": d.get("year"),
        "tipo": d.get("type") or d.get("vehicleType", ""),
        "chasis": d.get("chasisNumber", ""),
        "motor": d.get("engineNumber", ""),
        "color": d.get("color", ""),
        "combustible": d.get("fuel", ""),
        "km": None,                       # esta API no entrega kilometraje
        "rev_tecnica": vencimiento_estimado(p),
        "fuente": "verifik",
    }


def buscar(patente: str):
    p = normalizar(patente)
    if not es_patente_valida(p):
        return {"ok": False, "error": "Formato de patente inválido (ej: ABCD12 o AB1234)"}
    proveedor = os.environ.get("PATENTE_PROVIDER", "mock")
    try:
        data = _verifik(p) if proveedor == "verifik" else _mock(p)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Error consultando proveedor: {e}"}
    if not data:
        return {"ok": False, "error": f"No se encontraron datos para {p}",
                "rev_tecnica_estimada": vencimiento_estimado(p)}
    return {"ok": True, **data}
