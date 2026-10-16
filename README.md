# Bot de citas cloud - configuración directa

El archivo bot_cloud_directo.py ya contiene los datos del bot de Telegram
y del solicitante directamente dentro del código.

## Docker

Construir:

    docker build -t bot-citas .

Ejecutar:

    docker run --restart unless-stopped bot-citas

El bot comprueba cada 6 minutos y usa Chromium en modo headless.
