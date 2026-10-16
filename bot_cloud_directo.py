import time
import logging
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus/"

# Datos del bot
TOKEN = "8975628897:AAEsWs_b3hmGCXZzqh513pV7-t-DabJg9zQ"
CHAT_ID = "6008287114"

NIE = "Y9054113Q"
NOMBRE = "OUSSAMA SAIDI"
NACIONALIDAD = "MARRUECOS"
PROVINCIA = "Toledo"

INTERVALO = 360          # 6 minutos entre ciclos
ESPERA_REINTENTO = 20    # reintento corto si falla por timeout
MAX_INTENTOS = 2         # máximo 2 intentos por ciclo

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


def leer_texto(page):
    try:
        return " ".join(
            page.locator("body").inner_text(timeout=20000).split()
        ).casefold()
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


def esperar_cambio(page, espera_ms=1200):
    # La sede no siempre hace una navegación completa.
    # Esta pequeña espera evita depender de wait_for_load_state().
    page.wait_for_timeout(espera_ms)


def log_paso(numero, texto):
    logging.info("PASO %s | %s", numero, texto)


def abrir_sede(page):
    log_paso(1, "Abriendo sede")

    # Reintento de navegación porque en cloud la primera conexión
    # puede tardar más o fallar temporalmente.
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

            texto = leer_texto(page)
            if texto:
                logging.info("Sede cargada correctamente.")
                return

        except Exception as error:
            ultimo_error = error
            logging.warning(
                "Falló la carga inicial (intento %d/3): %s",
                intento,
                error,
            )

        if intento < 3:
            page.wait_for_timeout(5000)

    raise RuntimeError(f"No se pudo abrir la sede: {ultimo_error}")


def pulsar_acceso(page):
    log_paso(2, "Buscando 'Acceder al Procedimiento'")

    boton = page.get_by_text(
        "Acceder al Procedimiento",
        exact=False,
    ).first

    boton.wait_for(state="visible", timeout=45000)
    boton.scroll_into_view_if_needed()

    # La sede deja a veces la navegación pendiente indefinidamente.
    # El clic sí se ejecuta, por eso no esperamos a que Playwright dé
    # la navegación por terminada.
    boton.click(timeout=15000, no_wait_after=True)

    logging.info(
        "Clic realizado. Dejando cargar la siguiente pantalla..."
    )

    # Dejamos que llegue el HTML principal y después detenemos recursos
    # que puedan mantener la navegación abierta (analytics, imágenes, etc.).
    page.wait_for_timeout(5000)

    try:
        page.evaluate("window.stop()")
        logging.info("Carga pendiente detenida para continuar con el DOM.")
    except Exception as error:
        logging.info("No fue necesario detener la carga: %s", error)

    logging.info("URL después del acceso: %s", page.url)

    # Buscar el selector de provincia sin depender del estado de navegación.
    limite = time.time() + 45
    encontrado = False

    while time.time() < limite:
        try:
            existe = page.evaluate(
                """() => Boolean(document.querySelector("select#form, select[name='form']"))"""
            )
            if existe:
                encontrado = True
                break
        except Exception:
            pass

        page.wait_for_timeout(1000)

    if not encontrado:
        try:
            texto = page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 1000) : ''"
            )
        except Exception:
            texto = ""

        logging.warning("No apareció el selector de provincia.")
        logging.warning("Texto visible tras el acceso: %s", texto)
        raise RuntimeError(
            "PASO 2 falló: no apareció el selector de provincia tras el acceso."
        )

    logging.info("Selector de provincia encontrado. Continuando a Toledo.")

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
            boton.wait_for(state="visible", timeout=5000)
            boton.scroll_into_view_if_needed()
            boton.click(timeout=15000, no_wait_after=True)
            esperar_cambio(page, 1800)

            logging.info("%s pulsado con selector %s", descripcion, selector)
            return True

        except Exception:
            continue

    return False


def seleccionar_provincia(page):
    log_paso(3, "Seleccionando provincia: Toledo")

    provincia = page.locator(
        "select#form, select[name='form']"
    ).first

    provincia.wait_for(state="attached", timeout=30000)

    try:
        provincia.select_option(label=PROVINCIA, timeout=15000)
    except Exception:
        # Fallback por si Playwright vuelve a quedar condicionado por una
        # navegación pendiente: seleccionamos Toledo directamente en el DOM.
        seleccionado = page.evaluate(
            """(provincia) => {
                const s = document.querySelector("select#form, select[name='form']");
                if (!s) return false;
                const opt = Array.from(s.options).find(
                    o => o.text.trim().toLowerCase() === provincia.toLowerCase()
                );
                if (!opt) return false;
                s.value = opt.value;
                s.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""",
            PROVINCIA,
        )
        if not seleccionado:
            raise RuntimeError("No se encontró Toledo en el desplegable de provincia.")

    logging.info("Provincia seleccionada: %s", PROVINCIA)

    if not aceptar(page, "Aceptar provincia"):
        raise RuntimeError(
            "No se encontró el botón Aceptar después de seleccionar Toledo."
        )

def seleccionar_tramite(page):
    log_paso(4, "Buscando trámite de toma de huellas")

    # Damos tiempo explícito a que aparezcan los desplegables.
    page.locator("select").first.wait_for(
        state="attached",
        timeout=45000,
    )

    objetivo_1 = "toma de huellas"
    objetivo_2 = "expedición de tarjeta"

    menus = page.locator("select")
    cantidad = menus.count()

    logging.info("Desplegables encontrados: %d", cantidad)

    for i in range(cantidad):
        menu = menus.nth(i)

        try:
            opciones = menu.locator("option")

            for j in range(opciones.count()):
                opcion = opciones.nth(j)
                etiqueta = " ".join(opcion.inner_text().split())
                normalizada = etiqueta.casefold()

                if objetivo_1 in normalizada and objetivo_2 in normalizada:
                    valor = opcion.get_attribute("value")

                    if valor:
                        menu.select_option(value=valor)
                        logging.info("Trámite seleccionado: %s", etiqueta)
                        return True

        except Exception as error:
            logging.debug("Menú %d ignorado: %s", i, error)

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
            boton.wait_for(state="visible", timeout=7000)
            boton.scroll_into_view_if_needed()
            boton.click(timeout=15000, no_wait_after=True)
            esperar_cambio(page, 2000)

            logging.info("Avisos superados con selector: %s", selector)
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

    try:
        radio_nie = page.locator(
            "input[type='radio'][value='NIE'], "
            "input#rdbTipoDocNie"
        ).first

        if radio_nie.is_visible(timeout=3000):
            radio_nie.check()
            logging.info("Tipo NIE seleccionado.")
    except Exception:
        pass

    # NIE
    input_nie = page.locator(
        "input#txtIdCit, input[name*='IdCit'], input[name*='Citado']"
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
                logging.info(
                    "Nacionalidad seleccionada directamente: %s",
                    NACIONALIDAD,
                )
            except Exception:
                opciones = select_pais.locator("option")
                encontrada = False

                for i in range(opciones.count()):
                    opcion = opciones.nth(i)
                    etiqueta = opcion.inner_text().strip()

                    if NACIONALIDAD.casefold() in etiqueta.casefold():
                        valor = opcion.get_attribute("value")
                        if valor:
                            select_pais.select_option(value=valor)
                            encontrada = True
                            logging.info(
                                "Nacionalidad seleccionada: %s",
                                etiqueta,
                            )
                            break

                if not encontrada:
                    logging.warning(
                        "No se encontró la nacionalidad en el desplegable."
                    )

    except Exception as error:
        logging.warning(
            "No se pudo seleccionar nacionalidad: %s",
            error,
        )

    if not aceptar(page, "Aceptar datos personales"):
        raise RuntimeError(
            "No se pudo avanzar después de rellenar datos personales."
        )


def pulsar_solicitar_cita(page):
    log_paso(7, "Buscando botón 'Solicitar Cita'")

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

            # Damos tiempo a que aparezca el resultado.
            esperar_cambio(page, 3500)

            logging.info(
                "Botón Solicitar Cita pulsado con selector: %s",
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
        logging.warning("La pantalla final no contiene texto legible.")
        return "desconocido"

    if "429" in texto or any(x in texto for x in BLOQUEO_TEXTOS):
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
            "RESULTADO FINAL: no hay citas ordinarias; solo Cl@ve."
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
        "Texto final (primeros 800 caracteres): %s",
        texto[:800],
    )

    return "desconocido"


def ejecutar_ciclo(page):
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(120000)

    abrir_sede(page)

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    pulsar_acceso(page)

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    seleccionar_provincia(page)

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    if not seleccionar_tramite(page):
        raise RuntimeError(
            "PASO 4 falló: no se encontró el trámite de toma de huellas."
        )

    if not aceptar(page, "Aceptar trámite"):
        raise RuntimeError(
            "PASO 4 falló: no se pudo aceptar el trámite."
        )

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    if not pasar_avisos(page):
        raise RuntimeError(
            "PASO 5 falló: no se pudo superar la pantalla de avisos."
        )

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    rellenar_formulario_datos(page)

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    if not pulsar_solicitar_cita(page):
        raise RuntimeError(
            "PASO 7 falló: no se encontró 'Solicitar Cita'."
        )

    return evaluar_resultado_final(page)


def crear_contexto(navegador):
    return navegador.new_context(
        locale="es-ES",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1365, "height": 900},
    )


def ejecutar_intento(navegador, numero_intento):
    # Contexto NUEVO en cada intento:
    # evita arrastrar una sesión rota o caducada.
    contexto = crear_contexto(navegador)
    page = contexto.new_page()

    try:
        logging.info(
            "===== COMPROBACIÓN | INTENTO %d/%d =====",
            numero_intento,
            MAX_INTENTOS,
        )

        return ejecutar_ciclo(page)

    finally:
        try:
            page.close()
        except Exception:
            pass

        try:
            contexto.close()
        except Exception:
            pass

        logging.info("Pestaña y contexto cerrados.")


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

                for intento in range(1, MAX_INTENTOS + 1):
                    try:
                        resultado = ejecutar_intento(
                            navegador,
                            intento,
                        )

                        # Si llegamos a un resultado real, no repetimos.
                        if resultado in (
                            "sin_citas",
                            "citas_encontradas",
                            "bloqueo",
                            "caducado",
                        ):
                            break

                    except PlaywrightTimeoutError as error:
                        logging.warning(
                            "TIMEOUT en intento %d/%d: %s",
                            intento,
                            MAX_INTENTOS,
                            error,
                        )
                        resultado = "desconocido"

                    except Exception as error:
                        logging.exception(
                            "ERROR en intento %d/%d: %s",
                            intento,
                            MAX_INTENTOS,
                            error,
                        )
                        resultado = "desconocido"

                    if intento < MAX_INTENTOS:
                        logging.info(
                            "Reintentando con una sesión nueva en %d segundos...",
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
