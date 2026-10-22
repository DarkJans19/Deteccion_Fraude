import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:18765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    geolocation: { latitude: 4.609719, longitude: -74.081749, accuracy: 12.4 },
    permissions: ["geolocation"],
    locale: "es-CO",
    timezoneId: "America/Bogota",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        channel: process.platform === "win32" ? "msedge" : undefined,
        viewport: { width: 1440, height: 1100 },
      },
    },
  ],
  webServer: {
    command: "python -m backend.tests.serve_dashboard",
    cwd: "../..",
    url: "http://127.0.0.1:18765/api/session",
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
