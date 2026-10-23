import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("alerta-bosa-rol", "admin"),
  );
});

test("lista hashes completos y ubica reportes sin cambiar el zoom", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const thirdParties: string[] = [];
  page.on("request", (request) => {
    if (!request.url().startsWith("http://127.0.0.1:"))
      thirdParties.push(request.url());
  });
  await page.goto("/dashboard/trazabilidad");
  await expect(
    page.getByRole("heading", { name: "Reportes en Bogotá" }),
  ).toBeVisible();
  const reports = page.locator('[aria-label="Lista de reportes"]');
  await expect(
    reports.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(9);
  await expect(reports.locator("code")).toHaveCount(9);
  for (const hash of await reports.locator("code").allTextContents())
    expect(hash).toMatch(/^[a-f0-9]{64}$/);
  await expect(page.getByRole("button", { name: "Nuevo reporte" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("region", {
      name: "Mapa de reportes por localidades de Bogotá",
    }),
  ).toBeVisible();
  await reports
    .getByRole("button", {
      name: "Ver reporte: SMS con un enlace sospechoso",
      exact: true,
    })
    .click();
  await expect(page.locator(".leaflet-popup-content")).toContainText(
    "SMS con un enlace sospechoso",
  );
  await expect(page.locator(".leaflet-popup-content")).toContainText(
    "Notificación de inicio de sesión",
  );
  await expect(page.locator(".leaflet-popup-content")).toContainText(
    "Chapinero",
  );
  await expect(
    page.getByText("Acércate para ver las manzanas", { exact: true }),
  ).toBeVisible();
  await reports
    .getByRole("button", {
      name: "Copiar hash de SMS con un enlace sospechoso",
      exact: true,
    })
    .click();
  const firstHash = await reports.locator("code").first().textContent();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    firstHash,
  );
  expect(errors).toEqual([]);
  expect(thirdParties).toEqual([]);
});

test("cuenta por localidad, filtra y separa los reportes fuera de Bogotá", async ({
  page,
}) => {
  await page.goto("/");
  const stats = page.locator('[aria-label="Estadísticas por localidad"]');
  await expect(
    stats.getByRole("button", { name: /: \d+ reportes$/ }),
  ).toHaveCount(20);
  await expect(stats.getByText("8 en Bogotá", { exact: true })).toBeVisible();
  await expect(
    stats.getByRole("button", { name: "Chapinero: 2 reportes", exact: true }),
  ).toBeVisible();
  await stats
    .getByRole("button", { name: "Chapinero: 2 reportes", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(2);
  await expect(
    page.getByRole("combobox", { name: "Filtrar por localidad" }),
  ).toContainText("Chapinero");
  await page.getByRole("button", { name: "Quitar filtro" }).click();
  await stats.getByRole("button", { name: "Fuera de Bogotá: 1" }).click();
  await expect(
    page.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", {
      name: "Ver reporte: Promoción de inversión por mensaje",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Quitar filtro" }).click();
  await page
    .getByRole("textbox", { name: "Buscar reportes" })
    .fill("NO-EXISTE");
  await expect(
    page.getByText("No hay reportes para este filtro"),
  ).toBeVisible();
  await expect(stats.getByText("0 en Bogotá", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Limpiar filtros" }).click();
  await expect(
    page.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(9);
});

test("un error de integridad limpia los puntos y los conteos", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(9);
  await page.route("**/api/reports", (route) =>
    route.fulfill({
      status: 409,
      json: { error: "El reporte fue alterado", integrity: false },
    }),
  );
  await page.getByRole("button", { name: "Actualizar", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "El reporte fue alterado",
  );
  await expect(page.locator(".report-marker")).toHaveCount(0);
  await expect(page.getByText("Estadísticas no disponibles.")).toBeVisible();
  await page.unroute("**/api/reports");
  await page.getByRole("button", { name: "Reintentar reportes" }).click();
  await expect(
    page.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(9);
});

test("permite reintentar la cartografía y mantiene los estados vacíos", async ({
  page,
}) => {
  await page.route("**/geo/localidades.geojson", (route) =>
    route.fulfill({ status: 503, body: "unavailable" }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "No se pudo cargar la cartografía",
  );
  await page.unroute("**/geo/localidades.geojson");
  await page.getByRole("button", { name: "Reintentar mapa" }).click();
  await expect(page.locator(".leaflet-container")).toBeVisible();
  await page.route("**/api/reports", (route) =>
    route.fulfill({ json: { reports: [] } }),
  );
  await page.getByRole("button", { name: "Actualizar", exact: true }).click();
  await expect(page.getByText("Aún no hay reportes")).toBeVisible();
  await expect(page.locator(".report-marker")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /: 0 reportes$/ })).toHaveCount(
    20,
  );
});

test("mapa y lista funcionan en escritorio y móvil", async ({
  page,
}, testInfo) => {
  const popupFits = async () => {
    const map = await page.locator(".leaflet-container").boundingBox();
    const popup = await page.locator(".leaflet-popup").boundingBox();
    return (
      !!map &&
      !!popup &&
      popup.y >= map.y + 60 &&
      popup.y + popup.height <= map.y + map.height &&
      popup.x >= map.x &&
      popup.x + popup.width <= map.x + map.width
    );
  };
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Ver reporte:", exact: false }),
  ).toHaveCount(9);
  await page.screenshot({
    path: testInfo.outputPath("dashboard-desktop.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", {
      name: "Ver reporte: SMS con un enlace sospechoso",
      exact: true,
    })
    .click();
  await expect.poll(popupFits).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("dashboard-map-detail.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(popupFits).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("dashboard-mobile.png"),
    fullPage: true,
  });
});
