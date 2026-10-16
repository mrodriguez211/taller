"""
Generación de PDF para boletas, presupuestos y órdenes de reparación (fpdf2).

La app arma los datos (filas de cliente/vehículo, grupos de líneas, totales y
secciones de texto) y este módulo solo se encarga de dibujarlos en A4.
"""
from io import BytesIO

from fpdf import FPDF

ANCHO = 186  # ancho útil A4 con márgenes de 12 mm


def _t(s):
    """Las fuentes base de PDF solo admiten latin-1: reemplaza lo que no cabe."""
    return str("" if s is None else s).encode("latin-1", "replace").decode("latin-1")


def _rgb(hexcolor, defecto=(13, 59, 102)):
    try:
        h = hexcolor.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError, IndexError):
        return defecto


def _recortar(pdf, texto, ancho):
    texto = _t(texto)
    if pdf.get_string_width(texto) <= ancho - 2:
        return texto
    while texto and pdf.get_string_width(texto + "...") > ancho - 2:
        texto = texto[:-1]
    return texto + "..."


class _PDF(FPDF):
    pie = ""

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", "", 7)
        self.set_text_color(120)
        self.cell(ANCHO - 20, 4, _recortar(self, self.pie, ANCHO - 20))
        self.cell(20, 4, f"Pág. {self.page_no()}/{{nb}}", align="R")


def _caja(pdf, x, y, w, titulo, filas, color):
    pdf.set_xy(x, y)
    pdf.set_fill_color(*color)
    pdf.set_text_color(255)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(w, 5, _t(titulo.upper()), fill=True)
    pdf.set_text_color(30)
    yy = y + 6
    for etiqueta, valor in filas:
        pdf.set_xy(x + 1, yy)
        pdf.set_font("helvetica", "B", 8)
        pdf.cell(24, 4.5, _t(etiqueta))
        pdf.set_font("helvetica", "", 8)
        pdf.cell(w - 26, 4.5, _recortar(pdf, valor, w - 26))
        yy += 4.5
    pdf.set_draw_color(190)
    pdf.rect(x, y, w, yy - y + 1)
    return yy + 1


def _titulo_seccion(pdf, texto, color):
    pdf.set_fill_color(*color)
    pdf.set_text_color(255)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(ANCHO, 5, _t(texto.upper()), fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30)


def generar(taller, logo, titulo, numero, meta, cliente, vehiculo, grupos, totales, secciones, fmt):
    """
    taller:    dict con los datos del taller (nombre, rut, color1, banco, ...)
    logo:      bytes de la imagen del logo o None
    meta:      [(etiqueta, valor)] fila de datos del documento (fecha, forma de pago, ...)
    cliente / vehiculo: [(etiqueta, valor)]
    grupos:    [(nombre, [linea], subtotal)]  linea = dict(concepto, cantidad, precio, descuento)
    totales:   [(etiqueta, monto, destacado)]
    secciones: [(titulo, texto)]  solo se dibujan las que tienen texto
    fmt:       función que formatea montos ($ 12.345)
    """
    color = _rgb(taller.get("color1"))
    pdf = _PDF(format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(True, 16)
    pdf.pie = " | ".join(x for x in [
        taller.get("pie_documentos"),
        f"{taller.get('banco')} {taller.get('tipo_cuenta') or ''} N° {taller.get('num_cuenta')}"
        if taller.get("banco") and taller.get("num_cuenta") else "",
        taller.get("web"),
    ] if x)
    pdf.add_page()

    # ---- cabecera: logo + datos del taller + recuadro con el documento
    x0 = 12
    if logo:
        try:
            pdf.image(BytesIO(logo), x=12, y=12, w=34, h=24, keep_aspect_ratio=True)
            x0 = 50
        except Exception:  # imagen corrupta o formato no soportado: se omite el logo
            pass
    pdf.set_xy(x0, 12)
    pdf.set_text_color(*color)
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(130 - x0, 7, _recortar(pdf, taller.get("nombre"), 130 - x0), new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(60)
    pdf.set_font("helvetica", "", 8)
    for linea in [taller.get("razon_social"),
                  " - ".join(x for x in [f"RUT {taller.get('rut')}" if taller.get("rut") else "",
                                         taller.get("giro")] if x),
                  " ".join(x for x in [taller.get("direccion"), taller.get("ciudad")] if x),
                  " - ".join(x for x in [taller.get("telefono"), taller.get("email")] if x)]:
        if linea:
            pdf.set_x(x0)
            pdf.cell(130 - x0, 4, _recortar(pdf, linea, 130 - x0), new_x="LEFT", new_y="NEXT")

    pdf.set_draw_color(*color)
    pdf.set_line_width(0.6)
    pdf.rect(138, 12, 60, 24)
    pdf.set_line_width(0.2)
    pdf.set_text_color(*color)
    pdf.set_xy(138, 15)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(60, 7, _t(titulo.upper()), align="C", new_x="LEFT", new_y="NEXT")
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(60, 7, _t(f"N° {numero}"), align="C")
    pdf.set_text_color(30)

    # ---- fila de datos del documento
    y = 40
    if meta:
        w = ANCHO / len(meta)
        pdf.set_fill_color(238, 240, 243)
        for i, (etiqueta, valor) in enumerate(meta):
            pdf.set_xy(12 + i * w, y)
            pdf.set_font("helvetica", "", 7)
            pdf.cell(w, 4, _t(etiqueta), fill=True, new_x="LEFT", new_y="NEXT")
            pdf.set_font("helvetica", "B", 9)
            pdf.cell(w, 5, _recortar(pdf, valor, w), fill=True)
        y += 11

    # ---- cliente y vehículo
    y1 = _caja(pdf, 12, y, 92, "Datos del cliente", cliente, color)
    y2 = _caja(pdf, 106, y, 92, "Datos del vehículo", vehiculo, color) if vehiculo else y1
    pdf.set_xy(12, max(y1, y2) + 4)

    # ---- líneas agrupadas: piezas / mano de obra / otros
    cols = [("Concepto", 96, "L"), ("Cant.", 18, "R"), ("P. Unit.", 26, "R"), ("Desc.", 16, "R"), ("Total", 30, "R")]
    pdf.set_font("helvetica", "B", 8)
    pdf.set_fill_color(201, 206, 213)
    for nombre, ancho, al in cols:
        pdf.cell(ancho, 6, nombre, fill=True, align=al)
    pdf.ln()
    hay = False
    for nombre, lineas, subtotal in grupos:
        if not lineas:
            continue
        hay = True
        pdf.set_fill_color(*[min(255, c + 190) for c in color])
        pdf.set_font("helvetica", "B", 8)
        pdf.cell(ANCHO, 5, _t(nombre.upper()), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 8)
        for l in lineas:
            total = l["cantidad"] * l["precio"] * (1 - (l["descuento"] or 0) / 100)
            pdf.cell(96, 5, _recortar(pdf, l["concepto"], 96), border="B")
            pdf.cell(18, 5, f"{l['cantidad']:g}".replace(".", ","), border="B", align="R")
            pdf.cell(26, 5, _t(fmt(l["precio"])), border="B", align="R")
            pdf.cell(16, 5, f"{l['descuento']:g} %" if l["descuento"] else "", border="B", align="R")
            pdf.cell(30, 5, _t(fmt(total)), border="B", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "B", 8)
        pdf.cell(156, 5, _t(f"Subtotal {nombre.lower()}"), align="R")
        pdf.cell(30, 5, _t(fmt(subtotal)), align="R", new_x="LMARGIN", new_y="NEXT")
    if not hay:
        pdf.set_font("helvetica", "I", 8)
        pdf.cell(ANCHO, 8, "Sin líneas de detalle", align="C", new_x="LMARGIN", new_y="NEXT")

    # ---- totales
    pdf.ln(3)
    for etiqueta, monto, destacado in totales:
        pdf.set_x(128)
        pdf.set_font("helvetica", "B" if destacado else "", 10 if destacado else 9)
        if destacado:
            pdf.set_fill_color(*color)
            pdf.set_text_color(255)
        pdf.cell(40, 6, _t(etiqueta), border="B", fill=destacado)
        pdf.cell(30, 6, _t(fmt(monto)), border="B", align="R", fill=destacado, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(30)

    # ---- observaciones, ingreso, trabajos...
    for titulo_sec, texto in secciones:
        if not (texto or "").strip():
            continue
        pdf.ln(4)
        _titulo_seccion(pdf, titulo_sec, color)
        pdf.set_font("helvetica", "", 8)
        pdf.multi_cell(ANCHO, 4.2, _t(texto.strip()), new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
