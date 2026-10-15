"""
TallerPro - Sistema de gestión para talleres mecánicos (Flask + SQLite)

Ejecutar:
    pip install -r requirements.txt
    python app.py
    -> http://127.0.0.1:5000   usuario: admin / clave: admin123 / código taller: 1001
"""
import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import patente_provider

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TALLER_DB", os.path.join(BASE, "taller.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")

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
"""


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
    if not conn.execute("SELECT 1 FROM usuarios").fetchone():
        conn.execute("INSERT INTO usuarios(username,password_hash,nombre,codigo_taller) VALUES(?,?,?,?)",
                     ("admin", generate_password_hash("admin123"), "Usuario Administrador", "1001"))
        conn.execute("INSERT INTO taller VALUES(1,?,?,?,?,?,?,19)",
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
def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if "uid" not in session:
            return redirect(url_for("login", next=request.path))
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


@app.context_processor
def globales():
    return dict(taller=q("SELECT * FROM taller WHERE id=1", one=True) if "uid" in session else None,
                usuario=session.get("nombre"), codigo=session.get("codigo"),
                alertas=q("SELECT COUNT(*) c FROM boletas b WHERE cobrado < "
                          "(SELECT COALESCE(SUM(cantidad*precio*(1-descuento/100.0)),0) FROM lineas "
                          "WHERE doc_tipo='boleta' AND doc_id=b.id)", one=True)["c"] if "uid" in session else 0)


def total_lineas(doc_tipo, doc_id):
    r = q("SELECT COALESCE(SUM(cantidad*precio*(1-descuento/100.0)),0) t FROM lineas "
          "WHERE doc_tipo=? AND doc_id=?", (doc_tipo, doc_id), one=True)
    return r["t"]


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


app.jinja_env.globals.update(nombre_cliente=nombre_cliente, total_lineas=total_lineas)

CLIENTE_CAMPOS = ["rut", "tipo", "nombre", "apellido1", "apellido2", "alias", "telefono", "movil",
                  "direccion", "cod_postal", "comuna", "ciudad", "pais", "email", "calidad",
                  "observaciones", "tarifa", "grupo_facturacion", "desc_piezas", "desc_mo",
                  "desc_gral", "forma_pago", "condiciones_pago", "iva_especial",
                  "cuenta_contable", "como_conocio"]
VEHICULO_CAMPOS = ["patente", "chasis", "tipo", "marca", "modelo", "version", "combustible",
                   "potencia", "unidades_odometro", "n_motor", "tipo_motor", "tipo_aceite",
                   "potencia_cv", "potencia_kw", "anio", "km", "color", "n_caja", "ref_bujias",
                   "fecha_placa", "rev_tecnica"]


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
    rt = q("""SELECT v.*, c.nombre, c.apellido1, c.apellido2, c.id cid FROM vehiculos v
              JOIN clientes c ON c.id=v.cliente_id WHERE rev_tecnica IS NOT NULL AND rev_tecnica<>''
              AND rev_tecnica <= ? ORDER BY rev_tecnica LIMIT 8""",
           ((hoy + timedelta(days=60)).isoformat(),))
    return render_template("dashboard.html", meses=meses, ingresos=ingresos, gastos=gastos,
                           resumen=resumen, rev_proximas=rt, actualizado=datetime.now())


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
    ex("DELETE FROM vehiculos WHERE cliente_id=?", (cid,))
    ex("DELETE FROM clientes WHERE id=?", (cid,))
    flash("Cliente eliminado", "ok")
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
    return render_template("vehiculos.html", vehiculos=rows, s=s)


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
        ex("DELETE FROM vehiculos WHERE id=?", (vid,))
        flash("Vehículo eliminado", "ok")
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
             (tipo, siguiente(tipo), v["cliente_id"], vid, datetime.now().isoformat(timespec="minutes"),
              v["km"], "PENDIENTE" if tipo == "presupuesto" else "ABIERTA"))
    return redirect(url_for("documento", tipo=tipo, did=did))


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
    return render_template("documento.html", d=d, tipo=tipo, c=c, v=v, lineas=lineas,
                           total=total_lineas(tipo, did), origen=origen, derivado=derivado,
                           boleta=boleta, tab=request.args.get("tab", "datos"))


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
              datetime.now().isoformat(timespec="minutes"), p["km"], p["descripcion"]))
    copiar_lineas("presupuesto", did, "orden", oid)
    ex("UPDATE documentos SET estado='ACEPTADO' WHERE id=?", (did,))
    flash("Orden de reparación creada desde el presupuesto", "ok")
    return redirect(url_for("documento", tipo="orden", did=oid))


@app.route("/orden/<int:did>/cerrar", methods=["POST"])
@login_required
def orden_cerrar(did):
    ex("UPDATE documentos SET estado='CERRADA', fecha_salida=COALESCE(fecha_salida,?) WHERE id=? AND tipo='orden'",
       (datetime.now().isoformat(timespec="minutes"), did))
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


@app.route("/boleta/<int:bid>/imprimir")
@login_required
def boleta_imprimir(bid):
    b = q("SELECT * FROM boletas WHERE id=?", (bid,), one=True) or abort(404)
    c = q("SELECT * FROM clientes WHERE id=?", (b["cliente_id"],), one=True)
    v = q("SELECT * FROM vehiculos WHERE id=?", (b["vehiculo_id"],), one=True)
    o = q("SELECT * FROM documentos WHERE id=?", (b["orden_id"],), one=True)
    p = q("SELECT * FROM documentos WHERE id=?", (o["origen_id"],), one=True) if o and o["origen_id"] else None
    lineas = q("SELECT * FROM lineas WHERE doc_tipo='boleta' AND doc_id=? ORDER BY id", (bid,))
    total = total_lineas("boleta", bid)
    iva = q("SELECT iva FROM taller WHERE id=1", one=True)["iva"]
    neto = total / (1 + iva / 100)
    return render_template("boleta_print.html", b=b, c=c, v=v, o=o, p=p, lineas=lineas,
                           total=total, neto=neto, iva_monto=total - neto, iva=iva)


@app.route("/boletas")
@login_required
def boletas():
    rows = q("""SELECT b.*, c.nombre, c.apellido1, c.apellido2, v.patente FROM boletas b
                JOIN clientes c ON c.id=b.cliente_id JOIN vehiculos v ON v.id=b.vehiculo_id ORDER BY b.id DESC""")
    return render_template("boletas.html", boletas=rows)


# ------------------------------------------------------------ caja / config
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


@app.route("/configuracion", methods=["GET", "POST"])
@login_required
def configuracion():
    if request.method == "POST":
        f = request.form
        ex("UPDATE taller SET nombre=?, razon_social=?, rut=?, direccion=?, telefono=?, email=?, iva=? WHERE id=1",
           (f["nombre"], f["razon_social"], f["rut"], f["direccion"], f["telefono"], f["email"],
            float(f.get("iva") or 19)))
        flash("Datos del taller actualizados", "ok")
        return redirect(url_for("configuracion"))
    return render_template("configuracion.html")


@app.route("/pronto/<seccion>")
@login_required
def pronto(seccion):
    if seccion == "almacen":
        return redirect(url_for("almacen"))
    return render_template("pronto.html", seccion=seccion.replace("-", " ").capitalize())


@app.route('/almacen')
@login_required
def almacen():
    s = request.args.get('q', '').strip()
    like = f"%{s}%"
    rows = q("SELECT * FROM almacen WHERE ?='' OR codigo LIKE ? OR nombre LIKE ? OR proveedor LIKE ? ORDER BY id DESC",
             (s, like, like, like))
    return render_template('almacen.html', items=rows, s=s)


@app.route('/almacen/nuevo', methods=['GET', 'POST'])
@login_required
def almacen_nuevo():
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        nombre = request.form.get('nombre', '').strip()
        if not codigo or not nombre:
            flash('Código y nombre son obligatorios', 'error')
            return render_template('almacen_form.html', item=request.form)
        try:
            stock = float(request.form.get('stock') or 0)
            precio = float(request.form.get('precio') or 0)
        except ValueError:
            flash('Stock y precio deben ser numéricos', 'error')
            return render_template('almacen_form.html', item=request.form)
        aid = ex("INSERT INTO almacen(codigo,nombre,descripcion,stock,precio,proveedor,creado) VALUES(?,?,?,?,?,?,?)",
                 (codigo, nombre, request.form.get('descripcion', ''), stock, precio, request.form.get('proveedor', ''), datetime.now().isoformat()))
        flash('Artículo creado', 'ok')
        return redirect(url_for('almacen'))
    return render_template('almacen_form.html', item={})


@app.route('/almacen/<int:aid>/editar', methods=['GET', 'POST'])
@login_required
def almacen_editar(aid):
    item = q('SELECT * FROM almacen WHERE id=?', (aid,), one=True) or abort(404)
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        nombre = request.form.get('nombre', '').strip()
        if not codigo or not nombre:
            flash('Código y nombre son obligatorios', 'error')
            return render_template('almacen_form.html', item=request.form, editar=True)
        try:
            stock = float(request.form.get('stock') or 0)
            precio = float(request.form.get('precio') or 0)
        except ValueError:
            flash('Stock y precio deben ser numéricos', 'error')
            return render_template('almacen_form.html', item=request.form, editar=True)
        ex('UPDATE almacen SET codigo=?,nombre=?,descripcion=?,stock=?,precio=?,proveedor=? WHERE id=?',
           (codigo, nombre, request.form.get('descripcion', ''), stock, precio, request.form.get('proveedor', ''), aid))
        flash('Artículo actualizado', 'ok')
        return redirect(url_for('almacen'))
    return render_template('almacen_form.html', item=item, editar=True)


@app.route('/almacen/<int:aid>/borrar', methods=['POST'])
@login_required
def almacen_borrar(aid):
    if not q('SELECT 1 FROM almacen WHERE id=?', (aid,), one=True):
        abort(404)
    ex('DELETE FROM almacen WHERE id=?', (aid,))
    flash('Artículo eliminado', 'ok')
    return redirect(url_for('almacen'))


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
