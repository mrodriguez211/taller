# TallerPro — Sistema de gestión de taller mecánico (Python + Flask)

Réplica funcional del flujo de un software de taller: login → cuadro de mando →
clientes → vehículos (con búsqueda por patente) → presupuesto → orden de
reparación → boleta → impresión / PDF.

## Cómo ejecutarlo en Visual Studio Code

1. Instala **Python 3.10+** (python.org; en Windows marca "Add Python to PATH").
2. Instala **VS Code** y la extensión **Python** (de Microsoft).
3. Descomprime `taller.zip` y en VS Code: **File → Open Folder…** → elige la carpeta `taller`.
4. Abre la terminal: **Terminal → New Terminal** y ejecuta:

   **Mac / Linux**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python app.py
   ```

   **Windows (PowerShell)**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python app.py
   ```
   Si PowerShell bloquea el script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

5. Abre en el navegador **http://127.0.0.1:5000**
   - Usuario: `admin` · Contraseña: `admin123` · Código taller: `1001`

La base de datos `taller.db` (SQLite) se crea sola la primera vez.
Para empezar de cero, borra `taller.db` y vuelve a ejecutar.

## Flujo para probar

1. Gestión → **Nuevo Cliente** → completa y pulsa *Añadir*.
2. En la ficha → pestaña **Vehículos del Cliente** → *Añadir Vehículo*.
3. Escribe la patente y pulsa el botón ⟳ (o Enter). Patentes de prueba:
   `ABCD12`, `XYZW34`, `KLMN56`. Rellena marca, modelo, chasis, km y revisión técnica.
4. Guarda → en la fila del vehículo, menú **•••** → *Crear Presupuesto*.
5. Agrega líneas (piezas, mano de obra) → **Aceptar → Crear Orden**.
6. En la orden → **Emitir Boleta** → *Imprimir / PDF* → *Gestionar Cobros*.

## ¿De dónde salen los datos al ingresar la patente?

- **Marca, modelo, año, chasis, n° motor, color**: Registro de Vehículos Motorizados
  del Registro Civil, consultado a través de APIs de terceros (Boostr/Autodata,
  Verifik, GetAPI, etc.). Los softwares de taller no consultan al Registro Civil directo.
- **Vencimiento de revisión técnica y último kilometraje**: lo registran las Plantas
  de Revisión Técnica (PRT) y se informa al Ministerio de Transportes; empresas como
  Autofact obtienen el kilometraje desde esa fuente.

En este proyecto, `patente_provider.py` deja el proveedor intercambiable:

| Variable | Efecto |
|---|---|
| `PATENTE_PROVIDER=mock` (defecto) | Datos de prueba, sin internet |
| `PATENTE_PROVIDER=verifik` + `VERIFIK_TOKEN=...` | API real de Verifik (pagada) |

Si una patente no se encuentra, igual se estima el vencimiento de la revisión
técnica según el calendario MTT por último dígito.

## Estructura

```
taller/
├── app.py               # rutas Flask + base de datos SQLite
├── patente_provider.py  # búsqueda de datos por patente
├── documentos_pdf.py    # PDF de boletas, presupuestos y órdenes
├── requirements.txt
├── static/style.css
└── templates/           # páginas HTML (Jinja2)
```

## Módulos

| Menú | Qué hace |
|---|---|
| Gestión → Proveedores / Empleados | Fichas de proveedores y trabajadores (cada empleado tiene un PIN). |
| Gestión → Entrada / Salida | Reloj control: el trabajador elige su nombre y marca con su PIN. Solo los **administradores** pueden corregir, agregar o borrar marcas (con motivo). |
| Gestión → Citas previas / Código QR | Agenda de citas. El QR lleva a `/agendar`, una página pública donde el cliente pide hora sin iniciar sesión. |
| Taller → Ingreso de vehículo | Al abrir una orden se registra el estado del vehículo: km, combustible, zonas con daños, accesorios y fotos. |
| Orden → Trabajos realizados | Se carga lo que se hizo (mecánico, horas) y opcionalmente se suma como mano de obra. |
| Presupuesto / Orden / Boleta | Líneas divididas en **Piezas, Mano de obra y Otros**; todo se imprime en **PDF** y se envía al cliente por correo o WhatsApp. |
| Almacén | Gestión de repuestos (con stock mínimo), facturas de compra y recepción de repuestos (suma stock y deja registro). |
| Configuración → General | Datos del taller, datos económicos (IVA, tarifa hora, banco), logo, nombre de la aplicación y colores. |
| Configuración → Usuarios, Alertas, Papelera | Roles (administrador/usuario), qué avisos muestra la campana 🔔 y restaurar lo eliminado. |
| Configuración → Soporte y capacitación | Pedir una cita con la administración de la página. |

La base de datos se actualiza sola al arrancar (agrega tablas y columnas nuevas sin perder datos).

### Variables de entorno opcionales

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`: envío directo de PDF por correo.
  Sin ellas, el botón «Abrir en mi correo» abre el programa de correo con el enlace al PDF.
- `SOPORTE_EMAIL`, `SOPORTE_WHATSAPP`: a dónde llegan las solicitudes de soporte/capacitación.

Ver `.env.example`.

## Deploy profesional (Render / Railway)

Para producción, la recomendación profesional es usar:

- PostgreSQL (no SQLite)
- Render o Railway
- variables de entorno para `DATABASE_URL` y `SECRET_KEY`

### Archivos lista para producción

- `render.yaml`
- `runtime.txt`
- `.env.example`
- `requirements.txt`

### Variables recomendadas

```env
SECRET_KEY=tu_clave_secreta
DATABASE_URL=postgresql://usuario:password@host:5432/tallerpro
FLASK_ENV=production
```

### Desplegar en Render

1. Subir el repositorio a GitHub.
2. Crear un nuevo servicio Web en Render.
3. Conectar el repo.
4. Añadir las variables de entorno.
5. Deploy.

El servicio usa:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT
```

> Importante: SQLite es útil para desarrollo local, pero no es la mejor opción para producción. Para un sistema de gestión real, PostgreSQL es la opción recomendada.
