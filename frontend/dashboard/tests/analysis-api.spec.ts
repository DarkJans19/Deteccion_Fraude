import { expect, test, type Page } from "@playwright/test";

const prediction = {
  es_fraude: true,
  veredicto: "RIESGO ALTO",
  respuesta_cruda: "No abras el enlace y confirma con la entidad.",
  veredicto_mensaje: "RIESGO ALTO",
  veredicto_url: "RIESGO ALTO",
  analisis_urls: [{ url: "https://fraude.example" }],
};

async function send(page: Page, value: string) {
  const input = page.getByRole("textbox", {
    name: "Escribe un mensaje o pega un enlace",
  });
  await input.fill(value);
  await page.getByRole("button", { name: "Enviar consulta" }).click();
}

test("conecta estado, texto, generación, QR e imagen con FastAPI", async ({
  page,
}) => {
  const called: string[] = [];
  await page.route("**/analysis-api/**", async (route) => {
    const url = new URL(route.request().url());
    called.push(url.pathname);
    if (url.pathname.endsWith("/health"))
      return route.fulfill({
        json: {
          status: "ok",
          model_available: true,
          safe_browsing_configured: true,
          redirect_checks_enabled: true,
        },
      });
    if (url.pathname.endsWith("/respuesta")) {
      expect(route.request().postDataJSON()).toEqual({
        texto: "Explícame cómo validar este mensaje",
        max_tokens: 256,
      });
      return route.fulfill({
        json: { respuesta_cruda: "Orientación generada por el modelo." },
      });
    }
    if (url.pathname.endsWith("/analyze"))
      return route.fulfill({
        json: {
          scanned: {
            url: "https://inicio.example",
            hostname: "inicio.example",
            registrable_domain: "inicio.example",
          },
          destination: {
            url: "https://destino.example",
            hostname: "destino.example",
            registrable_domain: "destino.example",
          },
          redirect: {
            status: "resolved",
            redirect_count: 1,
            domain_changed: true,
          },
          initial_analysis: {},
          destination_analysis: { phishing_probability: 0.87 },
          authenticity: {},
          summary: {
            risk_level: "high",
            title: "Riesgo alto: no abras el enlace",
            reasons: ["El dominio cambió."],
          },
        },
      });
    if (url.pathname.endsWith("/analizar-imagen"))
      return route.fulfill({
        json: {
          ...prediction,
          texto_extraido: "Texto del comprobante",
          qr_detectados: ["https://qr.example"],
          advertencias: [],
        },
      });
    return route.fulfill({ json: prediction });
  });

  await page.goto("/#/chat");
  await expect(page.getByText("API conectada")).toBeVisible();

  await send(page, "Mensaje sospechoso https://fraude.example");
  await expect(
    page.getByRole("heading", { name: "RIESGO ALTO" }),
  ).toBeVisible();

  await page.getByLabel("Tipo de consulta:").selectOption("generation");
  await page.getByLabel("Longitud máxima de la respuesta").selectOption("256");
  await send(page, "Explícame cómo validar este mensaje");
  await expect(
    page.getByText("Orientación generada por el modelo."),
  ).toBeVisible();

  await page.getByLabel("Tipo de consulta:").selectOption("qr");
  await send(page, "https://inicio.example");
  await expect(page.getByText("https://destino.example")).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: "captura.png",
    mimeType: "image/png",
    buffer: Buffer.from("fake image"),
  });
  await page.getByRole("button", { name: "Enviar consulta" }).click();
  await expect(page.getByText("Texto del comprobante")).toBeVisible();
  expect(called).toEqual(
    expect.arrayContaining([
      "/analysis-api/health",
      "/analysis-api/analizar",
      "/analysis-api/respuesta",
      "/analysis-api/analyze",
      "/analysis-api/analizar-imagen",
    ]),
  );
});
