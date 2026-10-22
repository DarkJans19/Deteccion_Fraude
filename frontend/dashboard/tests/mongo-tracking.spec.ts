import { expect, test } from "@playwright/test";

const reportId = "672a1b2c3d4e5f6789012345";
const report = {
  _id: { $oid: reportId },
  description: "Mensaje SMS fraudulento que suplanta una entidad bancaria.",
  messageContent:
    "BANCO: Su cuenta está bloqueada.\nValide sus datos en https://ejemplo.invalid/verificar\n<script>alert(1)</script>",
  date: { $date: "2026-10-22T15:30:00.000Z" },
  hashCode: "a".repeat(64),
  location: { type: "Point", coordinates: [-74.0721, 4.711] },
};

test("muestra documentos MongoDB y añade seguimiento conservando el hash del reporte", async ({
  page,
}) => {
  const events: object[] = [];
  let status = "registrado";
  await page.route("**/api/reports", (route) =>
    route.fulfill({ json: { reports: [report] } }),
  );
  await page.route(`**/api/reports/${reportId}/tracking`, async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().headers()["x-csrf-token"]).toBeTruthy();
      const data = route.request().postDataJSON();
      expect(data).toEqual({
        status: "confirmado",
        note: "La entidad confirmó la suplantación.",
      });
      status = data.status;
      const event = {
        _id: { $oid: "672a1b2c3d4e5f6789012346" },
        date: { $date: "2026-10-22T16:00:00.000Z" },
        ...data,
        hashCode: "b".repeat(64),
      };
      events.push(event);
      await route.fulfill({ status: 201, json: event });
    } else {
      await route.fulfill({
        json: { reportId, status, registeredAt: report.date.$date, events },
      });
    }
  });
  await page.goto("/");
  await page
    .getByRole("button", {
      name: `Ver reporte: ${report.description}`,
      exact: true,
    })
    .click();
  const panel = page.getByRole("region", {
    name: "Seguimiento del caso",
    exact: true,
  });
  await expect(panel).toBeVisible();
  await expect(
    panel.getByRole("region", { name: "Contenido completo del mensaje" }),
  ).toContainText(report.messageContent);
  await expect(panel.getByText("Registrado", { exact: true })).toHaveCount(2);
  await panel
    .getByRole("combobox", { name: "Estado del caso", exact: true })
    .selectOption("confirmado");
  await panel
    .getByRole("textbox", { name: "Nota de seguimiento", exact: true })
    .fill("La entidad confirmó la suplantación.");
  await panel
    .getByRole("button", { name: "Guardar seguimiento", exact: true })
    .click();
  await expect(panel.getByRole("status")).toHaveText("Seguimiento guardado.");
  await expect(panel.getByLabel("Historial del caso")).toContainText(
    "La entidad confirmó la suplantación.",
  );
  await expect(panel.locator("strong")).toHaveText("Fraude confirmado");
  await expect(page.getByLabel("Lista de reportes").locator("code")).toHaveText(
    report.hashCode,
  );
});

test("un reporte anterior muestra que falta el mensaje original", async ({
  page,
}) => {
  const { messageContent: _message, ...legacy } = report;
  await page.route("**/api/reports", (route) =>
    route.fulfill({ json: { reports: [legacy] } }),
  );
  await page.route(`**/api/reports/${reportId}/tracking`, (route) =>
    route.fulfill({
      json: {
        reportId,
        status: "registrado",
        registeredAt: report.date.$date,
        events: [],
      },
    }),
  );
  await page.goto("/");
  await page
    .getByRole("button", {
      name: `Ver reporte: ${report.description}`,
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Este reporte anterior no contiene el mensaje original."),
  ).toBeVisible();
});

test("un fallo al guardar conserva la nota y permite reintentar", async ({
  page,
}) => {
  await page.route("**/api/reports", (route) =>
    route.fulfill({ json: { reports: [report] } }),
  );
  await page.route(`**/api/reports/${reportId}/tracking`, (route) =>
    route.fulfill(
      route.request().method() === "POST"
        ? {
            status: 503,
            json: { error: "No se pudo acceder a MongoDB; vuelve a intentar" },
          }
        : {
            json: {
              reportId,
              status: "registrado",
              registeredAt: report.date.$date,
              events: [],
            },
          },
    ),
  );
  await page.goto("/");
  await page
    .getByRole("button", {
      name: `Ver reporte: ${report.description}`,
      exact: true,
    })
    .click();
  const panel = page.getByRole("region", {
    name: "Seguimiento del caso",
    exact: true,
  });
  await panel
    .getByRole("textbox", { name: "Nota de seguimiento", exact: true })
    .fill("Revisión pendiente.");
  await panel
    .getByRole("button", { name: "Guardar seguimiento", exact: true })
    .click();
  await expect(panel.getByRole("alert")).toContainText(
    "No se pudo acceder a MongoDB",
  );
  await expect(
    panel.getByRole("textbox", { name: "Nota de seguimiento", exact: true }),
  ).toHaveValue("Revisión pendiente.");
  await expect(
    panel.getByRole("button", { name: "Guardar seguimiento", exact: true }),
  ).toBeEnabled();
});
