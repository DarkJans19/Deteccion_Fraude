# Lector y analizador de codigos QR

Aplicacion web local que lee un QR con la camara o desde una imagen, extrae su
URL y usa `backend.url_analysis` para evaluarla. No navega automaticamente al enlace.
Tambien sigue hasta cinco redirecciones HTTP para mostrar el destino final y
analiza nuevamente la URL si termina en otro lugar.
Ademas, compara el host final con los dominios reportados a la Superfinanciera
y alerta cuando el nombre parece una variante tipografica de un dominio oficial.

## Ejecutar

Desde la raiz del proyecto:

```powershell
.venv\Scripts\python.exe -m backend.payment_receipts.server
```

Se abrira `http://127.0.0.1:8000`. La camara funciona en `localhost`. La
aplicacion usa `BarcodeDetector` cuando el navegador lo ofrece y cambia
automaticamente a la copia local de `jsQR` cuando Chrome o Edge no incluyen esa
API. La imagen y los fotogramas de la camara se decodifican en el navegador y no
se suben al servidor.

`static/vendor/jsQR.js` corresponde a jsQR 1.4.0 (Apache-2.0); su licencia se
incluye junto al archivo como `static/vendor/jsQR.LICENSE`.

Para usar Google Safe Browsing, define primero
`GOOGLE_SAFE_BROWSING_API_KEY`. Sin la clave, el modelo local continua
funcionando y la interfaz muestra que la comprobacion remota no esta
configurada.

Opciones utiles:

```powershell
# No abrir el navegador automaticamente
.venv\Scripts\python.exe -m backend.payment_receipts.server --no-browser

# No conectarse a las URLs para resolver redirecciones
.venv\Scripts\python.exe -m backend.payment_receipts.server --no-redirects
```

La resolucion de redirecciones rechaza credenciales, puertos distintos de 80 y
443, y destinos locales, privados o no enrutables. Solo consulta cabeceras HTTP
(o un rango de un byte si el servidor no admite `HEAD`); no ejecuta JavaScript,
por lo que no detecta redirecciones realizadas exclusivamente por scripts.

