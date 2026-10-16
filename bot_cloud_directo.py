import time
import logging
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus/"

# Datos integrados directamente en el archivo
TOKEN = "8975628897:AAEsWs_b3hmGCXZzqh513pV7-t-DabJg9zQ"
CHAT_ID = "6008287114"

NIE = "Y9054113Q"
NOMBRE = "OUSSAMA SAIDI"
NACIONALIDAD = "MARRUECOS"
PROVINCIA = "Toledo"

INTERVALO = 360  # 6 minutos

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
            timeout=15,
        )
        respuesta.raise_for_status()
        return respuesta.json().get("ok") is True
    except requests.RequestException as error:
        logging.error("Error de Telegram: %s", error)
        return False


def leer_texto(page):
    try:
        texto = page.locator("body").inner_text(timeout=10000)
        return " ".join(texto.split()).casefold()
    except Exception:
        return ""


def comprobar_estado(page):
    texto = leer_texto(page)

    if not texto:
        return "desconocido"

    if "429" in texto or any(x in texto for x in BLOQUEO_TEXTOS):
        return "bloqueo"

    if any(x in texto for x in CADUCADO_TEXTOS):
        return "caducado"

    return "ok"


def esperar_carga(page, timeout=15000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


def pulsar_acceso(page):
    boton = page.get_by_text("Acceder al Procedimiento", exact=False).first
    boton.wait_for(state="visible", timeout=20000)
    boton.click()
    esperar_carga(page)


def aceptar(page):
    selectores = [
        "button:has-text('Aceptar')",
        "input[value='Aceptar']",
        "input#btnAceptar",
        "input#btnEnviar",
    ]

    for selector in selectores:
        boton = page.locator(selector).first
        try:
            boton.wait_for(state="visible", timeout=2500)
            boton.scroll_into_view_if_needed()
            boton.click()
            esperar_carga(page, 10000)
            return True
        except Exception:
            continue

    return False


def seleccionar_provincia(page):
    provincia = page.locator("select#form, select[name='form']").first
    provincia.wait_for(state="visible", timeout=15000)
    provincia.select_option(label=PROVINCIA)

    if not aceptar(page):
        raise RuntimeError("No se encontró el botón Aceptar de provincia.")


def seleccionar_tramite(page):
    objetivo_1 = "toma de huellas"
    objetivo_2 = "expedición de tarjeta"

    menus = page.locator("select")

    for i in range(menus.count()):
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
                        return True
        except Exception:
            continue

    return False


def pasar_avisos(page):
    selectores = [
        "input#btnEntrar",
        "input[value='Entrar']",
        "button#btnEntrar",
        "button:has-text('Entrar')",
        "text=Presentación sin Cl@ve",
    ]

    for selector in selectores:
        boton = page.locator(selector).first
        try:
            boton.wait_for(state="visible", timeout=3000)
            boton.click()
            esperar_carga(page, 10000)
            return True
        except Exception:
            continue

    return False


def rellenar_formulario_datos(page):
    page.wait_for_selector(
        "input[type='text'], input#txtIdCit",
        timeout=15000,
    )

    try:
        radio_nie = page.locator(
            "input[type='radio'][value='NIE'], input#rdbTipoDocNie"
        ).first

        if radio_nie.is_visible(timeout=2000):
            radio_nie.check()
    except Exception:
        pass

    input_nie = page.locator(
        "input#txtIdCit, input[name*='IdCit'], input[name*='Citado']"
    ).first

    try:
        input_nie.wait_for(state="visible", timeout=3000)
    except Exception:
        input_nie = page.locator("input[type='text']").nth(0)

    input_nie.fill(NIE)

    input_nombre = page.locator(
        "input#txtNombSoli, input[name*='Nomb']"
    ).first

    try:
        input_nombre.wait_for(state="visible", timeout=3000)
    except Exception:
        input_nombre = page.locator("input[type='text']").nth(1)

    input_nombre.fill(NOMBRE)

    try:
        select_pais = page.locator(
            "select#txtPaisNac, select[name*='Pais']"
        ).first

        if select_pais.is_visible(timeout=2500):
            try:
                select_pais.select_option(label=NACIONALIDAD)
            except Exception:
                opciones = select_pais.locator("option")

                for i in range(opciones.count()):
                    opcion = opciones.nth(i)
                    etiqueta = opcion.inner_text().strip()

                    if NACIONALIDAD.casefold() in etiqueta.casefold():
                        valor = opcion.get_attribute("value")
                        if valor:
                            select_pais.select_option(value=valor)
                            break

    except Exception as error:
        logging.warning("No se pudo seleccionar nacionalidad: %s", error)

    if not aceptar(page):
        raise RuntimeError(
            "No se pudo avanzar después de rellenar los datos personales."
        )


def pulsar_solicitar_cita(page):
    selectores = [
        "input[value='Solicitar Cita']",
        "button:has-text('Solicitar Cita')",
        "input#btnEnviar",
    ]

    for selector in selectores:
        boton = page.locator(selector).first

        try:
            boton.wait_for(state="visible", timeout=5000)
            boton.click()
            esperar_carga(page, 15000)
            page.wait_for_timeout(2000)
            return True
        except Exception:
            continue

    return False


def evaluar_resultado_final(page):
    texto = leer_texto(page)
    url_actual = page.url

    if not texto:
        return "desconocido"

    if "429" in texto or any(x in texto for x in BLOQUEO_TEXTOS):
        return "bloqueo"

    if any(x in texto for x in CADUCADO_TEXTOS):
        return "caducado"

    if any(x in texto for x in SIN_CITAS_TEXTOS):
        enviar_telegram(
            f"❌ *No hay citas disponibles*\n\n"
            f"📍 *Provincia:* {PROVINCIA}\n"
            f"👤 *Solicitante:* {NOMBRE}\n"
            f"🆔 *NIE:* {NIE}\n\n"
            "✅ Comprobación completada correctamente.\n"
            f"🔗 [Abrir sede]({url_actual})"
        )
        return "sin_citas"

    if "cl@ve" in texto and "disposición" in texto:
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
        enviar_telegram(
            f"🔥 *¡HAY CITAS DISPONIBLES EN {PROVINCIA.upper()}!* 🔥\n\n"
            f"👤 *Solicitante:* {NOMBRE}\n"
            f"🆔 *NIE:* {NIE}\n\n"
            "✅ La sede ha llegado a una pantalla de disponibilidad.\n"
            f"🔗 [ENTRAR A LA SEDE]({url_actual})"
        )
        return "citas_encontradas"

    logging.warning("Pantalla final desconocida.")
    logging.warning("URL final: %s", url_actual)
    logging.warning("Texto final: %s", texto[:500])
    return "desconocido"


def ejecutar_ciclo(page):
    logging.info("===== NUEVA COMPROBACIÓN =====")

    page.goto(URL, wait_until="domcontentloaded", timeout=60000)

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
        raise RuntimeError("No se encontró el trámite.")

    if not aceptar(page):
        raise RuntimeError("No se pudo aceptar el trámite.")

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    if not pasar_avisos(page):
        raise RuntimeError("No se pudo superar la pantalla de avisos.")

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    rellenar_formulario_datos(page)

    estado = comprobar_estado(page)
    if estado != "ok":
        return estado

    if not pulsar_solicitar_cita(page):
        raise RuntimeError("No se encontró el botón Solicitar Cita.")

    return evaluar_resultado_final(page)


def main():
    enviar_telegram(
        f"☁️ *Bot de citas iniciado en la nube*\n\n"
        f"📍 Provincia: {PROVINCIA}\n"
        "⏱ Comprobación cada 6 minutos."
    )

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )

        contexto = navegador.new_context(
            locale="es-ES",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        try:
            while True:
                page = contexto.new_page()
                resultado = "desconocido"

                try:
                    resultado = ejecutar_ciclo(page)

                except PlaywrightTimeoutError:
                    logging.warning("Tiempo de espera agotado.")

                except Exception as error:
                    logging.exception("Error durante el ciclo: %s", error)

                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

                logging.info(
                    "Resultado: %s | Próxima comprobación en 6 minutos.",
                    resultado,
                )

                time.sleep(INTERVALO)

        finally:
            contexto.close()
            navegador.close()


if __name__ == "__main__":
    main()
