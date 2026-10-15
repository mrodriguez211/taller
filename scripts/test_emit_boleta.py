import sqlite3, datetime, requests, os
DB=os.path.join(os.path.dirname(__file__), '..', 'taller.db')
DB=os.path.normpath(DB)
conn=sqlite3.connect(DB)
cur=conn.cursor()
now=datetime.datetime.now().isoformat()
# Create cliente
cur.execute("INSERT INTO clientes(numero,rut,nombre,apellido1,telefono,comuna,ciudad,pais,creado) VALUES(?,?,?,?,?,?,?,?,?)",
            (999,'99.999.999-9','Test','Cliente','+56900000000','Santiago','Santiago','Chile',now))
cliente_id=cur.lastrowid
# Create vehiculo
cur.execute("INSERT INTO vehiculos(cliente_id,patente,marca,modelo,km,creado) VALUES(?,?,?,?,?,?)",
            (cliente_id,'ZZZ999','TEST','MODEL',12345,now))
veh_id=cur.lastrowid
# Create orden (documento tipo 'orden')
numero='OTTEST'
fecha=now
cur.execute("INSERT INTO documentos(tipo,numero,cliente_id,vehiculo_id,fecha,km,estado) VALUES(?,?,?,?,?,?,?)",
            ('orden',numero,cliente_id,veh_id,fecha,12345,'ABIERTA'))
orden_id=cur.lastrowid
# Add a line to order
cur.execute("INSERT INTO lineas(doc_tipo,doc_id,tipo,concepto,cantidad,precio,descuento) VALUES(?,?,?,?,?,?,?)",
            ('orden',orden_id,'Repuesto','Filtro aceite',1,15000,0))
conn.commit()
print('Creado cliente_id=',cliente_id,'veh_id=',veh_id,'orden_id=',orden_id)
# Now call the endpoint to emit boleta
s=requests.Session()
login_data={'usuario':'admin','password':'admin123','codigo':'1001'}
res=s.post('http://127.0.0.1:5000/login', data=login_data, allow_redirects=False)
print('Login status',res.status_code)
if res.status_code in (302,303,200):
    r=s.post(f'http://127.0.0.1:5000/orden/{orden_id}/boleta', allow_redirects=False)
    print('Emitir boleta status', r.status_code)
    cur.execute('SELECT id,numero FROM boletas WHERE orden_id=?', (orden_id,))
    b=cur.fetchone()
    print('Boleta en DB:', b)
else:
    print('Login falló')
conn.close()
