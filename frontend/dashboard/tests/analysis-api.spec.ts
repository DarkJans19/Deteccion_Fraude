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
  context,
}) => {
  const called: string[] = [];
  const savedReports: Record<string, unknown>[] = [];
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 4.711, longitude: -74.0721 });
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: { csrf: "test-csrf", model_available: true } }),
  );
  await page.route("**/api/reports", async (route) => {
    savedReports.push(route.request().postDataJSON());
    return route.fulfill({
      status: 201,
      json: { _id: { $oid: `6a000000000000000000000${savedReports.length}` } },
    });
  });
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
  await expect(page.getByRole("status")).toContainText("guardado para seguimiento");
  expect(savedReports).toHaveLength(1);
  expect(savedReports[0]).toMatchObject({
    riskLevel: "RIESGO ALTO",
    messageContent: "Mensaje sospechoso https://fraude.example",
    location: { type: "Point", coordinates: [-74.0721, 4.711] },
  });

  await page.getByLabel("Tipo de consulta:").selectOption("generation");
  await page.getByLabel("Longitud máxima de la respuesta").selectOption("256");
  await send(page, "Explícame cómo validar este mensaje");
  await expect(
    page.getByText("Orientación generada por el modelo."),
  ).toBeVisible();
  expect(savedReports).toHaveLength(1);

  await page.getByLabel("Tipo de consulta:").selectOption("qr");
  await send(page, "https://inicio.example");
  await expect(page.getByText("https://destino.example")).toBeVisible();
  await expect(page.getByRole("status")).toContainText("guardado para seguimiento");
  expect(savedReports).toHaveLength(2);
  expect(savedReports[1]).toMatchObject({ riskLevel: "RIESGO ALTO" });

  await page.locator('input[type="file"]').setInputFiles({
    name: "captura.png",
    mimeType: "image/png",
    buffer: Buffer.from("fake image"),
  });
  await page.getByRole("button", { name: "Enviar consulta" }).click();
  await expect(page.getByText("Texto del comprobante")).toBeVisible();
  await expect(page.getByRole("status")).toContainText("guardado para seguimiento");
  expect(savedReports).toHaveLength(3);
  expect(savedReports[2]).toMatchObject({ riskLevel: "RIESGO ALTO" });
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

test("no registra análisis con riesgo bajo ni no concluyente", async ({
  page,
  context,
}) => {
  let saved = 0;
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: { csrf: "test-csrf", model_available: true } }),
  );
  await page.route("**/api/reports", async (route) => {
    saved += 1;
    return route.fulfill({ status: 201, json: { _id: { $oid: "6a0000000000000000000001" } } });
  });
  await page.route("**/analysis-api/**", (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({
        json: {
          status: "ok",
          model_available: true,
          safe_browsing_configured: true,
          redirect_checks_enabled: true,
        },
      });
    const text = route.request().postDataJSON().texto as string;
    const low = text.includes("seguro");
    return route.fulfill({
      json: {
        ...prediction,
        es_fraude: low ? false : null,
        veredicto: low ? "RIESGO BAJO" : "RIESGO NO CONCLUYENTE",
        veredicto_mensaje: low ? "RIESGO BAJO" : "RIESGO NO CONCLUYENTE",
        veredicto_url: null,
      },
    });
  });
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 4.711, longitude: -74.0721 });
  await page.goto("/#/chat");
  await send(page, "Mensaje seguro");
  await expect(page.getByRole("heading", { name: "RIESGO BAJO" })).toBeVisible();
  await send(page, "Mensaje sin señales suficientes");
  await expect(page.getByRole("heading", { name: "RIESGO NO CONCLUYENTE" })).toBeVisible();
  expect(saved).toBe(0);
});
