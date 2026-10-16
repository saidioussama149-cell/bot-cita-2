import time
import logging
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus/"

# ---------------- CONFIGURACIÓN ----------------

TOKEN = "8975628897:AAEsWs_b3hmGCXZzqh513pV7-t-DabJg9zQ"
CHAT_ID = "6008287114"

NIE = "Y9054113Q"
NOMBRE = "OUSSAMA SAIDI"
NACIONALIDAD = "MARRUECOS"
PROVINCIA = "Toledo"

INTERVALO = 360  # 6 minutos
MAX_INTENTOS = 2
ESPERA_REINTENTO = 20

SIN_CITAS_TEXTOS = (
    "en este momento no hay citas disponibles",
    "en este momento no hay citas",
    "no hay citas disponibles",
)

BLOQUEO_TEXTOS = (
    "the requested url was rejected",
    "request rejected",
    "too many requests",
)

CADUCADO_TEXTOS = (
    "su sesión ha caducado",
    "sesión ha caducado",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


# ---------------- TELEGRAM ----------------

def enviar_telegram(mensaje):
    try:
        respuesta = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": mensaje,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        respuesta.raise_for_status()
        return respuesta.json().get("ok") is True

    except requests.RequestException as error:
        logging.error("Telegram falló: %s", error)
        return False


# ---------------- UTILIDADES ----------------

def log_paso(numero, texto):
    logging.info("PASO %s | %s", numero, texto)


def leer_texto(page):
    try:
        texto = page.locator("body").inner_text(timeout=20000)
        return " ".join(texto.split()).casefold()
    except Exception:
        return ""


def comprobar_estado(page):
    texto = leer_texto(page)

    if not texto:
        return "desconocido"

    if "429" in texto or any(x in texto for x in BLOQUEO_TEXTOS):
        logging.warning("Bloqueo/429 detectado.")
        return "bloqueo"

    if any(x in texto for x in CADUCADO_TEXTOS):
        logging.warning("Sesión caducada.")
        return "caducado"

    return "ok"


def pequeña_espera(page, ms=1500):
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass


def aceptar(page, descripcion="Aceptar"):
    selectores = [
        "button:has-text('Aceptar')",
        "input[value='Aceptar']",
        "input#btnAceptar",
        "input#btnEnviar",
    ]

    for selector in selectores:
        try:
            boton = page.locator(selector).first
            boton.wait_for(state="visible", timeout=6000)
            boton.scroll_into_view_if_needed()
            boton.click(timeout=15000, no_wait_after=True)
            pequeña_espera(page, 1800)

            logging.info("%s pulsado con selector: %s", descripcion, selector)
            return True

        except Exception:
            continue

    return False


# ---------------- RECORRIDO ----------------

def abrir_sede(page):
    log_paso(1, "Abriendo sede")

    ultimo_error = None

    for intento in range(1, 4):
        try:
            logging.info("Navegación intento %d/3", intento)

            page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=120000,
            )

            page.wait_for_selector("body", timeout=30000)

            if leer_texto(page):
                logging.info("Sede cargada correctamente.")
                return

        except Exception as error:
            ultimo_error = error
            logging.warning(
                "Falló la carga inicial intento %d/3: %s",
                intento,
                error,
            )

        if intento < 3:
            page.wait_for_timeout(5000)

    raise RuntimeError(
        f"No se pudo abrir la sede: {ultimo_error}"
    )


def pulsar_acceso_y_cambiar_pestana(contexto, page_inicial):
    log_paso(
        2,
        "Pulsando 'Acceder al Procedimiento' y esperando nueva pestaña"
    )

    boton = page_inicial.get_by_text(
        "Acceder al Procedimiento",
        exact=False,
    ).first

    boton.wait_for(state="visible", timeout=45000)
    boton.scroll_into_view_if_needed()

    paginas_antes = set(contexto.pages)

    nueva_page = None

    try:
        # La sede abre el procedimiento en una pestaña nueva.
        with contexto.expect_page(timeout=30000) as popup_info:
            boton.click(timeout=15000, no_wait_after=True)

        nueva_page = popup_info.value

        logging.info(
            "Nueva pestaña detectada: %s",
            nueva_page.url,
        )

    except PlaywrightTimeoutError:
        logging.warning(
            "expect_page no detectó popup. Buscando cualquier pestaña nueva..."
        )

        # Fallback por si el evento popup no se captura bien en cloud.
        limite = time.time() + 20

        while time.time() < limite:
            actuales = contexto.pages

            nuevas = [p for p in actuales if p not in paginas_antes]

            if nuevas:
                nueva_page = nuevas[-1]
                break

            time.sleep(0.5)

    if nueva_page is None:
        raise RuntimeError(
            "PASO 2 falló: no apareció la nueva pestaña del procedimiento."
        )

    nueva_page.set_default_timeout(30000)
    nueva_page.set_default_navigation_timeout(120000)

    try:
        nueva_page.wait_for_load_state(
            "domcontentloaded",
            timeout=60000,
        )
    except PlaywrightTimeoutError:
        logging.warning(
            "La nueva pestaña no terminó domcontentloaded, continuando."
        )

    nueva_page.wait_for_selector("body", timeout=30000)

    logging.info(
        "Trabajando ahora en la nueva pestaña: %s",
        nueva_page.url,
    )

    return nueva_page


def seleccionar_provincia(page):
    log_paso(3, "Seleccionando provincia: Toledo")

    # Primero probamos los selectores conocidos.
    provincia = page.locator(
        "select#form, select[name='form']"
    ).first

    encontrada = False

    try:
        provincia.wait_for(state="visible", timeout=20000)
        encontrada = True
    except Exception:
        pass

    # Fallback: buscar un select que contenga TOLEDO entre sus opciones.
    if not encontrada:
        logging.info(
            "No apareció select#form. Buscando desplegable por opciones..."
        )

        selects = page.locator("select")

        for i in range(selects.count()):
            menu = selects.nth(i)

            try:
                opciones = menu.locator("option")
                textos = []

                for j in range(opciones.count()):
                    textos.append(
                        opciones.nth(j).inner_text().strip().casefold()
                    )

                if any("toledo" in texto for texto in textos):
                    provincia = menu
                    encontrada = True
                    break

            except Exception:
                continue

    if not encontrada:
        logging.warning(
            "Texto visible en la pantalla: %s",
            leer_texto(page)[:1000],
        )
        raise RuntimeError(
            "PASO 3 falló: no se encontró el desplegable de provincias."
        )

    # Intento por label exacto.
    try:
        provincia.select_option(label=PROVINCIA)
    except Exception:
        # Fallback buscando la opción que contenga Toledo.
        opciones = provincia.locator("option")
        seleccionado = False

        for i in range(opciones.count()):
            opcion = opciones.nth(i)
            etiqueta = opcion.inner_text().strip()

            if PROVINCIA.casefold() in etiqueta.casefold():
                valor = opcion.get_attribute("value")

                if valor:
                    provincia.select_option(value=valor)
                    seleccionado = True
                    break

        if not seleccionado:
            raise RuntimeError(
                "PASO 3 falló: apareció el desplegable pero no la opción Toledo."
            )

    logging.info("Provincia seleccionada: %s", PROVINCIA)

    if not aceptar(page, "Aceptar provincia"):
        raise RuntimeError(
            "PASO 3 falló: no se pudo pulsar Aceptar tras elegir Toledo."
        )


def seleccionar_tramite(page):
    log_paso(4, "Buscando trámite de toma de huellas")

    pequeña_espera(page, 1500)

    objetivo_1 = "toma de huellas"
    objetivo_2 = "expedición de tarjeta"

    selects = page.locator("select")
    cantidad = selects.count()

    logging.info("Desplegables encontrados: %d", cantidad)

    for i in range(cantidad):
        menu = selects.nth(i)

        try:
            opciones = menu.locator("option")

            for j in range(opciones.count()):
                opcion = opciones.nth(j)
                etiqueta = " ".join(
                    opcion.inner_text().split()
                )
                normalizada = etiqueta.casefold()

                if (
                    objetivo_1 in normalizada
                    and objetivo_2 in normalizada
                ):
                    valor = opcion.get_attribute("value")

                    if valor:
                        menu.select_option(value=valor)

                        logging.info(
                            "Trámite seleccionado: %s",
                            etiqueta,
                        )
                        return True

        except Exception:
            continue

    return False


def pasar_avisos(page):
    log_paso(5, "Superando pantalla de avisos")

    selectores = [
        "input#btnEntrar",
        "input[value='Entrar']",
        "button#btnEntrar",
        "button:has-text('Entrar')",
        "text=Presentación sin Cl@ve",
    ]

    for selector in selectores:
        try:
            boton = page.locator(selector).first
            boton.wait_for(state="visible", timeout=8000)
            boton.scroll_into_view_if_needed()
            boton.click(timeout=15000, no_wait_after=True)
            pequeña_espera(page, 2000)

            logging.info(
                "Pantalla de avisos superada con selector: %s",
                selector,
            )
            return True

        except Exception:
            continue

    return False


def rellenar_formulario_datos(page):
    log_paso(6, "Rellenando datos personales")

    page.wait_for_selector(
        "input[type='text'], input#txtIdCit",
        timeout=45000,
    )

    # NIE
    try:
        radio_nie = page.locator(
            "input[type='radio'][value='NIE'], "
            "input#rdbTipoDocNie"
        ).first

        if radio_nie.is_visible(timeout=3000):
            radio_nie.check()
    except Exception:
        pass

    input_nie = page.locator(
        "input#txtIdCit, "
        "input[name*='IdCit'], "
        "input[name*='Citado']"
    ).first

    try:
        input_nie.wait_for(state="visible", timeout=7000)
    except Exception:
        input_nie = page.locator("input[type='text']").nth(0)
        input_nie.wait_for(state="visible", timeout=10000)

    input_nie.fill(NIE)
    logging.info("NIE introducido.")

    # Nombre
    input_nombre = page.locator(
        "input#txtNombSoli, input[name*='Nomb']"
    ).first

    try:
        input_nombre.wait_for(state="visible", timeout=7000)
    except Exception:
        input_nombre = page.locator("input[type='text']").nth(1)
        input_nombre.wait_for(state="visible", timeout=10000)

    input_nombre.fill(NOMBRE)
    logging.info("Nombre introducido.")

    # Nacionalidad
    try:
        select_pais = page.locator(
            "select#txtPaisNac, select[name*='Pais']"
        ).first

        if select_pais.is_visible(timeout=5000):
            try:
                select_pais.select_option(label=NACIONALIDAD)

            except Exception:
                opciones = select_pais.locator("option")

                for i in range(opciones.count()):
                    opcion = opciones.nth(i)
                    etiqueta = opcion.inner_text().strip()

                    if (
                        NACIONALIDAD.casefold()
                        in etiqueta.casefold()
                    ):
                        valor = opcion.get_attribute("value")

                        if valor:
                            select_pais.select_option(
                                value=valor
                            )
                            break

    except Exception as error:
        logging.warning(
            "No se pudo seleccionar nacionalidad: %s",
            error,
        )

    if not aceptar(page, "Aceptar datos personales"):
        raise RuntimeError(
            "PASO 6 falló: no se pudo avanzar tras rellenar los datos."
        )


def pulsar_solicitar_cita(page):
    log_paso(7, "Pulsando 'Solicitar Cita'")

    selectores = [
        "input[value='Solicitar Cita']",
        "button:has-text('Solicitar Cita')",
        "input#btnEnviar",
    ]

    for selector in selectores:
        try:
            boton = page.locator(selector).first
            boton.wait_for(state="visible", timeout=10000)
            boton.scroll_into_view_if_needed()
            boton.click(timeout=15000, no_wait_after=True)

            pequeña_espera(page, 3500)

            logging.info(
                "'Solicitar Cita' pulsado con selector: %s",
                selector,
            )
            return True

        except Exception:
            continue

    return False


def evaluar_resultado_final(page):
    log_paso(8, "Leyendo resultado final")

    texto = leer_texto(page)
    url_actual = page.url

    if not texto:
        logging.warning("Pantalla final sin texto.")
        return "desconocido"

    if "429" in texto or any(
        x in texto for x in BLOQUEO_TEXTOS
    ):
        logging.warning("Resultado final: bloqueo/429.")
        return "bloqueo"

    if any(x in texto for x in CADUCADO_TEXTOS):
        logging.warning("Resultado final: sesión caducada.")
        return "caducado"

    if any(x in texto for x in SIN_CITAS_TEXTOS):
        logging.info("RESULTADO FINAL: NO HAY CITAS.")

        enviar_telegram(
            f"❌ *No hay citas disponibles*\n\n"
            f"📍 *Provincia:* {PROVINCIA}\n"
            f"👤 *Solicitante:* {NOMBRE}\n"
            f"🆔 *NIE:* {NIE}\n\n"
            "✅ El recorrido llegó correctamente al resultado final.\n"
            f"🔗 [Abrir sede]({url_actual})"
        )

        return "sin_citas"

    if "cl@ve" in texto and "disposición" in texto:
        logging.info(
            "RESULTADO FINAL: no hay citas ordinarias."
        )

        enviar_telegram(
            f"❌ *No hay citas ordinarias en {PROVINCIA}*\n\n"
            "La sede muestra disponibilidad únicamente mediante Cl@ve."
        )

        return "sin_citas"

    indicios_cita = (
        "seleccione día",
        "seleccione una cita",
        "seleccionar cita",
        "seleccione fecha",
        "seleccione hora",
        "citas disponibles",
        "elegir cita",
    )

    if any(indicio in texto for indicio in indicios_cita):
        logging.warning("RESULTADO FINAL: HAY CITAS.")

        enviar_telegram(
            f"🔥 *¡HAY CITAS DISPONIBLES EN {PROVINCIA.upper()}!* 🔥\n\n"
            f"👤 *Solicitante:* {NOMBRE}\n"
            f"🆔 *NIE:* {NIE}\n\n"
            "✅ La sede ha llegado a una pantalla de disponibilidad.\n"
            f"🔗 [ENTRAR A LA SEDE]({url_actual})"
        )

        return "citas_encontradas"

    logging.warning("RESULTADO FINAL: pantalla desconocida.")
    logging.warning("URL final: %s", url_actual)
    logging.warning(
        "Texto final: %s",
        texto[:1000],
    )

    return "desconocido"


# ---------------- CICLO COMPLETO ----------------

def ejecutar_ciclo(contexto):
    page_inicial = contexto.new_page()
    page_procedimiento = None

    try:
        abrir_sede(page_inicial)

        estado = comprobar_estado(page_inicial)
        if estado != "ok":
            return estado

        page_procedimiento = pulsar_acceso_y_cambiar_pestana(
            contexto,
            page_inicial,
        )

        estado = comprobar_estado(page_procedimiento)
        if estado not in ("ok", "desconocido"):
            return estado

        seleccionar_provincia(page_procedimiento)

        estado = comprobar_estado(page_procedimiento)
        if estado not in ("ok", "desconocido"):
            return estado

        if not seleccionar_tramite(page_procedimiento):
            raise RuntimeError(
                "PASO 4 falló: no se encontró el trámite de toma de huellas."
            )

        if not aceptar(
            page_procedimiento,
            "Aceptar trámite",
        ):
            raise RuntimeError(
                "PASO 4 falló: no se pudo aceptar el trámite."
            )

        estado = comprobar_estado(page_procedimiento)
        if estado not in ("ok", "desconocido"):
            return estado

        if not pasar_avisos(page_procedimiento):
            raise RuntimeError(
                "PASO 5 falló: no se pudo superar la pantalla de avisos."
            )

        estado = comprobar_estado(page_procedimiento)
        if estado not in ("ok", "desconocido"):
            return estado

        rellenar_formulario_datos(page_procedimiento)

        estado = comprobar_estado(page_procedimiento)
        if estado not in ("ok", "desconocido"):
            return estado

        if not pulsar_solicitar_cita(page_procedimiento):
            raise RuntimeError(
                "PASO 7 falló: no se encontró 'Solicitar Cita'."
            )

        return evaluar_resultado_final(page_procedimiento)

    finally:
        # Cerrar todas las páginas del contexto de este intento.
        for p in list(contexto.pages):
            try:
                p.close()
            except Exception:
                pass


def crear_contexto(navegador):
    return navegador.new_context(
        locale="es-ES",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={
            "width": 1365,
            "height": 900,
        },
    )


def ejecutar_intento(navegador, numero_intento):
    contexto = crear_contexto(navegador)

    try:
        logging.info(
            "===== COMPROBACIÓN | INTENTO %d/%d =====",
            numero_intento,
            MAX_INTENTOS,
        )

        return ejecutar_ciclo(contexto)

    finally:
        try:
            contexto.close()
        except Exception:
            pass

        logging.info("Contexto cerrado.")


# ---------------- MAIN ----------------

def main():
    enviar_telegram(
        f"☁️ *Bot de citas iniciado en la nube*\n\n"
        f"📍 Provincia: {PROVINCIA}\n"
        "⏱ Comprobación cada 6 minutos."
    )

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        try:
            while True:
                resultado = "desconocido"

                for intento in range(
                    1,
                    MAX_INTENTOS + 1,
                ):
                    try:
                        resultado = ejecutar_intento(
                            navegador,
                            intento,
                        )

                        if resultado in (
                            "sin_citas",
                            "citas_encontradas",
                            "bloqueo",
                            "caducado",
                        ):
                            break

                    except PlaywrightTimeoutError as error:
                        logging.warning(
                            "TIMEOUT intento %d/%d: %s",
                            intento,
                            MAX_INTENTOS,
                            error,
                        )

                        resultado = "desconocido"

                    except Exception as error:
                        logging.exception(
                            "ERROR intento %d/%d: %s",
                            intento,
                            MAX_INTENTOS,
                            error,
                        )

                        resultado = "desconocido"

                    if intento < MAX_INTENTOS:
                        logging.info(
                            "Reintentando en %d segundos...",
                            ESPERA_REINTENTO,
                        )
                        time.sleep(ESPERA_REINTENTO)

                logging.info(
                    "RESULTADO DEL CICLO: %s",
                    resultado,
                )

                logging.info(
                    "Próxima comprobación en 6 minutos."
                )

                time.sleep(INTERVALO)

        except KeyboardInterrupt:
            logging.info("Bot detenido manualmente.")

        finally:
            navegador.close()


if __name__ == "__main__":
    main()
