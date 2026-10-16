"""
TallerPro - Sistema de gestión para talleres mecánicos (Flask + SQLite)

Ejecutar:
    pip install -r requirements.txt
    python app.py
    -> http://127.0.0.1:5000   usuario: admin / clave: admin123 / código taller: 1001
"""
import json
import os
import re
import smtplib
import sqlite3
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from io import BytesIO
from urllib.parse import quote

import segno
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import documentos_pdf
import patente_provider

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TALLER_DB", os.path.join(BASE, "taller.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # URLs https correctas detrás de Render
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024         # subida de fotos / logo

# --------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
  nombre TEXT, codigo_taller TEXT);
CREATE TABLE IF NOT EXISTS taller(
  id INTEGER PRIMARY KEY CHECK (id=1), nombre TEXT, razon_social TEXT, rut TEXT,
  direccion TEXT, telefono TEXT, email TEXT, iva REAL DEFAULT 19);
CREATE TABLE IF NOT EXISTS clientes(
  id INTEGER PRIMARY KEY, numero INTEGER UNIQUE, rut TEXT, tipo TEXT,
  nombre TEXT, apellido1 TEXT, apellido2 TEXT, alias TEXT, telefono TEXT, movil TEXT,
  direccion TEXT, cod_postal TEXT, comuna TEXT, ciudad TEXT, pais TEXT, email TEXT,
  calidad TEXT, observaciones TEXT, tarifa TEXT, grupo_facturacion TEXT,
  desc_piezas REAL, desc_mo REAL, desc_gral REAL, forma_pago TEXT,
  condiciones_pago TEXT, iva_especial REAL, cuenta_contable TEXT, como_conocio TEXT,
  creado TEXT);
CREATE TABLE IF NOT EXISTS vehiculos(
  id INTEGER PRIMARY KEY, cliente_id INTEGER REFERENCES clientes(id),
  patente TEXT, chasis TEXT, tipo TEXT, marca TEXT, modelo TEXT, version TEXT,
  combustible TEXT, potencia TEXT, unidades_odometro TEXT, n_motor TEXT,
  tipo_motor TEXT, tipo_aceite TEXT, potencia_cv TEXT, potencia_kw TEXT,
  anio INTEGER, km INTEGER, color TEXT, n_caja TEXT, ref_bujias TEXT,
  fecha_placa TEXT, rev_tecnica TEXT, creado TEXT);
CREATE TABLE IF NOT EXISTS documentos(
  id INTEGER PRIMARY KEY, tipo TEXT,             -- presupuesto | orden
  numero TEXT, cliente_id INTEGER, vehiculo_id INTEGER, origen_id INTEGER,
  fecha TEXT, fecha_salida TEXT, km INTEGER, descripcion TEXT,
  tipo_intervencion TEXT, estado TEXT, comentarios TEXT);
CREATE TABLE IF NOT EXISTS boletas(
  id INTEGER PRIMARY KEY, numero TEXT, orden_id INTEGER, cliente_id INTEGER,
  vehiculo_id INTEGER, fecha TEXT, vencimiento TEXT, forma_pago TEXT,
  condiciones_pago TEXT, cobrado REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS lineas(
  id INTEGER PRIMARY KEY, doc_tipo TEXT, doc_id INTEGER,  -- presupuesto|orden|boleta
  tipo TEXT, concepto TEXT, cantidad REAL, precio REAL, descuento REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS caja(
  id INTEGER PRIMARY KEY, fecha TEXT, tipo TEXT, concepto TEXT, monto REAL);
CREATE TABLE IF NOT EXISTS almacen(
    id INTEGER PRIMARY KEY, codigo TEXT UNIQUE, nombre TEXT, descripcion TEXT,
    stock REAL DEFAULT 0, precio REAL DEFAULT 0, proveedor TEXT, creado TEXT);
CREATE TABLE IF NOT EXISTS archivos(               -- logo del taller
  clave TEXT PRIMARY KEY, mime TEXT, datos BLOB, actualizado TEXT);
CREATE TABLE IF NOT EXISTS proveedores(
  id INTEGER PRIMARY KEY, rut TEXT, nombre TEXT, contacto TEXT, telefono TEXT,
  email TEXT, direccion TEXT, rubro TEXT, observaciones TEXT, creado TEXT);
CREATE TABLE IF NOT EXISTS compras(                -- facturas de compra a proveedores
  id INTEGER PRIMARY KEY, proveedor_id INTEGER, numero TEXT, fecha TEXT,
  observaciones TEXT, pagada INTEGER DEFAULT 0, creado TEXT);
CREATE TABLE IF NOT EXISTS compra_lineas(
  id INTEGER PRIMARY KEY, compra_id INTEGER, articulo_id INTEGER,
  cantidad REAL, precio REAL, recibido REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS movimientos_stock(
  id INTEGER PRIMARY KEY, articulo_id INTEGER, fecha TEXT, cantidad REAL,
  referencia TEXT, usuario TEXT);
CREATE TABLE IF NOT EXISTS empleados(
  id INTEGER PRIMARY KEY, rut TEXT, nombre TEXT, cargo TEXT, telefono TEXT,
  email TEXT, pin_hash TEXT, activo INTEGER DEFAULT 1, creado TEXT);
CREATE TABLE IF NOT EXISTS marcaciones(            -- reloj control entrada / salida
  id INTEGER PRIMARY KEY, empleado_id INTEGER, tipo TEXT, fecha TEXT,
  editado_por TEXT, motivo TEXT);
CREATE TABLE IF NOT EXISTS trabajos(               -- trabajos realizados en una orden
  id INTEGER PRIMARY KEY, orden_id INTEGER, fecha TEXT, empleado_id INTEGER,
  descripcion TEXT, horas REAL);
CREATE TABLE IF NOT EXISTS ingresos(               -- recepción del vehículo (daños)
  id INTEGER PRIMARY KEY, orden_id INTEGER UNIQUE, fecha TEXT, km INTEGER,
  combustible TEXT, zonas TEXT, accesorios TEXT, danos TEXT, objetos TEXT,
  observaciones TEXT);
CREATE TABLE IF NOT EXISTS fotos_ingreso(
  id INTEGER PRIMARY KEY, orden_id INTEGER, nombre TEXT, mime TEXT, datos BLOB);
CREATE TABLE IF NOT EXISTS papelera(
  id INTEGER PRIMARY KEY, tabla TEXT, registro_id INTEGER, descripcion TEXT,
  datos TEXT, hijos TEXT, fecha TEXT, usuario TEXT);
CREATE TABLE IF NOT EXISTS alertas_config(
  clave TEXT PRIMARY KEY, activa INTEGER DEFAULT 1, dias INTEGER);
CREATE TABLE IF NOT EXISTS citas(
  id INTEGER PRIMARY KEY, nombre TEXT, telefono TEXT, email TEXT, patente TEXT,
  fecha TEXT, hora TEXT, motivo TEXT, estado TEXT, origen TEXT, creado TEXT);
CREATE TABLE IF NOT EXISTS soporte(                -- citas con la administración de la página
  id INTEGER PRIMARY KEY, tipo TEXT, fecha TEXT, hora TEXT, asunto TEXT, detalle TEXT,
  contacto TEXT, estado TEXT, usuario TEXT, creado TEXT);
"""

# Columnas nuevas sobre tablas que ya existían: se agregan con ALTER TABLE si faltan,
# así una base de datos antigua se actualiza sola al arrancar.
MIGRACIONES = {
    "usuarios": [("rol", "TEXT DEFAULT 'admin'")],
    "taller": [("giro", "TEXT"), ("ciudad", "TEXT"), ("web", "TEXT"),
               ("tarifa_hora", "REAL DEFAULT 35000"), ("banco", "TEXT"), ("tipo_cuenta", "TEXT"),
               ("num_cuenta", "TEXT"), ("pie_documentos", "TEXT"),
               ("nombre_app", "TEXT DEFAULT 'TallerPRO'"), ("color1", "TEXT DEFAULT '#0d3b66'"),
               ("color2", "TEXT DEFAULT '#1fb5c4'")],
    "vehiculos": [("observaciones", "TEXT")],
    "almacen": [("stock_minimo", "REAL DEFAULT 0")],
}

# clave, nombre, texto del parámetro "días" (None = sin parámetro), días por defecto
ALERTAS_DEF = [
    ("rev_tecnica", "Revisión técnica por vencer", "Avisar con estos días de anticipación", 60),
    ("boletas", "Boletas pendientes de cobro", "Avisar cuando lleven estos días sin cobrar", 0),
    ("stock", "Repuestos bajo el stock mínimo", None, 0),
    ("ordenes", "Órdenes abiertas por mucho tiempo", "Avisar cuando lleven estos días abiertas", 7),
    ("citas", "Citas pendientes de confirmar", None, 0),
]


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def q(sql, args=(), one=False):
    cur = db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def ex(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur.lastrowid


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    for tabla, columnas in MIGRACIONES.items():
        existentes = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        for col, decl in columnas:
            if col not in existentes:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {decl}")
    for clave, _, _, dias in ALERTAS_DEF:
        conn.execute("INSERT OR IGNORE INTO alertas_config(clave,activa,dias) VALUES(?,1,?)", (clave, dias))
    if not conn.execute("SELECT 1 FROM usuarios").fetchone():
        conn.execute("INSERT INTO usuarios(username,password_hash,nombre,codigo_taller,rol) VALUES(?,?,?,?,?)",
                     ("admin", generate_password_hash("admin123"), "Usuario Administrador", "1001", "admin"))
        conn.execute("INSERT INTO taller(id,nombre,razon_social,rut,direccion,telefono,email,iva) "
                     "VALUES(1,?,?,?,?,?,?,19)",
                     ("Mi Taller Demo", "MI TALLER DEMO SPA", "11.111.111-1",
                      "Av. Siempre Viva 123, Santiago", "+56 9 1234 5678", "contacto@mitaller.cl"))
        # Datos de ejemplo para el cuadro de mando (12 meses)
        hoy = date.today().replace(day=15)
        import random
        random.seed(7)
        for i in range(12):
            d = (hoy - timedelta(days=30 * i)).isoformat()
            conn.execute("INSERT INTO caja(fecha,tipo,concepto,monto) VALUES(?,?,?,?)",
                         (d, "ingreso", "Ventas del mes (demo)", random.randint(3, 11) * 900_000))
            if i < 3:
                conn.execute("INSERT INTO caja(fecha,tipo,concepto,monto) VALUES(?,?,?,?)",
                             (d, "gasto", "Compra repuestos (demo)", random.randint(1, 4) * 800_000))
        conn.execute("""INSERT INTO clientes(numero,rut,tipo,nombre,apellido1,telefono,comuna,ciudad,pais,
                        tarifa,forma_pago,calidad,creado) VALUES(229,'12.345.678-5','Particular','Juan',
                        'Pérez','+56 9 8765 4321','Maipú','Santiago','Chile','Tarifa 1 - 35000 $ Hora',
                        'Efectivo','Bueno',?)""", (datetime.now().isoformat(),))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------- helpers
def usuario_actual():
    if "uid" not in session:
        return None
    if "usuario_actual" not in g:
        g.usuario_actual = q("SELECT * FROM usuarios WHERE id=?", (session["uid"],), one=True)
    return g.usuario_actual


def es_admin():
    u = usuario_actual()
    return bool(u and u["rol"] == "admin")


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if "uid" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrap


def admin_required(f):
    @wraps(f)
    @login_required
    def wrap(*a, **kw):
        if not es_admin():
            flash("Solo los administradores pueden acceder a esta sección", "error")
            return redirect(url_for("dashboard"))
        return f(*a, **kw)
    return wrap


@app.template_filter("clp")
def clp(v):
    try:
        return "$ " + f"{float(v or 0):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "$ 0"


@app.template_filter("fecha")
def fecha(v):
    if not v:
        return ""
    try:
        return datetime.fromisoformat(v).strftime("%d/%m/%Y")
    except ValueError:
        return v


@app.template_filter("fechahora")
def fechahora(v):
    try:
        return datetime.fromisoformat(v).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return v or ""


@app.template_filter("tono")
def tono(hexcolor, f):
    """Aclara (f>0) u oscurece (f<0) un color #rrggbb; se usa para derivar la paleta."""
    try:
        r, gg, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    except (TypeError, ValueError):
        return hexcolor
    lim = 255 if f > 0 else 0
    return "#%02x%02x%02x" % tuple(round(c + (lim - c) * abs(f)) for c in (r, gg, b))


@app.context_processor
def globales():
    logueado = "uid" in session
    t = q("SELECT * FROM taller WHERE id=1", one=True)
    logo_v = q("SELECT actualizado FROM archivos WHERE clave='logo'", one=True)
    return dict(taller=t if logueado else None, marca=t, logo_v=logo_v["actualizado"] if logo_v else None,
                usuario=session.get("nombre"), codigo=session.get("codigo"),
                admin=es_admin(), alertas=len(alertas_activas()) if logueado else 0)


def total_lineas(doc_tipo, doc_id):
    r = q("SELECT COALESCE(SUM(cantidad*precio*(1-descuento/100.0)),0) t FROM lineas "
          "WHERE doc_tipo=? AND doc_id=?", (doc_tipo, doc_id), one=True)
    return r["t"]


TIPOS_LINEA = ["Piezas", "Mano de obra", "Otros"]


def grupo_linea(tipo):
    t = (tipo or "").lower()
    if t.startswith("mano"):
        return "Mano de obra"
    if t.startswith(("pieza", "repuesto")):
        return "Piezas"
    return "Otros"


def total_linea(l):
    return l["cantidad"] * l["precio"] * (1 - (l["descuento"] or 0) / 100)


def agrupar_lineas(lineas):
    """Divide las líneas en Piezas / Mano de obra / Otros con su subtotal."""
    grupos = {g: [] for g in TIPOS_LINEA}
    for l in lineas:
        grupos[grupo_linea(l["tipo"])].append(l)
    return [(g, ls, sum(map(total_linea, ls))) for g, ls in grupos.items()]


def siguiente(tabla_tipo):
    if tabla_tipo == "presupuesto":
        n = q("SELECT COUNT(*) c FROM documentos WHERE tipo='presupuesto'", one=True)["c"] + 1
        return f"PPT{n:04d}"
    if tabla_tipo == "orden":
        n = q("SELECT COUNT(*) c FROM documentos WHERE tipo='orden'", one=True)["c"] + 1
        return f"OT{n:04d}"
    n = q("SELECT COUNT(*) c FROM boletas", one=True)["c"] + 1
    return f"{n:010d}"


def nombre_cliente(c):
    return " ".join(x for x in [c["nombre"], c["apellido1"], c["apellido2"]] if x)


app.jinja_env.globals.update(nombre_cliente=nombre_cliente, total_lineas=total_lineas,
                             agrupar_lineas=agrupar_lineas, total_linea=total_linea,
                             TIPOS_LINEA=TIPOS_LINEA)

CLIENTE_CAMPOS = ["rut", "tipo", "nombre", "apellido1", "apellido2", "alias", "telefono", "movil",
                  "direccion", "cod_postal", "comuna", "ciudad", "pais", "email", "calidad",
                  "observaciones", "tarifa", "grupo_facturacion", "desc_piezas", "desc_mo",
                  "desc_gral", "forma_pago", "condiciones_pago", "iva_especial",
                  "cuenta_contable", "como_conocio"]
VEHICULO_CAMPOS = ["patente", "chasis", "tipo", "marca", "modelo", "version", "combustible",
                   "potencia", "unidades_odometro", "n_motor", "tipo_motor", "tipo_aceite",
                   "potencia_cv", "potencia_kw", "anio", "km", "color", "n_caja", "ref_bujias",
                   "fecha_placa", "rev_tecnica", "observaciones"]
PROVEEDOR_CAMPOS = ["rut", "nombre", "contacto", "telefono", "email", "direccion", "rubro", "observaciones"]
EMPLEADO_CAMPOS = ["rut", "nombre", "cargo", "telefono", "email"]


def ahora():
    return datetime.now().isoformat(timespec="minutes")


# ------------------------------------------------------------------ alertas
def alertas_activas():
    if "alertas" in g:
        return g.alertas
    cfg = {r["clave"]: r for r in q("SELECT * FROM alertas_config")}
    hoy = date.today()

    def dias(clave):
        return cfg[clave]["dias"] or 0 if clave in cfg else 0

    def activa(clave):
        return clave in cfg and cfg[clave]["activa"]

    out = []
    if activa("rev_tecnica"):
        for v in q("""SELECT id, patente, marca, modelo, rev_tecnica, cliente_id FROM vehiculos
                      WHERE rev_tecnica IS NOT NULL AND rev_tecnica<>'' AND rev_tecnica<=?
                      ORDER BY rev_tecnica""", ((hoy + timedelta(days=dias("rev_tecnica"))).isoformat(),)):
            vencida = v["rev_tecnica"] < hoy.isoformat()
            out.append(dict(tipo="Revisión técnica", grave=vencida,
                            texto=f"{v['patente']} {v['marca'] or ''} {v['modelo'] or ''}: "
                                  f"{'venció' if vencida else 'vence'} el {fecha(v['rev_tecnica'])}",
                            url=url_for("cliente_ficha", cid=v["cliente_id"], tab="vehiculos")))
    if activa("boletas"):
        limite = (hoy - timedelta(days=dias("boletas"))).isoformat()
        for b in q("SELECT * FROM boletas WHERE fecha<=? ORDER BY fecha", (limite,)):
            pendiente = total_lineas("boleta", b["id"]) - b["cobrado"]
            if pendiente > 0:
                out.append(dict(tipo="Cobro pendiente", grave=b["vencimiento"] and b["vencimiento"] < hoy.isoformat(),
                                texto=f"Boleta {b['numero']} del {fecha(b['fecha'])}: pendiente {clp(pendiente)}",
                                url=url_for("boleta", bid=b["id"])))
    if activa("stock"):
        for a in q("SELECT * FROM almacen WHERE stock_minimo>0 AND stock<=stock_minimo ORDER BY nombre"):
            out.append(dict(tipo="Stock bajo", grave=a["stock"] <= 0,
                            texto=f"{a['codigo']} {a['nombre']}: quedan {a['stock']:g} (mínimo {a['stock_minimo']:g})",
                            url=url_for("almacen_editar", aid=a["id"])))
    if activa("ordenes"):
        limite = (datetime.now() - timedelta(days=dias("ordenes"))).isoformat()
        for o in q("""SELECT d.*, v.patente FROM documentos d LEFT JOIN vehiculos v ON v.id=d.vehiculo_id
                      WHERE d.tipo='orden' AND d.estado='ABIERTA' AND d.fecha<=? ORDER BY d.fecha""", (limite,)):
            out.append(dict(tipo="Orden abierta", grave=False,
                            texto=f"{o['numero']} ({o['patente'] or '-'}) abierta desde el {fecha(o['fecha'])}",
                            url=url_for("documento", tipo="orden", did=o["id"])))
    if activa("citas"):
        for c in q("SELECT * FROM citas WHERE estado='PENDIENTE' ORDER BY fecha, hora"):
            out.append(dict(tipo="Cita por confirmar", grave=False,
                            texto=f"{c['nombre']} - {fecha(c['fecha'])} {c['hora'] or ''} {c['patente'] or ''}",
                            url=url_for("citas")))
    g.alertas = out
    return out


# ----------------------------------------------------------------- papelera
# tabla -> (nombre para mostrar, [(tabla_hija, columna_fk)])
PAPELERA = {
    "clientes": ("Cliente", [("vehiculos", "cliente_id")]),
    "vehiculos": ("Vehículo", []),
    "almacen": ("Repuesto", []),
    "proveedores": ("Proveedor", []),
    "empleados": ("Empleado", []),
    "citas": ("Cita", []),
}


def a_papelera(tabla, rid, descripcion):
    """En vez de borrar definitivamente, guarda una copia del registro (y sus hijos) en la papelera."""
    row = q(f"SELECT * FROM {tabla} WHERE id=?", (rid,), one=True) or abort(404)
    hijos = {t: [dict(r) for r in q(f"SELECT * FROM {t} WHERE {col}=?", (rid,))]
             for t, col in PAPELERA[tabla][1]}
    db().execute("INSERT INTO papelera(tabla,registro_id,descripcion,datos,hijos,fecha,usuario) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (tabla, rid, descripcion, json.dumps(dict(row)), json.dumps(hijos), ahora(), session.get("nombre")))
    for t, col in PAPELERA[tabla][1]:
        db().execute(f"DELETE FROM {t} WHERE {col}=?", (rid,))
    db().execute(f"DELETE FROM {tabla} WHERE id=?", (rid,))
    db().commit()


def _insertar(tabla, datos):
    existentes = {r["name"] for r in q(f"PRAGMA table_info({tabla})")}
    datos = {k: v for k, v in datos.items() if k in existentes}
    db().execute(f"INSERT INTO {tabla}({','.join(datos)}) VALUES({','.join('?'*len(datos))})", list(datos.values()))


# --------------------------------------------------------------------- auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM usuarios WHERE username=?", (request.form["usuario"].strip(),), one=True)
        if u and check_password_hash(u["password_hash"], request.form["password"]) \
                and request.form.get("codigo", "").strip() == u["codigo_taller"]:
            session.clear()
            session.update(uid=u["id"], nombre=u["nombre"], codigo=u["codigo_taller"])
            session.permanent = bool(request.form.get("recordar"))
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Usuario, contraseña o código de taller incorrectos", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- dashboard
@app.route("/")
@login_required
def dashboard():
    hoy = date.today()
    meses, ingresos, gastos = [], [], []
    nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    for i in range(11, -1, -1):
        y, m = hoy.year, hoy.month - i
        while m <= 0:
            m += 12
            y -= 1
        pref = f"{y}-{m:02d}"
        ing = q("SELECT COALESCE(SUM(monto),0) s FROM caja WHERE tipo='ingreso' AND fecha LIKE ?",
                (pref + "%",), one=True)["s"]
        ing += sum(total_lineas("boleta", b["id"]) for b in
                   q("SELECT id FROM boletas WHERE fecha LIKE ?", (pref + "%",)))
        gas = q("SELECT COALESCE(SUM(monto),0) s FROM caja WHERE tipo='gasto' AND fecha LIKE ?",
                (pref + "%",), one=True)["s"]
        meses.append(f"{nombres[m-1]} {y}")
        ingresos.append(ing)
        gastos.append(gas)
    resumen = dict(
        clientes=q("SELECT COUNT(*) c FROM clientes", one=True)["c"],
        vehiculos=q("SELECT COUNT(*) c FROM vehiculos", one=True)["c"],
        abiertas=q("SELECT COUNT(*) c FROM documentos WHERE tipo='orden' AND estado='ABIERTA'", one=True)["c"],
        presupuestos=q("SELECT COUNT(*) c FROM documentos WHERE tipo='presupuesto' AND estado='PENDIENTE'", one=True)["c"],
    )
    dias_rt = (q("SELECT dias FROM alertas_config WHERE clave='rev_tecnica'", one=True) or {"dias": 60})["dias"] or 60
    rt = q("""SELECT v.*, c.nombre, c.apellido1, c.apellido2, c.id cid FROM vehiculos v
              JOIN clientes c ON c.id=v.cliente_id WHERE rev_tecnica IS NOT NULL AND rev_tecnica<>''
              AND rev_tecnica <= ? ORDER BY rev_tecnica LIMIT 8""",
           ((hoy + timedelta(days=dias_rt)).isoformat(),))
    return render_template("dashboard.html", meses=meses, ingresos=ingresos, gastos=gastos,
                           resumen=resumen, rev_proximas=rt, actualizado=datetime.now())


@app.route("/alertas")
@login_required
def alertas_lista():
    return render_template("alertas.html", lista=alertas_activas())


# ----------------------------------------------------------------- clientes
@app.route("/clientes")
@login_required
def clientes():
    s = request.args.get("q", "").strip()
    like = f"%{s}%"
    rows = q("""SELECT c.*, (SELECT COUNT(*) FROM vehiculos v WHERE v.cliente_id=c.id) nveh
                FROM clientes c WHERE ?='' OR nombre LIKE ? OR apellido1 LIKE ? OR rut LIKE ?
                OR CAST(numero AS TEXT)=? ORDER BY numero DESC""", (s, like, like, like, s))
    return render_template("clientes.html", clientes=rows, s=s)


@app.route("/clientes/nuevo", methods=["GET", "POST"])
@login_required
def cliente_nuevo():
    sig = (q("SELECT MAX(numero) m FROM clientes", one=True)["m"] or 0) + 1
    if request.method == "POST":
        if not request.form.get("nombre", "").strip():
            flash("El nombre es obligatorio", "error")
            return render_template("cliente_form.html", c=request.form, numero=sig)
        vals = [request.form.get(k, "").strip() for k in CLIENTE_CAMPOS]
        cid = ex(f"INSERT INTO clientes(numero,{','.join(CLIENTE_CAMPOS)},creado) "
                 f"VALUES(?,{','.join('?'*len(CLIENTE_CAMPOS))},?)",
                 [int(request.form.get("numero") or sig), *vals, datetime.now().isoformat()])
        flash("Cliente creado correctamente", "ok")
        return redirect(url_for("cliente_ficha", cid=cid, tab="vehiculos"))
    return render_template("cliente_form.html", c={}, numero=sig)


@app.route("/clientes/<int:cid>/editar", methods=["GET", "POST"])
@login_required
def cliente_editar(cid):
    c = q("SELECT * FROM clientes WHERE id=?", (cid,), one=True) or abort(404)
    if request.method == "POST":
        sets = ",".join(f"{k}=?" for k in CLIENTE_CAMPOS)
        ex(f"UPDATE clientes SET {sets} WHERE id=?",
           [request.form.get(k, "").strip() for k in CLIENTE_CAMPOS] + [cid])
        flash("Cliente actualizado", "ok")
        return redirect(url_for("cliente_ficha", cid=cid))
    return render_template("cliente_form.html", c=c, numero=c["numero"], editar=True)


@app.route("/clientes/<int:cid>/borrar", methods=["POST"])
@login_required
def cliente_borrar(cid):
    if q("SELECT 1 FROM documentos WHERE cliente_id=? UNION SELECT 1 FROM boletas WHERE cliente_id=?",
         (cid, cid), one=True):
        flash("No se puede borrar: el cliente tiene documentos asociados", "error")
        return redirect(url_for("cliente_ficha", cid=cid))
    c = q("SELECT * FROM clientes WHERE id=?", (cid,), one=True) or abort(404)
    a_papelera("clientes", cid, f"{c['numero']} - {nombre_cliente(c)}")
    flash("Cliente enviado a la papelera", "ok")
    return redirect(url_for("clientes"))


@app.route("/clientes/<int:cid>")
@login_required
def cliente_ficha(cid):
    c = q("SELECT * FROM clientes WHERE id=?", (cid,), one=True) or abort(404)
    tab = request.args.get("tab", "datos")
    vehiculos = q("SELECT * FROM vehiculos WHERE cliente_id=? ORDER BY id", (cid,))
    ordenes_sin_fact = q("""SELECT d.* FROM documentos d WHERE d.cliente_id=? AND d.tipo='orden'
                            AND NOT EXISTS (SELECT 1 FROM boletas b WHERE b.orden_id=d.id)
                            ORDER BY d.id DESC LIMIT 10""", (cid,))
    boletas = q("SELECT * FROM boletas WHERE cliente_id=? ORDER BY id DESC", (cid,))
    pendientes = [b for b in boletas if b["cobrado"] < total_lineas("boleta", b["id"])][:10]
    actividad = q("""SELECT d.*, v.patente, v.marca, v.modelo FROM documentos d
                     LEFT JOIN vehiculos v ON v.id=d.vehiculo_id WHERE d.cliente_id=?
                     ORDER BY d.fecha DESC LIMIT 20""", (cid,))
    return render_template("cliente_ficha.html", c=c, tab=tab, vehiculos=vehiculos,
                           ordenes_sin_fact=ordenes_sin_fact, pendientes=pendientes,
                           boletas=boletas, actividad=actividad)


# ---------------------------------------------------------------- vehículos
@app.route("/vehiculos")
@login_required
def vehiculos():
    s = request.args.get("q", "").strip().upper()
    like = f"%{s}%"
    rows = q("""SELECT v.*, c.nombre, c.apellido1, c.apellido2, c.numero cnum FROM vehiculos v
                JOIN clientes c ON c.id=v.cliente_id
                WHERE ?='' OR v.patente LIKE ? OR v.marca LIKE ? OR v.modelo LIKE ? OR v.chasis LIKE ?
                ORDER BY v.id DESC""", (s, like, like, like, like))
    return render_template("vehiculos.html", vehiculos=rows, s=s, ingreso=request.args.get("ingreso"))


@app.route("/clientes/<int:cid>/vehiculos/nuevo", methods=["GET", "POST"])
@app.route("/vehiculos/<int:vid>/editar", methods=["GET", "POST"])
@login_required
def vehiculo_form(cid=None, vid=None):
    v = q("SELECT * FROM vehiculos WHERE id=?", (vid,), one=True) if vid else None
    if vid and not v:
        abort(404)
    cid = cid or v["cliente_id"]
    c = q("SELECT * FROM clientes WHERE id=?", (cid,), one=True) or abort(404)
    if request.method == "POST":
        data = {k: request.form.get(k, "").strip() for k in VEHICULO_CAMPOS}
        data["patente"] = patente_provider.normalizar(data["patente"])
        if not data["patente"]:
            flash("La patente es obligatoria", "error")
            return render_template("vehiculo_form.html", c=c, v=request.form)
        otro = q("SELECT id FROM vehiculos WHERE patente=? AND id<>?", (data["patente"], vid or 0), one=True)
        if otro:
            flash(f"La patente {data['patente']} ya está registrada", "error")
            return render_template("vehiculo_form.html", c=c, v=request.form)
        if vid:
            ex(f"UPDATE vehiculos SET {','.join(k+'=?' for k in data)} WHERE id=?", [*data.values(), vid])
        else:
            ex(f"INSERT INTO vehiculos(cliente_id,{','.join(data)},creado) "
               f"VALUES(?,{','.join('?'*len(data))},?)", [cid, *data.values(), datetime.now().isoformat()])
        flash("Vehículo guardado", "ok")
        return redirect(url_for("cliente_ficha", cid=cid, tab="vehiculos"))
    return render_template("vehiculo_form.html", c=c, v=v or {})


@app.route("/vehiculos/<int:vid>/borrar", methods=["POST"])
@login_required
def vehiculo_borrar(vid):
    v = q("SELECT * FROM vehiculos WHERE id=?", (vid,), one=True) or abort(404)
    if q("SELECT 1 FROM documentos WHERE vehiculo_id=?", (vid,), one=True):
        flash("No se puede borrar: el vehículo tiene documentos", "error")
    else:
        a_papelera("vehiculos", vid, f"{v['patente']} {v['marca'] or ''} {v['modelo'] or ''}")
        flash("Vehículo enviado a la papelera", "ok")
    return redirect(url_for("cliente_ficha", cid=v["cliente_id"], tab="vehiculos"))


@app.route("/api/patente/<patente>")
@login_required
def api_patente(patente):
    res = patente_provider.buscar(patente)
    if res.get("ok"):
        # Último km conocido también desde el historial propio del taller
        r = q("SELECT MAX(d.km) k FROM documentos d JOIN vehiculos v ON v.id=d.vehiculo_id WHERE v.patente=?",
              (res["patente"],), one=True)
        if r and r["k"] and (not res.get("km") or r["k"] > res["km"]):
            res["km"] = r["k"]
    return jsonify(res)


# -------------------------------------------------- presupuestos y órdenes
@app.route("/<any(presupuesto,orden):tipo>/nuevo/<int:vid>")
@login_required
def documento_nuevo(tipo, vid):
    v = q("SELECT * FROM vehiculos WHERE id=?", (vid,), one=True) or abort(404)
    did = ex("""INSERT INTO documentos(tipo,numero,cliente_id,vehiculo_id,fecha,km,estado)
                VALUES(?,?,?,?,?,?,?)""",
             (tipo, siguiente(tipo), v["cliente_id"], vid, ahora(),
              v["km"], "PENDIENTE" if tipo == "presupuesto" else "ABIERTA"))
    # Una orden nueva parte por la recepción del vehículo (estado y daños con los que llega)
    return redirect(url_for("documento", tipo=tipo, did=did, tab="ingreso" if tipo == "orden" else None))


@app.route("/<any(presupuesto,orden):tipo>/<int:did>", methods=["GET", "POST"])
@login_required
def documento(tipo, did):
    d = q("SELECT * FROM documentos WHERE id=? AND tipo=?", (did, tipo), one=True) or abort(404)
    if request.method == "POST":
        km = request.form.get("km") or None
        ex("""UPDATE documentos SET km=?, descripcion=?, tipo_intervencion=?, comentarios=?,
              fecha_salida=? WHERE id=?""",
           (km, request.form.get("descripcion"), request.form.get("tipo_intervencion"),
            request.form.get("comentarios"), request.form.get("fecha_salida") or None, did))
        if km:
            ex("UPDATE vehiculos SET km=? WHERE id=? AND (km IS NULL OR km<?)",
               (int(km), d["vehiculo_id"], int(km)))
        flash("Datos guardados", "ok")
        return redirect(url_for("documento", tipo=tipo, did=did, tab=request.args.get("tab")))
    c = q("SELECT * FROM clientes WHERE id=?", (d["cliente_id"],), one=True)
    v = q("SELECT * FROM vehiculos WHERE id=?", (d["vehiculo_id"],), one=True)
    lineas = q("SELECT * FROM lineas WHERE doc_tipo=? AND doc_id=? ORDER BY id", (tipo, did))
    origen = q("SELECT * FROM documentos WHERE id=?", (d["origen_id"],), one=True) if d["origen_id"] else None
    derivado = q("SELECT * FROM documentos WHERE origen_id=?", (did,), one=True)
    boleta = q("SELECT * FROM boletas WHERE orden_id=?", (did,), one=True) if tipo == "orden" else None
    ingreso = q("SELECT * FROM ingresos WHERE orden_id=?", (did,), one=True)
    fotos = q("SELECT id, nombre FROM fotos_ingreso WHERE orden_id=? ORDER BY id", (did,))
    trabajos = q("""SELECT t.*, e.nombre empleado FROM trabajos t LEFT JOIN empleados e ON e.id=t.empleado_id
                    WHERE t.orden_id=? ORDER BY t.fecha, t.id""", (did,))
    return render_template("documento.html", d=d, tipo=tipo, c=c, v=v, lineas=lineas,
                           total=total_lineas(tipo, did), origen=origen, derivado=derivado,
                           boleta=boleta, tab=request.args.get("tab", "datos"),
                           ingreso=ingreso, fotos=fotos, trabajos=trabajos,
                           empleados=q("SELECT id, nombre FROM empleados WHERE activo=1 ORDER BY nombre"),
                           articulos=q("SELECT codigo, nombre, precio, stock FROM almacen ORDER BY nombre"),
                           zonas=ZONAS_DANO, accesorios=ACCESORIOS)


@app.route("/<any(presupuesto,orden,boleta):tipo>/<int:did>/linea", methods=["POST"])
@login_required
def linea_nueva(tipo, did):
    f = request.form
    try:
        cant = float(f.get("cantidad") or 1)
        precio = float(f.get("precio") or 0)
        desc = float(f.get("descuento") or 0)
    except ValueError:
        flash("Cantidad, precio y descuento deben ser números", "error")
        return redirect(request.referrer or url_for("dashboard"))
    if f.get("concepto", "").strip():
        ex("INSERT INTO lineas(doc_tipo,doc_id,tipo,concepto,cantidad,precio,descuento) VALUES(?,?,?,?,?,?,?)",
           (tipo, did, f.get("tipo", "Otros"), f["concepto"].strip(), cant, precio, desc))
    return redirect((request.referrer or url_for("dashboard")).split("#")[0] + "#lineas")


@app.route("/linea/<int:lid>/borrar", methods=["POST"])
@login_required
def linea_borrar(lid):
    ex("DELETE FROM lineas WHERE id=?", (lid,))
    return redirect((request.referrer or url_for("dashboard")).split("#")[0] + "#lineas")


def copiar_lineas(de_tipo, de_id, a_tipo, a_id):
    for l in q("SELECT * FROM lineas WHERE doc_tipo=? AND doc_id=?", (de_tipo, de_id)):
        ex("INSERT INTO lineas(doc_tipo,doc_id,tipo,concepto,cantidad,precio,descuento) VALUES(?,?,?,?,?,?,?)",
           (a_tipo, a_id, l["tipo"], l["concepto"], l["cantidad"], l["precio"], l["descuento"]))


@app.route("/presupuesto/<int:did>/a-orden", methods=["POST"])
@login_required
def presupuesto_a_orden(did):
    p = q("SELECT * FROM documentos WHERE id=? AND tipo='presupuesto'", (did,), one=True) or abort(404)
    ya = q("SELECT id FROM documentos WHERE origen_id=?", (did,), one=True)
    if ya:
        return redirect(url_for("documento", tipo="orden", did=ya["id"]))
    oid = ex("""INSERT INTO documentos(tipo,numero,cliente_id,vehiculo_id,origen_id,fecha,km,descripcion,estado)
                VALUES('orden',?,?,?,?,?,?,?,'ABIERTA')""",
             (siguiente("orden"), p["cliente_id"], p["vehiculo_id"], did,
              ahora(), p["km"], p["descripcion"]))
    copiar_lineas("presupuesto", did, "orden", oid)
    ex("UPDATE documentos SET estado='ACEPTADO' WHERE id=?", (did,))
    flash("Orden de reparación creada desde el presupuesto", "ok")
    return redirect(url_for("documento", tipo="orden", did=oid, tab="ingreso"))


@app.route("/orden/<int:did>/cerrar", methods=["POST"])
@login_required
def orden_cerrar(did):
    ex("UPDATE documentos SET estado='CERRADA', fecha_salida=COALESCE(fecha_salida,?) WHERE id=? AND tipo='orden'",
       (ahora(), did))
    return redirect(url_for("documento", tipo="orden", did=did))


@app.route("/presupuestos")
@app.route("/ordenes")
@login_required
def lista_documentos():
    tipo = "presupuesto" if request.path.startswith("/presupuestos") else "orden"
    estado = request.args.get("estado", "")
    rows = q("""SELECT d.*, c.nombre, c.apellido1, c.apellido2, v.patente, v.marca, v.modelo
                FROM documentos d JOIN clientes c ON c.id=d.cliente_id JOIN vehiculos v ON v.id=d.vehiculo_id
                WHERE d.tipo=? AND (?='' OR d.estado=?) ORDER BY d.id DESC""", (tipo, estado, estado))
    estados = [r["estado"] for r in q("SELECT DISTINCT estado FROM documentos WHERE tipo=?", (tipo,))]
    return render_template("documentos.html", tipo=tipo, docs=rows, estado=estado, estados=estados)


# ------------------------------------------- ingreso del vehículo (daños)
ZONAS_DANO = ["Parachoques delantero", "Parachoques trasero", "Capó", "Techo", "Maletero / portalón",
              "Puerta delantera izq.", "Puerta delantera der.", "Puerta trasera izq.", "Puerta trasera der.",
              "Guardabarros izq.", "Guardabarros der.", "Parabrisas", "Luneta trasera", "Espejos",
              "Focos / luces", "Llantas / neumáticos", "Tapiz / interior", "Tablero"]
ACCESORIOS = ["Rueda de repuesto", "Gata", "Llave de ruedas", "Triángulos", "Extintor", "Botiquín",
              "Radio / pantalla", "Documentos del vehículo", "Llaves (cantidad en observaciones)"]


@app.route("/orden/<int:did>/ingreso", methods=["POST"])
@login_required
def orden_ingreso(did):
    d = q("SELECT * FROM documentos WHERE id=? AND tipo='orden'", (did,), one=True) or abort(404)
    f = request.form
    km = int(f["km"]) if f.get("km", "").isdigit() else None
    datos = (f.get("fecha") or ahora(), km, f.get("combustible"), ", ".join(f.getlist("zonas")),
             ", ".join(f.getlist("accesorios")), f.get("danos", "").strip(), f.get("objetos", "").strip(),
             f.get("observaciones", "").strip())
    if q("SELECT 1 FROM ingresos WHERE orden_id=?", (did,), one=True):
        ex("""UPDATE ingresos SET fecha=?, km=?, combustible=?, zonas=?, accesorios=?, danos=?, objetos=?,
              observaciones=? WHERE orden_id=?""", (*datos, did))
    else:
        ex("""INSERT INTO ingresos(fecha,km,combustible,zonas,accesorios,danos,objetos,observaciones,orden_id)
              VALUES(?,?,?,?,?,?,?,?,?)""", (*datos, did))
    if km:
        ex("UPDATE documentos SET km=COALESCE(km,?) WHERE id=?", (km, did))
        ex("UPDATE vehiculos SET km=? WHERE id=? AND (km IS NULL OR km<?)", (km, d["vehiculo_id"], km))
    subidas = 0
    for foto in request.files.getlist("fotos"):
        if foto and foto.filename and (foto.mimetype or "").startswith("image/"):
            ex("INSERT INTO fotos_ingreso(orden_id,nombre,mime,datos) VALUES(?,?,?,?)",
               (did, foto.filename, foto.mimetype, foto.read()))
            subidas += 1
    flash("Ingreso del vehículo guardado" + (f" ({subidas} foto/s)" if subidas else ""), "ok")
    return redirect(url_for("documento", tipo="orden", did=did, tab="ingreso"))


@app.route("/foto/<int:fid>")
@login_required
def foto_ingreso(fid):
    f = q("SELECT mime, datos FROM fotos_ingreso WHERE id=?", (fid,), one=True) or abort(404)
    return Response(f["datos"], mimetype=f["mime"])


@app.route("/foto/<int:fid>/borrar", methods=["POST"])
@login_required
def foto_borrar(fid):
    f = q("SELECT orden_id FROM fotos_ingreso WHERE id=?", (fid,), one=True) or abort(404)
    ex("DELETE FROM fotos_ingreso WHERE id=?", (fid,))
    return redirect(url_for("documento", tipo="orden", did=f["orden_id"], tab="ingreso"))


# ------------------------------------------- trabajos realizados en la orden
@app.route("/orden/<int:did>/trabajo", methods=["POST"])
@login_required
def trabajo_nuevo(did):
    q("SELECT 1 FROM documentos WHERE id=? AND tipo='orden'", (did,), one=True) or abort(404)
    f = request.form
    desc = f.get("descripcion", "").strip()
    try:
        horas = float(f.get("horas") or 0)
    except ValueError:
        horas = 0
    if not desc:
        flash("Describe el trabajo realizado", "error")
        return redirect(url_for("documento", tipo="orden", did=did, tab="trabajos"))
    ex("INSERT INTO trabajos(orden_id,fecha,empleado_id,descripcion,horas) VALUES(?,?,?,?,?)",
       (did, f.get("fecha") or ahora(), f.get("empleado_id") or None, desc, horas))
    if f.get("cargar") and horas > 0:
        tarifa = q("SELECT tarifa_hora FROM taller WHERE id=1", one=True)["tarifa_hora"] or 0
        ex("INSERT INTO lineas(doc_tipo,doc_id,tipo,concepto,cantidad,precio,descuento) VALUES('orden',?,?,?,?,?,0)",
           (did, "Mano de obra", desc, horas, tarifa))
        flash(f"Trabajo registrado y cargado como mano de obra ({horas:g} h × {clp(tarifa)})", "ok")
    else:
        flash("Trabajo registrado", "ok")
    return redirect(url_for("documento", tipo="orden", did=did, tab="trabajos"))


@app.route("/trabajo/<int:tid>/borrar", methods=["POST"])
@login_required
def trabajo_borrar(tid):
    t = q("SELECT orden_id FROM trabajos WHERE id=?", (tid,), one=True) or abort(404)
    ex("DELETE FROM trabajos WHERE id=?", (tid,))
    return redirect(url_for("documento", tipo="orden", did=t["orden_id"], tab="trabajos"))


# ------------------------------------------------------------------ boletas
@app.route("/orden/<int:did>/boleta", methods=["POST"])
@login_required
def emitir_boleta(did):
    o = q("SELECT * FROM documentos WHERE id=? AND tipo='orden'", (did,), one=True) or abort(404)
    ya = q("SELECT id FROM boletas WHERE orden_id=?", (did,), one=True)
    if ya:
        return redirect(url_for("boleta", bid=ya["id"]))
    c = q("SELECT * FROM clientes WHERE id=?", (o["cliente_id"],), one=True)
    hoy = date.today().isoformat()
    bid = ex("""INSERT INTO boletas(numero,orden_id,cliente_id,vehiculo_id,fecha,vencimiento,forma_pago,condiciones_pago)
                VALUES(?,?,?,?,?,?,?,?)""",
             (siguiente("boleta"), did, o["cliente_id"], o["vehiculo_id"], hoy, hoy,
              c["forma_pago"] or "Efectivo", c["condiciones_pago"] or "-- Sin Definir --"))
    copiar_lineas("orden", did, "boleta", bid)
    flash("Boleta emitida", "ok")
    return redirect(url_for("boleta", bid=bid))


@app.route("/boleta/<int:bid>")
@login_required
def boleta(bid):
    b = q("SELECT * FROM boletas WHERE id=?", (bid,), one=True) or abort(404)
    c = q("SELECT * FROM clientes WHERE id=?", (b["cliente_id"],), one=True)
    v = q("SELECT * FROM vehiculos WHERE id=?", (b["vehiculo_id"],), one=True)
    o = q("SELECT * FROM documentos WHERE id=?", (b["orden_id"],), one=True)
    p = q("SELECT * FROM documentos WHERE id=?", (o["origen_id"],), one=True) if o and o["origen_id"] else None
    lineas = q("SELECT * FROM lineas WHERE doc_tipo='boleta' AND doc_id=? ORDER BY id", (bid,))
    return render_template("boleta.html", b=b, c=c, v=v, o=o, p=p, lineas=lineas,
                           total=total_lineas("boleta", bid), tab=request.args.get("tab", "datos"))


@app.route("/boleta/<int:bid>/cobrar", methods=["POST"])
@login_required
def boleta_cobrar(bid):
    b = q("SELECT * FROM boletas WHERE id=?", (bid,), one=True) or abort(404)
    try:
        monto = float(request.form.get("monto") or 0)
    except ValueError:
        monto = 0
    pendiente = total_lineas("boleta", bid) - b["cobrado"]
    monto = min(monto, pendiente)
    if monto > 0:
        ex("UPDATE boletas SET cobrado=cobrado+?, forma_pago=? WHERE id=?",
           (monto, request.form.get("forma_pago") or b["forma_pago"], bid))
        flash(f"Cobro registrado: {clp(monto)}", "ok")
    return redirect(url_for("boleta", bid=bid))


@app.route("/boletas")
@login_required
def boletas():
    rows = q("""SELECT b.*, c.nombre, c.apellido1, c.apellido2, v.patente FROM boletas b
                JOIN clientes c ON c.id=b.cliente_id JOIN vehiculos v ON v.id=b.vehiculo_id ORDER BY b.id DESC""")
    return render_template("boletas.html", boletas=rows)


# ------------------------------------------------------- PDF y envío
def pdf_documento(tipo, did):
    """Devuelve (bytes del PDF, nombre de archivo, cliente) de una boleta, presupuesto u orden."""
    t = dict(q("SELECT * FROM taller WHERE id=1", one=True))
    logo = q("SELECT datos FROM archivos WHERE clave='logo'", one=True)
    if tipo == "boleta":
        doc = q("SELECT * FROM boletas WHERE id=?", (did,), one=True) or abort(404)
        orden = q("SELECT * FROM documentos WHERE id=?", (doc["orden_id"],), one=True)
        titulo = "Boleta"
        meta = [("Fecha", fecha(doc["fecha"])), ("Vencimiento", fecha(doc["vencimiento"])),
                ("Forma de pago", doc["forma_pago"]), ("N° Orden", orden["numero"] if orden else "-")]
    else:
        doc = q("SELECT * FROM documentos WHERE id=? AND tipo=?", (did, tipo), one=True) or abort(404)
        orden = doc if tipo == "orden" else None
        titulo = "Presupuesto" if tipo == "presupuesto" else "Orden de reparación"
        meta = [("Fecha entrada", fechahora(doc["fecha"])), ("Kilómetros", doc["km"] or "-"),
                ("Tipo intervención", doc["tipo_intervencion"] or "-"), ("Estado", doc["estado"])]
        if tipo == "orden" and doc["fecha_salida"]:
            meta.append(("Fecha salida", fechahora(doc["fecha_salida"])))
    c = q("SELECT * FROM clientes WHERE id=?", (doc["cliente_id"],), one=True)
    v = q("SELECT * FROM vehiculos WHERE id=?", (doc["vehiculo_id"],), one=True)
    lineas = q("SELECT * FROM lineas WHERE doc_tipo=? AND doc_id=? ORDER BY id", (tipo, did))
    total = total_lineas(tipo, did)
    neto = total / (1 + (t["iva"] or 0) / 100)
    totales = [("Neto", neto, False), (f"IVA ({t['iva']:g}%)", total - neto, False), ("TOTAL", total, True)]
    if tipo == "boleta":
        totales += [("Cobrado", doc["cobrado"], False), ("Pendiente", total - doc["cobrado"], False)]
    cliente = [("Cliente", f"{c['numero']} - {nombre_cliente(c)}"), ("RUT", c["rut"] or "-"),
               ("Dirección", " ".join(x for x in [c["direccion"], c["comuna"], c["ciudad"]] if x) or "-"),
               ("Teléfono", " / ".join(x for x in [c["telefono"], c["movil"]] if x) or "-"),
               ("e-Mail", c["email"] or "-")]
    vehiculo = [("Patente", v["patente"]), ("Vehículo", f"{v['marca'] or ''} {v['modelo'] or ''}"),
                ("Año", v["anio"] or "-"), ("N° Chasis", v["chasis"] or "-"), ("Color", v["color"] or "-")]
    secciones = []
    if tipo != "boleta":
        secciones.append(("Descripción de la revisión / avería", doc["descripcion"]))
    if orden:
        ing = q("SELECT * FROM ingresos WHERE orden_id=?", (orden["id"],), one=True)
        if ing:
            secciones.append(("Estado del vehículo al ingreso", texto_ingreso(ing)))
        trabajos = q("""SELECT t.*, e.nombre empleado FROM trabajos t LEFT JOIN empleados e ON e.id=t.empleado_id
                        WHERE t.orden_id=? ORDER BY t.fecha, t.id""", (orden["id"],))
        secciones.append(("Trabajos realizados", "\n".join(map(texto_trabajo, trabajos))))
        secciones.append(("Comentarios mecánicos", orden["comentarios"]))
    secciones += [("Observaciones del cliente", c["observaciones"]),
                  ("Observaciones del vehículo", v["observaciones"])]
    contenido = documentos_pdf.generar(t, logo["datos"] if logo else None, titulo, doc["numero"], meta,
                                       cliente, vehiculo, agrupar_lineas(lineas), totales, secciones, clp)
    return contenido, f"{titulo.replace(' ', '_')}_{doc['numero']}.pdf", c, doc["numero"]


def texto_trabajo(t):
    partes = [f"- {fechahora(t['fecha'])}  {t['descripcion']}"]
    if t["empleado"]:
        partes.append(f"({t['empleado']})")
    if t["horas"]:
        partes.append(f"{t['horas']:g} h".replace(".", ","))
    return "  ".join(partes)


def texto_ingreso(ing):
    partes = [f"Fecha: {fechahora(ing['fecha'])}   Km: {ing['km'] or '-'}   Combustible: {ing['combustible'] or '-'}"]
    if ing["zonas"]:
        partes.append(f"Zonas con daños: {ing['zonas']}")
    if ing["danos"]:
        partes.append(f"Detalle de daños: {ing['danos']}")
    if ing["accesorios"]:
        partes.append(f"Accesorios recibidos: {ing['accesorios']}")
    if ing["objetos"]:
        partes.append(f"Objetos de valor: {ing['objetos']}")
    if ing["observaciones"]:
        partes.append(f"Observaciones: {ing['observaciones']}")
    return "\n".join(partes)


def _respuesta_pdf(contenido, nombre):
    return Response(contenido, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre}"'})


@app.route("/<any(presupuesto,orden,boleta):tipo>/<int:did>/pdf")
@login_required
def documento_pdf(tipo, did):
    contenido, nombre, _, _ = pdf_documento(tipo, did)
    return _respuesta_pdf(contenido, nombre)


@app.route("/boleta/<int:bid>/imprimir")
@login_required
def boleta_imprimir(bid):
    return redirect(url_for("documento_pdf", tipo="boleta", did=bid))


def _firmador():
    return URLSafeTimedSerializer(app.secret_key, salt="documento-publico")


def enlace_publico(tipo, did):
    """Link que el cliente puede abrir sin iniciar sesión (válido 60 días)."""
    return url_for("documento_publico", token=_firmador().dumps([tipo, did]), _external=True)


@app.route("/d/<token>")
def documento_publico(token):
    try:
        tipo, did = _firmador().loads(token, max_age=60 * 24 * 3600)
    except BadSignature:
        abort(404)
    if tipo not in ("presupuesto", "orden", "boleta"):
        abort(404)
    contenido, nombre, _, _ = pdf_documento(tipo, did)
    return _respuesta_pdf(contenido, nombre)


def smtp_configurado():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER"))


app.jinja_env.globals.update(enlace_publico=enlace_publico, smtp_configurado=smtp_configurado)


def enviar_correo(destino, asunto, cuerpo, adjunto=None, nombre_adjunto=None):
    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["To"] = destino
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    if adjunto:
        msg.add_attachment(adjunto, maintype="application", subtype="pdf", filename=nombre_adjunto)
    puerto = int(os.environ.get("SMTP_PORT", 587))
    clase = smtplib.SMTP_SSL if puerto == 465 else smtplib.SMTP
    with clase(os.environ["SMTP_HOST"], puerto, timeout=20) as s:
        if puerto != 465:
            s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def telefono_whatsapp(numero):
    """Normaliza un teléfono chileno al formato internacional que pide wa.me (569XXXXXXXX)."""
    n = re.sub(r"\D", "", numero or "")
    if len(n) == 9 and n.startswith("9"):
        n = "56" + n
    elif len(n) == 8:
        n = "569" + n
    return n


@app.route("/<any(presupuesto,orden,boleta):tipo>/<int:did>/enviar", methods=["POST"])
@login_required
def documento_enviar(tipo, did):
    canal = request.form.get("canal")
    destino = request.form.get("destino", "").strip()
    volver = url_for("boleta", bid=did) if tipo == "boleta" else url_for("documento", tipo=tipo, did=did)
    contenido, nombre, c, numero = pdf_documento(tipo, did)
    t = q("SELECT nombre, telefono FROM taller WHERE id=1", one=True)
    doc_txt = {"boleta": "boleta", "presupuesto": "presupuesto", "orden": "orden de reparación"}[tipo]
    link = enlace_publico(tipo, did)
    if canal == "whatsapp":
        tel = telefono_whatsapp(destino)
        if len(tel) < 10:
            flash("Número de teléfono no válido para WhatsApp", "error")
            return redirect(volver)
        texto = f"Hola {c['nombre']}, le enviamos su {doc_txt} N° {numero} de {t['nombre']} en PDF: {link}"
        return redirect(f"https://wa.me/{tel}?text={quote(texto)}")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", destino):
        flash("Correo electrónico no válido", "error")
        return redirect(volver)
    if not smtp_configurado():
        flash("El envío de correos no está configurado en el servidor (variables SMTP_HOST, SMTP_USER, "
              "SMTP_PASSWORD). Usa el botón «Abrir en mi correo».", "error")
        return redirect(volver)
    try:
        enviar_correo(destino, f"{doc_txt.capitalize()} - {t['nombre']}",
                      f"Hola {c['nombre']},\n\nAdjuntamos su {doc_txt} en PDF.\n"
                      f"También puede descargarla aquí: {link}\n\nSaludos,\n{t['nombre']}\n{t['telefono'] or ''}",
                      contenido, nombre)
        flash(f"PDF enviado por correo a {destino}", "ok")
    except (OSError, smtplib.SMTPException) as e:
        flash(f"No se pudo enviar el correo: {e}", "error")
    return redirect(volver)


# ------------------------------------------------------------ caja
@app.route("/caja", methods=["GET", "POST"])
@login_required
def caja():
    if request.method == "POST":
        try:
            monto = float(request.form["monto"])
        except (KeyError, ValueError):
            flash("Monto inválido", "error")
            return redirect(url_for("caja"))
        ex("INSERT INTO caja(fecha,tipo,concepto,monto) VALUES(?,?,?,?)",
           (request.form.get("fecha") or date.today().isoformat(), request.form["tipo"],
            request.form.get("concepto", ""), monto))
        flash("Movimiento registrado", "ok")
        return redirect(url_for("caja"))
    return render_template("caja.html", movs=q("SELECT * FROM caja ORDER BY fecha DESC, id DESC LIMIT 100"),
                           hoy=date.today().isoformat())


# ---------------------------------------------------------- configuración
TALLER_CAMPOS = ["nombre", "razon_social", "rut", "giro", "direccion", "ciudad", "telefono", "email", "web",
                 "banco", "tipo_cuenta", "num_cuenta", "pie_documentos", "nombre_app"]


@app.route("/configuracion", methods=["GET", "POST"])
@admin_required
def configuracion():
    if request.method == "POST":
        f = request.form
        try:
            iva = float(f.get("iva") or 19)
            tarifa = float(f.get("tarifa_hora") or 0)
        except ValueError:
            flash("IVA y tarifa por hora deben ser números", "error")
            return redirect(url_for("configuracion"))
        colores = [c if re.fullmatch(r"#[0-9a-fA-F]{6}", c or "") else d
                   for c, d in ((f.get("color1"), "#0d3b66"), (f.get("color2"), "#1fb5c4"))]
        ex(f"UPDATE taller SET {','.join(k + '=?' for k in TALLER_CAMPOS)}, iva=?, tarifa_hora=?, color1=?, color2=? "
           "WHERE id=1", [f.get(k, "").strip() for k in TALLER_CAMPOS] + [iva, tarifa, *colores])
        logo = request.files.get("logo")
        if logo and logo.filename:
            if not (logo.mimetype or "").startswith("image/") or logo.mimetype == "image/svg+xml":
                flash("El logo debe ser una imagen PNG o JPG", "error")
            else:
                ex("INSERT OR REPLACE INTO archivos(clave,mime,datos,actualizado) VALUES('logo',?,?,?)",
                   (logo.mimetype, logo.read(), datetime.now().strftime("%Y%m%d%H%M%S")))
        if f.get("quitar_logo"):
            ex("DELETE FROM archivos WHERE clave='logo'")
        flash("Configuración general actualizada", "ok")
        return redirect(url_for("configuracion"))
    return render_template("configuracion.html", smtp=smtp_configurado())


@app.route("/logo")
def logo():
    a = q("SELECT mime, datos FROM archivos WHERE clave='logo'", one=True) or abort(404)
    return Response(a["datos"], mimetype=a["mime"], headers={"Cache-Control": "public, max-age=86400"})


@app.route("/configuracion/usuarios", methods=["GET", "POST"])
@admin_required
def config_usuarios():
    if request.method == "POST":
        f = request.form
        user, clave = f.get("username", "").strip(), f.get("password", "")
        if not user or len(clave) < 6:
            flash("Usuario obligatorio y contraseña de al menos 6 caracteres", "error")
        elif q("SELECT 1 FROM usuarios WHERE username=?", (user,), one=True):
            flash(f"El usuario «{user}» ya existe", "error")
        else:
            ex("INSERT INTO usuarios(username,password_hash,nombre,codigo_taller,rol) VALUES(?,?,?,?,?)",
               (user, generate_password_hash(clave), f.get("nombre", "").strip() or user, session.get("codigo"),
                "admin" if f.get("rol") == "admin" else "usuario"))
            flash("Usuario creado", "ok")
        return redirect(url_for("config_usuarios"))
    return render_template("config_usuarios.html", usuarios=q("SELECT * FROM usuarios ORDER BY id"))


@app.route("/configuracion/usuarios/<int:uid>/borrar", methods=["POST"])
@admin_required
def usuario_borrar(uid):
    u = q("SELECT * FROM usuarios WHERE id=?", (uid,), one=True) or abort(404)
    admins = q("SELECT COUNT(*) c FROM usuarios WHERE rol='admin'", one=True)["c"]
    if uid == session["uid"]:
        flash("No puedes eliminar tu propio usuario", "error")
    elif u["rol"] == "admin" and admins <= 1:
        flash("Debe quedar al menos un administrador", "error")
    else:
        ex("DELETE FROM usuarios WHERE id=?", (uid,))
        flash("Usuario eliminado", "ok")
    return redirect(url_for("config_usuarios"))


@app.route("/configuracion/alertas", methods=["GET", "POST"])
@admin_required
def config_alertas():
    if request.method == "POST":
        for clave, _, _, defecto in ALERTAS_DEF:
            dias = request.form.get(f"dias_{clave}", "")
            ex("UPDATE alertas_config SET activa=?, dias=? WHERE clave=?",
               (1 if request.form.get(f"activa_{clave}") else 0, int(dias) if dias.isdigit() else defecto, clave))
        flash("Configuración de alertas guardada", "ok")
        return redirect(url_for("config_alertas"))
    cfg = {r["clave"]: r for r in q("SELECT * FROM alertas_config")}
    return render_template("config_alertas.html", defs=ALERTAS_DEF, cfg=cfg)


@app.route("/papelera")
@admin_required
def papelera():
    return render_template("papelera.html", items=q("SELECT id, tabla, descripcion, fecha, usuario FROM papelera "
                                                    "ORDER BY id DESC"), nombres=PAPELERA)


@app.route("/papelera/<int:pid>/restaurar", methods=["POST"])
@admin_required
def papelera_restaurar(pid):
    p = q("SELECT * FROM papelera WHERE id=?", (pid,), one=True) or abort(404)
    datos = json.loads(p["datos"])
    if p["tabla"] == "vehiculos" and q("SELECT 1 FROM vehiculos WHERE patente=?", (datos.get("patente"),), one=True):
        flash(f"Ya existe otro vehículo con la patente {datos.get('patente')}", "error")
        return redirect(url_for("papelera"))
    try:
        _insertar(p["tabla"], datos)
        for tabla, filas in json.loads(p["hijos"] or "{}").items():
            for fila in filas:
                _insertar(tabla, fila)
        db().execute("DELETE FROM papelera WHERE id=?", (pid,))
        db().commit()
        flash(f"{PAPELERA[p['tabla']][0]} «{p['descripcion']}» restaurado", "ok")
    except sqlite3.IntegrityError:
        db().rollback()
        flash("No se pudo restaurar: hay un registro con el mismo número/código, o el cliente "
              "dueño del vehículo también está en la papelera (restáuralo primero)", "error")
    return redirect(url_for("papelera"))


@app.route("/papelera/<int:pid>/eliminar", methods=["POST"])
@app.route("/papelera/vaciar", methods=["POST"])
@admin_required
def papelera_eliminar(pid=None):
    if pid:
        ex("DELETE FROM papelera WHERE id=?", (pid,))
        flash("Eliminado definitivamente", "ok")
    else:
        ex("DELETE FROM papelera")
        flash("Papelera vaciada", "ok")
    return redirect(url_for("papelera"))


@app.route("/pronto/<seccion>")
@login_required
def pronto(seccion):
    if seccion == "almacen":
        return redirect(url_for("almacen"))
    return render_template("pronto.html", seccion=seccion.replace("-", " ").capitalize())


# ------------------------------------------------------------- proveedores
@app.route("/proveedores")
@login_required
def proveedores():
    s = request.args.get("q", "").strip()
    like = f"%{s}%"
    rows = q("""SELECT * FROM proveedores WHERE ?='' OR nombre LIKE ? OR rut LIKE ? OR rubro LIKE ?
                OR contacto LIKE ? ORDER BY nombre""", (s, like, like, like, like))
    return render_template("proveedores.html", proveedores=rows, s=s)


@app.route("/proveedores/nuevo", methods=["GET", "POST"])
@app.route("/proveedores/<int:pid>/editar", methods=["GET", "POST"])
@login_required
def proveedor_form(pid=None):
    p = q("SELECT * FROM proveedores WHERE id=?", (pid,), one=True) if pid else {}
    if pid and not p:
        abort(404)
    if request.method == "POST":
        data = [request.form.get(k, "").strip() for k in PROVEEDOR_CAMPOS]
        if not request.form.get("nombre", "").strip():
            flash("El nombre del proveedor es obligatorio", "error")
            return render_template("proveedor_form.html", p=request.form, editar=bool(pid))
        if pid:
            ex(f"UPDATE proveedores SET {','.join(k + '=?' for k in PROVEEDOR_CAMPOS)} WHERE id=?", [*data, pid])
        else:
            ex(f"INSERT INTO proveedores({','.join(PROVEEDOR_CAMPOS)},creado) "
               f"VALUES({','.join('?'*len(PROVEEDOR_CAMPOS))},?)", [*data, datetime.now().isoformat()])
        flash("Proveedor guardado", "ok")
        return redirect(url_for("proveedores"))
    return render_template("proveedor_form.html", p=p, editar=bool(pid))


@app.route("/proveedores/<int:pid>/borrar", methods=["POST"])
@login_required
def proveedor_borrar(pid):
    p = q("SELECT * FROM proveedores WHERE id=?", (pid,), one=True) or abort(404)
    a_papelera("proveedores", pid, p["nombre"])
    flash("Proveedor enviado a la papelera", "ok")
    return redirect(url_for("proveedores"))


# --------------------------------------------------------------- empleados
@app.route("/empleados")
@login_required
def empleados():
    return render_template("empleados.html", empleados=q("SELECT * FROM empleados ORDER BY activo DESC, nombre"))


@app.route("/empleados/nuevo", methods=["GET", "POST"])
@app.route("/empleados/<int:eid>/editar", methods=["GET", "POST"])
@login_required
def empleado_form(eid=None):
    e = q("SELECT * FROM empleados WHERE id=?", (eid,), one=True) if eid else {}
    if eid and not e:
        abort(404)
    if request.method == "POST":
        f = request.form
        pin = f.get("pin", "").strip()
        if not f.get("nombre", "").strip():
            flash("El nombre es obligatorio", "error")
            return render_template("empleado_form.html", e=f, editar=bool(eid))
        if (pin or not eid) and not re.fullmatch(r"\d{4,6}", pin):
            flash("El PIN para marcar asistencia debe tener de 4 a 6 dígitos", "error")
            return render_template("empleado_form.html", e=f, editar=bool(eid))
        data = [f.get(k, "").strip() for k in EMPLEADO_CAMPOS] + [1 if f.get("activo") or not eid else 0]
        if eid:
            ex(f"UPDATE empleados SET {','.join(k + '=?' for k in EMPLEADO_CAMPOS)}, activo=? WHERE id=?", [*data, eid])
        else:
            eid = ex(f"INSERT INTO empleados({','.join(EMPLEADO_CAMPOS)},activo,creado) "
                     f"VALUES({','.join('?'*len(EMPLEADO_CAMPOS))},?,?)", [*data, datetime.now().isoformat()])
        if pin:
            ex("UPDATE empleados SET pin_hash=? WHERE id=?", (generate_password_hash(pin), eid))
        flash("Empleado guardado", "ok")
        return redirect(url_for("empleados"))
    return render_template("empleado_form.html", e=e, editar=bool(eid))


@app.route("/empleados/<int:eid>/borrar", methods=["POST"])
@login_required
def empleado_borrar(eid):
    e = q("SELECT * FROM empleados WHERE id=?", (eid,), one=True) or abort(404)
    a_papelera("empleados", eid, e["nombre"])
    flash("Empleado enviado a la papelera", "ok")
    return redirect(url_for("empleados"))


# ---------------------------------------- control de asistencia (entrada / salida)
@app.route("/asistencia", methods=["GET", "POST"])
@login_required
def asistencia():
    if request.method == "POST":
        e = q("SELECT * FROM empleados WHERE id=? AND activo=1", (request.form.get("empleado_id"),), one=True)
        if not e or not e["pin_hash"] or not check_password_hash(e["pin_hash"], request.form.get("pin", "")):
            flash("Empleado o PIN incorrecto", "error")
            return redirect(url_for("asistencia"))
        ultima = q("SELECT tipo FROM marcaciones WHERE empleado_id=? AND fecha LIKE ? ORDER BY fecha DESC, id DESC",
                   (e["id"], date.today().isoformat() + "%"), one=True)
        tipo = "salida" if ultima and ultima["tipo"] == "entrada" else "entrada"
        ex("INSERT INTO marcaciones(empleado_id,tipo,fecha) VALUES(?,?,?)", (e["id"], tipo, ahora()))
        flash(f"{e['nombre']}: {tipo.upper()} registrada a las {datetime.now():%H:%M}", "ok")
        return redirect(url_for("asistencia"))
    hoy = q("""SELECT m.*, e.nombre FROM marcaciones m LEFT JOIN empleados e ON e.id=m.empleado_id
               WHERE m.fecha LIKE ? ORDER BY m.fecha DESC, m.id DESC""", (date.today().isoformat() + "%",))
    return render_template("asistencia.html", hoy=hoy,
                           empleados=q("SELECT id, nombre FROM empleados WHERE activo=1 AND pin_hash IS NOT NULL "
                                       "ORDER BY nombre"))


def horas_trabajadas(marcas):
    """Suma las horas emparejando cada entrada con la siguiente salida del mismo empleado."""
    total, abierta = 0.0, None
    for m in sorted(marcas, key=lambda m: m["fecha"]):
        if m["tipo"] == "entrada":
            abierta = m["fecha"]
        elif abierta:
            total += (datetime.fromisoformat(m["fecha"]) - datetime.fromisoformat(abierta)).total_seconds() / 3600
            abierta = None
    return total


@app.route("/asistencia/registros")
@login_required
def asistencia_registros():
    hoy = date.today()
    desde = request.args.get("desde") or (hoy - timedelta(days=hoy.weekday())).isoformat()
    hasta = request.args.get("hasta") or hoy.isoformat()
    eid = request.args.get("empleado", "")
    marcas = q("""SELECT m.*, e.nombre FROM marcaciones m LEFT JOIN empleados e ON e.id=m.empleado_id
                  WHERE substr(m.fecha,1,10) BETWEEN ? AND ? AND (?='' OR m.empleado_id=?)
                  ORDER BY m.fecha DESC, m.id DESC""", (desde, hasta, eid, eid))
    resumen = {}
    for m in marcas:
        resumen.setdefault(m["nombre"] or "(empleado eliminado)", []).append(m)
    resumen = {k: horas_trabajadas(v) for k, v in resumen.items()}
    return render_template("asistencia_registros.html", marcas=marcas, resumen=resumen, desde=desde,
                           hasta=hasta, eid=eid, empleados=q("SELECT id, nombre FROM empleados ORDER BY nombre"))


@app.route("/asistencia/marca", methods=["POST"])
@app.route("/asistencia/marca/<int:mid>", methods=["POST"])
@admin_required
def asistencia_modificar(mid=None):
    """Solo administradores: corregir, agregar o borrar marcas (olvidos, emergencias)."""
    f = request.form
    motivo = f.get("motivo", "").strip()
    volver = redirect(request.referrer or url_for("asistencia_registros"))
    if not motivo:
        flash("Indica el motivo de la modificación", "error")
        return volver
    editado = f"{session.get('nombre')} {ahora()}"
    if f.get("accion") == "borrar" and mid:
        ex("DELETE FROM marcaciones WHERE id=?", (mid,))
        flash("Marca eliminada", "ok")
        return volver
    try:
        cuando = datetime.fromisoformat(f.get("fecha", "")).isoformat(timespec="minutes")
    except ValueError:
        flash("Fecha y hora no válidas", "error")
        return volver
    tipo = "salida" if f.get("tipo") == "salida" else "entrada"
    if mid:
        ex("UPDATE marcaciones SET fecha=?, tipo=?, editado_por=?, motivo=? WHERE id=?",
           (cuando, tipo, editado, motivo, mid))
        flash("Marca modificada", "ok")
    else:
        if not q("SELECT 1 FROM empleados WHERE id=?", (f.get("empleado_id"),), one=True):
            flash("Selecciona un empleado", "error")
            return volver
        ex("INSERT INTO marcaciones(empleado_id,tipo,fecha,editado_por,motivo) VALUES(?,?,?,?,?)",
           (f.get("empleado_id"), tipo, cuando, editado, motivo))
        flash("Marca agregada manualmente", "ok")
    return volver


# ----------------------------------------------------------------- almacén
def _articulo_form(item, editar=False):
    return render_template("almacen_form.html", item=item, editar=editar,
                           proveedores=q("SELECT nombre FROM proveedores ORDER BY nombre"))


@app.route('/almacen')
@login_required
def almacen():
    s = request.args.get('q', '').strip()
    like = f"%{s}%"
    rows = q("SELECT * FROM almacen WHERE ?='' OR codigo LIKE ? OR nombre LIKE ? OR proveedor LIKE ? ORDER BY id DESC",
             (s, like, like, like))
    return render_template('almacen.html', items=rows, s=s)


def _leer_articulo():
    f = request.form
    codigo, nombre = f.get('codigo', '').strip(), f.get('nombre', '').strip()
    if not codigo or not nombre:
        raise ValueError('Código y nombre son obligatorios')
    try:
        nums = [float(f.get(k) or 0) for k in ('stock', 'precio', 'stock_minimo')]
    except ValueError:
        raise ValueError('Stock, precio y stock mínimo deben ser numéricos') from None
    return [codigo, nombre, f.get('descripcion', ''), *nums, f.get('proveedor', '')]


@app.route('/almacen/nuevo', methods=['GET', 'POST'])
@login_required
def almacen_nuevo():
    if request.method == 'POST':
        try:
            datos = _leer_articulo()
        except ValueError as e:
            flash(str(e), 'error')
            return _articulo_form(request.form)
        try:
            ex("INSERT INTO almacen(codigo,nombre,descripcion,stock,precio,stock_minimo,proveedor,creado) "
               "VALUES(?,?,?,?,?,?,?,?)", (*datos, datetime.now().isoformat()))
        except sqlite3.IntegrityError:
            flash(f'Ya existe un artículo con el código {datos[0]}', 'error')
            return _articulo_form(request.form)
        flash('Artículo creado', 'ok')
        volver = request.args.get('volver', '')
        return redirect(volver if volver.startswith('/') and not volver.startswith('//') else url_for('almacen'))
    return _articulo_form({})


@app.route('/almacen/<int:aid>/editar', methods=['GET', 'POST'])
@login_required
def almacen_editar(aid):
    item = q('SELECT * FROM almacen WHERE id=?', (aid,), one=True) or abort(404)
    if request.method == 'POST':
        try:
            datos = _leer_articulo()
        except ValueError as e:
            flash(str(e), 'error')
            return _articulo_form(request.form, editar=True)
        try:
            ex('UPDATE almacen SET codigo=?,nombre=?,descripcion=?,stock=?,precio=?,stock_minimo=?,proveedor=? '
               'WHERE id=?', (*datos, aid))
        except sqlite3.IntegrityError:
            flash(f'Ya existe un artículo con el código {datos[0]}', 'error')
            return _articulo_form(request.form, editar=True)
        flash('Artículo actualizado', 'ok')
        return redirect(url_for('almacen'))
    return _articulo_form(item, editar=True)


@app.route('/almacen/<int:aid>/borrar', methods=['POST'])
@login_required
def almacen_borrar(aid):
    a = q('SELECT * FROM almacen WHERE id=?', (aid,), one=True) or abort(404)
    a_papelera('almacen', aid, f"{a['codigo']} - {a['nombre']}")
    flash('Artículo enviado a la papelera', 'ok')
    return redirect(url_for('almacen'))


# ------------------------------------------------- facturas de compra
@app.route("/compras")
@login_required
def compras():
    rows = q("""SELECT c.*, p.nombre proveedor,
                  (SELECT COALESCE(SUM(cantidad*precio),0) FROM compra_lineas WHERE compra_id=c.id) total,
                  (SELECT COUNT(*) FROM compra_lineas WHERE compra_id=c.id AND recibido<cantidad) pendientes,
                  (SELECT COUNT(*) FROM compra_lineas WHERE compra_id=c.id) nlineas
                FROM compras c LEFT JOIN proveedores p ON p.id=c.proveedor_id ORDER BY c.fecha DESC, c.id DESC""")
    return render_template("compras.html", compras=rows)


@app.route("/compras/nueva", methods=["GET", "POST"])
@login_required
def compra_nueva():
    provs = q("SELECT id, nombre FROM proveedores ORDER BY nombre")
    if request.method == "POST":
        f = request.form
        if not f.get("proveedor_id") or not f.get("numero", "").strip():
            flash("Proveedor y número de factura son obligatorios", "error")
            return render_template("compra_form.html", proveedores=provs, f=f, hoy=date.today().isoformat())
        cid = ex("INSERT INTO compras(proveedor_id,numero,fecha,observaciones,creado) VALUES(?,?,?,?,?)",
                 (f["proveedor_id"], f["numero"].strip(), f.get("fecha") or date.today().isoformat(),
                  f.get("observaciones", ""), datetime.now().isoformat()))
        flash("Factura de compra creada: agrega ahora los repuestos", "ok")
        return redirect(url_for("compra", cid=cid))
    return render_template("compra_form.html", proveedores=provs, f={}, hoy=date.today().isoformat())


def _compra(cid):
    c = q("""SELECT c.*, p.nombre proveedor, p.rut prut FROM compras c LEFT JOIN proveedores p ON p.id=c.proveedor_id
             WHERE c.id=?""", (cid,), one=True) or abort(404)
    lineas = q("""SELECT l.*, a.codigo, a.nombre FROM compra_lineas l LEFT JOIN almacen a ON a.id=l.articulo_id
                  WHERE l.compra_id=? ORDER BY l.id""", (cid,))
    return c, lineas


@app.route("/compras/<int:cid>")
@login_required
def compra(cid):
    c, lineas = _compra(cid)
    return render_template("compra.html", c=c, lineas=lineas, total=sum(l["cantidad"] * l["precio"] for l in lineas),
                           articulos=q("SELECT id, codigo, nombre FROM almacen ORDER BY nombre"))


@app.route("/compras/<int:cid>/linea", methods=["POST"])
@login_required
def compra_linea(cid):
    _compra(cid)
    f = request.form
    try:
        cant, precio = float(f.get("cantidad") or 0), float(f.get("precio") or 0)
    except ValueError:
        cant = 0
    if cant <= 0 or not q("SELECT 1 FROM almacen WHERE id=?", (f.get("articulo_id"),), one=True):
        flash("Selecciona un repuesto y una cantidad mayor a cero", "error")
    else:
        ex("INSERT INTO compra_lineas(compra_id,articulo_id,cantidad,precio) VALUES(?,?,?,?)",
           (cid, f["articulo_id"], cant, precio))
    return redirect(url_for("compra", cid=cid))


@app.route("/compras/linea/<int:lid>/borrar", methods=["POST"])
@login_required
def compra_linea_borrar(lid):
    l = q("SELECT * FROM compra_lineas WHERE id=?", (lid,), one=True) or abort(404)
    if l["recibido"]:
        flash("No se puede quitar: ese repuesto ya fue recibido en bodega", "error")
    else:
        ex("DELETE FROM compra_lineas WHERE id=?", (lid,))
    return redirect(url_for("compra", cid=l["compra_id"]))


@app.route("/compras/<int:cid>/pagar", methods=["POST"])
@login_required
def compra_pagar(cid):
    c, lineas = _compra(cid)
    if not c["pagada"]:
        ex("INSERT INTO caja(fecha,tipo,concepto,monto) VALUES(?,?,?,?)",
           (date.today().isoformat(), "gasto", f"Factura compra {c['numero']} - {c['proveedor']}",
            sum(l["cantidad"] * l["precio"] for l in lineas)))
        ex("UPDATE compras SET pagada=1 WHERE id=?", (cid,))
        flash("Pago registrado como gasto en caja", "ok")
    return redirect(url_for("compra", cid=cid))


@app.route("/compras/<int:cid>/borrar", methods=["POST"])
@login_required
def compra_borrar(cid):
    _, lineas = _compra(cid)
    if any(l["recibido"] for l in lineas):
        flash("No se puede eliminar: la factura ya tiene repuestos recibidos", "error")
        return redirect(url_for("compra", cid=cid))
    ex("DELETE FROM compra_lineas WHERE compra_id=?", (cid,))
    ex("DELETE FROM compras WHERE id=?", (cid,))
    flash("Factura de compra eliminada", "ok")
    return redirect(url_for("compras"))


# ------------------------------------------------- recepción de repuestos
def entrada_stock(articulo_id, cantidad, referencia):
    ex("UPDATE almacen SET stock=stock+? WHERE id=?", (cantidad, articulo_id))
    ex("INSERT INTO movimientos_stock(articulo_id,fecha,cantidad,referencia,usuario) VALUES(?,?,?,?,?)",
       (articulo_id, ahora(), cantidad, referencia, session.get("nombre")))


@app.route("/almacen/recepcion")
@login_required
def recepcion():
    pendientes = []
    for c in q("""SELECT c.*, p.nombre proveedor FROM compras c LEFT JOIN proveedores p ON p.id=c.proveedor_id
                  WHERE EXISTS (SELECT 1 FROM compra_lineas l WHERE l.compra_id=c.id AND l.recibido<l.cantidad)
                  ORDER BY c.fecha"""):
        pendientes.append((c, _compra(c["id"])[1]))
    movs = q("""SELECT m.*, a.codigo, a.nombre FROM movimientos_stock m LEFT JOIN almacen a ON a.id=m.articulo_id
                ORDER BY m.id DESC LIMIT 50""")
    return render_template("recepcion.html", pendientes=pendientes, movs=movs,
                           articulos=q("SELECT id, codigo, nombre, stock FROM almacen ORDER BY nombre"),
                           compra_sel=request.args.get("compra", type=int))


@app.route("/almacen/recepcion/<int:cid>", methods=["POST"])
@login_required
def recepcion_compra(cid):
    c, lineas = _compra(cid)
    n = 0
    for l in lineas:
        try:
            cant = float(request.form.get(f"recibir_{l['id']}") or 0)
        except ValueError:
            cant = 0
        cant = min(cant, l["cantidad"] - l["recibido"])
        if cant > 0:
            entrada_stock(l["articulo_id"], cant, f"Factura compra {c['numero']} - {c['proveedor']}")
            ex("UPDATE compra_lineas SET recibido=recibido+? WHERE id=?", (cant, l["id"]))
            n += 1
    flash(f"Recepción registrada: {n} repuesto/s ingresados a bodega" if n else "No se ingresó ninguna cantidad",
          "ok" if n else "error")
    return redirect(url_for("recepcion"))


@app.route("/almacen/recepcion/directa", methods=["POST"])
@login_required
def recepcion_directa():
    f = request.form
    try:
        cant = float(f.get("cantidad") or 0)
    except ValueError:
        cant = 0
    if cant <= 0 or not q("SELECT 1 FROM almacen WHERE id=?", (f.get("articulo_id"),), one=True):
        flash("Selecciona un repuesto y una cantidad mayor a cero", "error")
    else:
        entrada_stock(f["articulo_id"], cant, f.get("referencia", "").strip() or "Recepción sin factura")
        flash("Repuesto ingresado a bodega", "ok")
    return redirect(url_for("recepcion"))


# ------------------------------------------------------------------- citas
def _guardar_cita(f, origen):
    """Valida y guarda una cita. Devuelve un mensaje de error o None."""
    nombre, tel = f.get("nombre", "").strip(), f.get("telefono", "").strip()
    try:
        dia = date.fromisoformat(f.get("fecha", ""))
    except ValueError:
        return "Indica una fecha válida"
    if not nombre or not tel:
        return "Nombre y teléfono son obligatorios"
    if dia < date.today():
        return "La fecha no puede ser anterior a hoy"
    ex("""INSERT INTO citas(nombre,telefono,email,patente,fecha,hora,motivo,estado,origen,creado)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
       (nombre[:120], tel[:40], f.get("email", "").strip()[:120],
        patente_provider.normalizar(f.get("patente", ""))[:10], dia.isoformat(), f.get("hora", "")[:5],
        f.get("motivo", "").strip()[:1000], "PENDIENTE" if origen == "web" else "CONFIRMADA", origen, ahora()))
    return None


@app.route("/agendar", methods=["GET", "POST"])
def agendar():
    """Página pública (sin login) a la que lleva el código QR."""
    if request.method == "POST":
        if request.form.get("web"):  # campo trampa: solo lo rellenan los bots
            return render_template("agendar.html", listo=True)
        error = _guardar_cita(request.form, "web")
        if error:
            flash(error, "error")
            return render_template("agendar.html", f=request.form, hoy=date.today().isoformat())
        return render_template("agendar.html", listo=True)
    return render_template("agendar.html", f={}, hoy=date.today().isoformat())


@app.route("/citas", methods=["GET", "POST"])
@login_required
def citas():
    if request.method == "POST":
        error = _guardar_cita(request.form, "taller")
        flash(error or "Cita agendada", "error" if error else "ok")
        return redirect(url_for("citas"))
    estado = request.args.get("estado", "")
    rows = q("""SELECT * FROM citas WHERE (?='' OR estado=?) AND (?<>'' OR fecha>=?)
                ORDER BY fecha, hora""", (estado, estado, estado, date.today().isoformat()))
    return render_template("citas.html", citas=rows, estado=estado, hoy=date.today().isoformat())


@app.route("/citas/<int:cid>/estado", methods=["POST"])
@login_required
def cita_estado(cid):
    nuevo = request.form.get("estado")
    if nuevo == "BORRAR":
        c = q("SELECT * FROM citas WHERE id=?", (cid,), one=True) or abort(404)
        a_papelera("citas", cid, f"{c['nombre']} {fecha(c['fecha'])} {c['hora'] or ''}")
        flash("Cita enviada a la papelera", "ok")
    elif nuevo in ("PENDIENTE", "CONFIRMADA", "ATENDIDA", "CANCELADA"):
        ex("UPDATE citas SET estado=? WHERE id=?", (nuevo, cid))
    return redirect(request.referrer or url_for("citas"))


@app.route("/marketing/qr")
@login_required
def marketing_qr():
    url = url_for("agendar", _external=True)
    return render_template("qr.html", url=url, svg=segno.make(url, error="m").svg_inline(scale=8, dark="#111"))


@app.route("/marketing/qr.<any(png,svg):fmt>")
@login_required
def marketing_qr_archivo(fmt):
    buf = BytesIO()
    segno.make(url_for("agendar", _external=True), error="m").save(buf, kind=fmt, scale=20, border=2)
    return Response(buf.getvalue(), mimetype="image/png" if fmt == "png" else "image/svg+xml",
                    headers={"Content-Disposition": f'attachment; filename="qr-agendar-cita.{fmt}"'})


# ------------------------------------------------- soporte y capacitación
@app.route("/soporte", methods=["GET", "POST"])
@login_required
def soporte():
    if request.method == "POST":
        f = request.form
        if not f.get("asunto", "").strip() or not f.get("fecha"):
            flash("Indica el asunto y la fecha que te acomoda", "error")
            return redirect(url_for("soporte"))
        tipo = "Capacitación" if f.get("tipo") == "Capacitación" else "Soporte técnico"
        ex("""INSERT INTO soporte(tipo,fecha,hora,asunto,detalle,contacto,estado,usuario,creado)
              VALUES(?,?,?,?,?,?,'SOLICITADA',?,?)""",
           (tipo, f["fecha"], f.get("hora", ""), f["asunto"].strip(), f.get("detalle", "").strip(),
            f.get("contacto", "").strip(), session.get("nombre"), ahora()))
        aviso = os.environ.get("SOPORTE_EMAIL")
        if aviso and smtp_configurado():
            try:
                t = q("SELECT nombre FROM taller WHERE id=1", one=True)
                enviar_correo(aviso, f"[{tipo}] {f['asunto'].strip()} - {t['nombre']}",
                              f"Taller: {t['nombre']}\nUsuario: {session.get('nombre')}\n"
                              f"Fecha solicitada: {f['fecha']} {f.get('hora', '')}\nContacto: {f.get('contacto', '')}"
                              f"\n\n{f.get('detalle', '')}")
            except (OSError, smtplib.SMTPException):
                pass  # la solicitud ya quedó guardada; el aviso por correo es opcional
        flash("Solicitud enviada. La administración de la página te confirmará la cita.", "ok")
        return redirect(url_for("soporte"))
    return render_template("soporte.html", solicitudes=q("SELECT * FROM soporte ORDER BY id DESC"),
                           hoy=date.today().isoformat(), whatsapp=os.environ.get("SOPORTE_WHATSAPP"),
                           correo=os.environ.get("SOPORTE_EMAIL"))


@app.route("/soporte/<int:sid>/estado", methods=["POST"])
@admin_required
def soporte_estado(sid):
    if request.form.get("estado") in ("SOLICITADA", "CONFIRMADA", "REALIZADA", "CANCELADA"):
        ex("UPDATE soporte SET estado=? WHERE id=?", (request.form["estado"], sid))
    return redirect(url_for("soporte"))


# Ruta de prueba (temporal) para crear cliente/vehículo/orden y emitir boleta automáticamente
@app.route('/__test_emit_boleta')
def __test_emit_boleta():
    # Crear cliente y vehículo de prueba
    now = datetime.now().isoformat()
    cid = ex("INSERT INTO clientes(numero,rut,nombre,apellido1,telefono,comuna,ciudad,pais,creado) VALUES(?,?,?,?,?,?,?,?,?)",
             (9999, '99.999.999-9', 'TEST', 'CLIENTE', '+56900000000', 'Santiago', 'Santiago', 'Chile', now))
    vid = ex("INSERT INTO vehiculos(cliente_id,patente,marca,modelo,km,creado) VALUES(?,?,?,?,?,?)",
             (cid, 'ZZZ999', 'TEST', 'MODEL', 1000, now))
    # Crear orden
    numero = siguiente('orden')
    did = ex("INSERT INTO documentos(tipo,numero,cliente_id,vehiculo_id,fecha,km,estado) VALUES(?,?,?,?,?,?,?)",
             ('orden', numero, cid, vid, now, 1000, 'ABIERTA'))
    # Añadir línea
    ex("INSERT INTO lineas(doc_tipo,doc_id,tipo,concepto,cantidad,precio,descuento) VALUES(?,?,?,?,?,?,?)",
       ('orden', did, 'Repuesto', 'Test linea', 1, 10000, 0))
    # Emitir boleta (usa la misma lógica que la ruta /orden/<did>/boleta)
    o = q("SELECT * FROM documentos WHERE id=? AND tipo='orden'", (did,), one=True)
    c = q("SELECT * FROM clientes WHERE id=?", (o['cliente_id'],), one=True)
    hoy = date.today().isoformat()
    bid = ex("""INSERT INTO boletas(numero,orden_id,cliente_id,vehiculo_id,fecha,vencimiento,forma_pago,condiciones_pago)
                VALUES(?,?,?,?,?,?,?,?)""",
             (siguiente('boleta'), did, o['cliente_id'], o['vehiculo_id'], hoy, hoy,
              c['forma_pago'] or 'Efectivo', c['condiciones_pago'] or '-- Sin Definir --'))
    copiar_lineas('orden', did, 'boleta', bid)
    return jsonify(dict(ok=True, cliente_id=cid, vehiculo_id=vid, orden_id=did, boleta_id=bid))


# Ruta de comprobación pública (temporal) para verificar que la vista 'Almacén' existe
@app.route('/__check_almacen_public')
def __check_almacen_public():
    return render_template('pronto.html', seccion='almacen')


init_db()

if __name__ == "__main__":
    app.run(debug=True)
