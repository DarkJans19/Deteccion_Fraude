"use strict";
// La vista crea y consulta registros; no modifica reportes existentes.
const $ = (id) => document.getElementById(id);
const names = {fraude: "Positivo para fraude", no_fraude: "Negativo para fraude", incierto: "Incierto", sin_analisis: "Sin análisis al registrar", sms: "SMS", correo: "Correo", otro: "Otro"};
let reports = [], selected = null, token = "", loading = false;
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
const date = (value) => new Date(value).toLocaleString("es-CO", {dateStyle: "medium", timeStyle: "long"});
const label = (report) => report.analysis?.label || "sin_analisis";
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
  $("message").hidden = false;
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": token}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "No se pudo completar la operación");
  return result;
}
async function refresh() {
  if (loading) return;
  loading = true;
  $("refresh").disabled = true;
  $("new-report").disabled = true;
  try {
    const session = await api("/api/session");
    token = session.csrf;
    $("model-status").textContent = session.model_available ? "Detector local conectado" : "LLM sin conectar";
    $("creation-note").textContent = session.model_available
      ? "El modelo analizará el contenido antes de guardarlo. El resultado quedará incluido en el registro y no se modificará después."
      : "El LLM no está conectado: este reporte se guardará sin análisis y no podrá completarse después.";
    reports = (await api("/api/reports")).reports;
    $("load-status").textContent = "Reportes cargados";
    $("total").textContent = reports.length;
    $("positive").textContent = reports.filter(r => r.fraud_positive === true).length;
    $("negative").textContent = reports.filter(r => r.fraud_positive === false).length;
    $("unknown").textContent = reports.filter(r => r.fraud_positive === null).length;
    renderRows(); renderDetail();
  } catch (error) {
    reports = []; selected = null; renderRows(); renderDetail();
    $("load-status").textContent = "No se pudieron cargar los reportes";
    for (const id of ["total", "positive", "negative", "unknown"]) $(id).textContent = "—";
    $("empty").innerHTML = "<h3>Reportes no disponibles</h3><p>Revisa el aviso y vuelve a intentar.</p>";
    message(error.message, true);
  } finally {
    loading = false;
    $("refresh").disabled = false;
    $("new-report").disabled = !token;
  }
}
function renderRows() {
  const query = $("search").value.toLocaleLowerCase();
  const filtered = reports.filter(r => (!$("label-filter").value || label(r) === $("label-filter").value) && `${r.title} ${r.id} ${r.source_ref} ${r.reported_by}`.toLocaleLowerCase().includes(query));
  $("result-count").textContent = `${filtered.length} reportes`;
  $("rows").innerHTML = filtered.map(r => `<tr class="${r.id === selected ? "selected" : ""}"><td><button class="report-link" data-id="${escape(r.id)}">${escape(r.title)}</button><span class="small">${names[r.source]} · ${escape(r.id.slice(0, 8))}<br>Reportado por: ${escape(r.reported_by)}</span></td><td><span class="badge ${label(r)}">${names[label(r)]}</span></td><td class="date">${escape(date(r.created_at))}</td></tr>`).join("");
  $("empty").hidden = filtered.length > 0;
  $("empty").innerHTML = reports.length ? "<h3>No hay coincidencias</h3><p>Prueba con otro filtro o término de búsqueda.</p>" : "<span class='empty-icon'>≡</span><h3>Aún no hay reportes</h3><p>Registra una evidencia para consultarla aquí.</p>";
  for (const button of $("rows").querySelectorAll("button")) button.addEventListener("click", () => {
    selected = button.dataset.id; renderRows(); renderDetail();
  });
}
function renderDetail() {
  const report = reports.find(r => r.id === selected);
  $("detail-empty").hidden = Boolean(report); $("detail-content").hidden = !report;
  if (!report) { $("detail-content").replaceChildren(); return; }
  const analysis = report.analysis;
  $("detail-content").innerHTML = `
    <p class="eyebrow">DETALLE DEL REPORTE</p><h2>${escape(report.title)}</h2>
    <p class="detail-id">${escape(report.id)}</p>
    <dl class="report-info">
      <dt>Usuario que reporta</dt><dd>${escape(report.reported_by)}</dd>
      <dt>Fecha y hora del reporte</dt><dd>${escape(date(report.created_at))}</dd>
      <dt>Origen</dt><dd>${names[report.source]}</dd>
      <dt>Resultado de fraude según el modelo</dt><dd>${names[label(report)]}</dd>
    </dl>
    <p class="section-label">Contenido registrado</p><pre class="evidence">${escape(report.text)}</pre>
    ${report.source_ref ? `<p class="muted">Referencia: ${escape(report.source_ref)}</p>` : ""}
    ${analysis ? `<p class="section-label">Análisis incluido al registrar</p><div class="analysis-box"><p>${escape(analysis.explanation)}</p><span class="small">${escape(analysis.model_name)} · ${escape(analysis.model_version)}</span>${analysis.score === null ? "" : `<p>Puntuación del modelo: ${escape(analysis.score)} (sin calibración acreditada)</p>`}</div>` : ""}
    <p class="section-label">Hash del reporte completo · SHA-256</p><code class="hash">${escape(report.hash)}</code>
    <div class="detail-actions"><a class="secondary" href="/api/reports/${encodeURIComponent(report.id)}/export" download>↓ Exportar reporte JSON</a></div>`;
}
$("new-report").addEventListener("click", () => { $("create-error").textContent = ""; $("create-dialog").showModal(); });
$("close-dialog").addEventListener("click", () => $("create-dialog").close());
$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const result = await api("/api/reports", Object.fromEntries(new FormData(form)));
    selected = result.id; form.reset(); $("create-dialog").close();
    message("Reporte guardado con su hash. El registro no admite modificaciones.");
    await refresh();
  } catch (error) { $("create-error").textContent = error.message; }
  finally { button.disabled = false; }
});
for (const id of ["search", "label-filter"]) $(id).addEventListener("input", renderRows);
$("refresh").addEventListener("click", () => { $("message").hidden = true; refresh(); });
refresh();
