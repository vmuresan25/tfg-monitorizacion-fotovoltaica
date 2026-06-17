# Sistema de monitorización fotovoltaica

Este repositorio contiene el código desarrollado para un Trabajo Fin de Grado centrado en la monitorización de una instalación fotovoltaica doméstica. El sistema combina captura de imágenes mediante una ESP32-CAM, análisis de visión artificial, comparación entre producción real y producción teórica, un sensor óptico de suciedad y automatización mediante servicios systemd.

## Descripción general

El objetivo del proyecto es detectar posibles pérdidas de rendimiento en una instalación solar fotovoltaica y relacionarlas con factores como suciedad, sombras u objetos sobre los paneles.

El sistema realiza varias tareas principales:

- Captura periódica de imágenes del panel mediante una ESP32-CAM.
- Recorte y procesado del panel mediante visión artificial.
- Detección de posibles objetos, manchas o sombras sobre la superficie del panel.
- Comparación entre la producción real obtenida de FusionSolar y una producción teórica calculada con datos meteorológicos de Open-Meteo.
- Registro horario de un sensor óptico basado en un fotodiodo BPW34 y un láser.
- Envío de resultados y avisos mediante un bot de Telegram.
- Ejecución automática en Linux mediante servicios y temporizadores systemd.

## Estructura del repositorio

hardware/
- esp32-cam/
- sensor-optico/

software/
- vision/src/

deployment/
- systemd/

requirements.txt

## Componentes principales

### ESP32-CAM

La ESP32-CAM se utiliza para capturar imágenes periódicas de los paneles solares. El código correspondiente se encuentra en hardware/esp32-cam/CameraWebServer/.

### Sensor óptico

El sensor óptico utiliza un fotodiodo BPW34 y un láser para obtener una medida indirecta de la suciedad acumulada sobre un cristal testigo. El código del microcontrolador se encuentra en hardware/sensor-optico/.

### Software de monitorización

Los scripts Python se encuentran en software/vision/src/. Estos scripts se encargan de la captura, procesado de imágenes, comparación energética, registro de datos, resumen diario y comunicación con Telegram.

### Despliegue en Linux

La carpeta deployment/systemd/ contiene plantillas de los servicios y temporizadores utilizados para automatizar la ejecución del sistema en Linux.

Los archivos incluidos son plantillas limpias y no contienen credenciales reales.

## Dependencias

Las dependencias principales del proyecto están recogidas en el archivo requirements.txt.

Para instalarlas en un entorno virtual:

pip install -r requirements.txt

## Archivos no incluidos

Por seguridad y limpieza del repositorio, no se incluyen archivos generados durante la ejecución, como imágenes capturadas, resultados de depuración, logs, resúmenes diarios, cachés JSON, cookies de FusionSolar o credenciales personales.

Estos archivos están excluidos mediante .gitignore.

## Credenciales y configuración

El proyecto utiliza variables de entorno para credenciales sensibles, como:

- FUSIONSOLAR_USERNAME
- FUSIONSOLAR_PASSWORD
- BOT_TOKEN
- TELEGRAM_CHAT_ID

Por seguridad, estas credenciales no se incluyen en el repositorio.
