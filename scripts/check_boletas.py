import sqlite3, os
DB=os.path.join(os.path.dirname(__file__), '..', 'taller.db')
DB=os.path.normpath(DB)
conn=sqlite3.connect(DB)
cur=conn.cursor()
for row in cur.execute('SELECT id,numero,orden_id,cliente_id,cobrado FROM boletas ORDER BY id DESC LIMIT 10'):
    print(row)
conn.close()
