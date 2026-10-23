import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  ArrowRight,
  BarChart3,
  Check,
  Copy,
  Link2,
  LoaderCircle,
  History,
  MessageSquare,
  Plus,
  ShieldCheck,
  X,
  Info,
  Lock,
  User,
} from "lucide-react";
import MoonChatComposer from "@/components/ui/ruixen-moon-chat";
import RadialGlowBackground from "@/components/ui/radial-glow-background";
import {
  analysisApi,
  type AnalysisHealth,
  type GeneratedResponse,
  type ImagePrediction,
  type Prediction,
  type QrAnalysis,
} from "@/lib/analysis";
import AboutPage from "./AboutPage";
import "./LandingDemo.css";

// Reutiliza el dashboard actual y lo carga al abrir Informes.
const Dashboard = lazy(() => import("./App"));

type View = "chat" | "informes" | "about";
type Role = "ciudadano" | "admin";

// Modo demo: el tipo de sesión se elige en el encabezado, sin inicio de sesión.
// Ciudadano: chat, historial y Proyecto. Admin: solo Informes.
const roleViews: Record<Role, View[]> = {
  ciudadano: ["chat", "about"],
  admin: ["informes"],
};
const homeView: Record<Role, View> = { ciudadano: "chat", admin: "informes" };
const viewHash: Record<View, string> = {
  chat: "#/chat",
  informes: "#/informes",
  about: "#/proyecto",
};
const ROLE_KEY = "alerta-bosa-rol";

function readRole(): Role {
  try {
    return localStorage.getItem(ROLE_KEY) === "admin" ? "admin" : "ciudadano";
  } catch {
    return "ciudadano";
  }
}

type AnalysisMode = "analysis" | "generation" | "qr";
type TurnResult =
  | { kind: "prediction"; data: Prediction | ImagePrediction }
  | { kind: "generation"; data: GeneratedResponse }
  | { kind: "qr"; data: QrAnalysis }
  | { kind: "error"; message: string };
type Turn = {
  id: string;
  content: string;
  mode: AnalysisMode;
  fileName?: string;
  result: TurnResult;
};
type Conversation = { id: string; title: string; turns: Turn[] };

const suggestions = [
  {
    icon: Link2,
    title: "Analizar URL / QR",
    description: "Revisa el destino, las redirecciones y el dominio.",
    prompt: "https://ejemplo.invalid/verificar",
    mode: "qr" as AnalysisMode,
  },
  {
    icon: MessageSquare,
    title: "Consultar un mensaje",
    description: "Pega un SMS, correo o mensaje de WhatsApp.",
    prompt:
      "Recibí este mensaje en un grupo de WhatsApp de mi barrio en Bosa y quiero revisarlo: ",
    mode: "analysis" as AnalysisMode,
  },
];

function readView(): View {
  if (window.location.hash === "#/informes") return "informes";
  if (window.location.hash === "#/proyecto") return "about";
  return "chat";
}

function newConversation(): Conversation {
  return { id: crypto.randomUUID(), title: "Nueva consulta", turns: [] };
}

function predictionTone(result: Prediction) {
  if (result.es_fraude === true) return "alto";
  if (result.es_fraude === false) return "bajo";
  return "revision";
}

function copyableResult(turn: Turn) {
  if (turn.result.kind === "error") return turn.result.message;
  if (turn.result.kind === "generation")
    return turn.result.data.respuesta_cruda;
  if (turn.result.kind === "qr") {
    const { summary, destination } = turn.result.data;
    return [
      summary.title,
      ...summary.reasons,
      `Destino: ${destination.url}`,
    ].join("\n");
  }
  return [turn.result.data.veredicto, turn.result.data.respuesta_cruda]
    .filter(Boolean)
    .join("\n");
}

function TurnResultView({ turn }: { turn: Turn }) {
  if (turn.result.kind === "error")
    return (
      <div className="af-result af-result-alto" role="alert">
        <span className="af-eyebrow">ERROR DEL SERVICIO</span>
        <h2>No se completó la consulta</h2>
        <p>{turn.result.message}</p>
      </div>
    );

  if (turn.result.kind === "generation")
    return (
      <div className="af-result af-result-generation">
        <span className="af-eyebrow">RESPUESTA DEL MODELO</span>
        <h2>Orientación generada</h2>
        <p className="af-preserve">
          {turn.result.data.respuesta_cruda || "El modelo no generó contenido."}
        </p>
      </div>
    );

  if (turn.result.kind === "qr") {
    const {
      summary,
      scanned,
      destination,
      redirect,
      destination_analysis: analysis,
    } = turn.result.data;
    const tone =
      summary.risk_level === "high"
        ? "alto"
        : summary.risk_level === "low"
          ? "bajo"
          : "revision";
    const probability = Number(analysis.phishing_probability);
    return (
      <>
        <div className={`af-result af-result-${tone}`}>
          <span className="af-eyebrow">ANÁLISIS DE URL / QR</span>
          <h2>{summary.title}</h2>
          <ul>
            {summary.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
        <dl className="af-result-details">
          <div>
            <dt>URL leída</dt>
            <dd>{scanned.url}</dd>
          </div>
          <div>
            <dt>Destino final</dt>
            <dd>{destination.url}</dd>
          </div>
          <div>
            <dt>Redirecciones</dt>
            <dd>
              {redirect.redirect_count ?? 0}
              {redirect.domain_changed ? " · cambia de dominio" : ""}
            </dd>
          </div>
          <div>
            <dt>Probabilidad estimada</dt>
            <dd>
              {Number.isFinite(probability)
                ? `${(probability * 100).toFixed(1)} %`
                : "No disponible"}
            </dd>
          </div>
        </dl>
      </>
    );
  }

  const result = turn.result.data;
  const image = "texto_extraido" in result ? (result as ImagePrediction) : null;
  return (
    <>
      <div className={`af-result af-result-${predictionTone(result)}`}>
        <span className="af-eyebrow">ANÁLISIS INTEGRAL</span>
        <h2>{result.veredicto}</h2>
        <p className="af-preserve">
          {result.respuesta_cruda ||
            "El resultado se obtuvo únicamente a partir de los enlaces detectados."}
        </p>
      </div>
      <dl className="af-result-details">
        <div>
          <dt>Mensaje</dt>
          <dd>{result.veredicto_mensaje ?? "No aplica"}</dd>
        </div>
        <div>
          <dt>Enlaces</dt>
          <dd>{result.veredicto_url ?? "No aplica"}</dd>
        </div>
        <div>
          <dt>URLs evaluadas</dt>
          <dd>{result.analisis_urls.length}</dd>
        </div>
      </dl>
      {result.analisis_urls.length > 0 && (
        <div className="af-extracted">
          <h3>Detalle de enlaces</h3>
          <ul>
            {result.analisis_urls.map((item, index) => {
              const url = String(
                item.normalized_url ?? item.url ?? `Enlace ${index + 1}`,
              );
              const classification = String(
                item.classification ?? item.risk_level ?? "Sin clasificación",
              );
              const probability = Number(item.phishing_probability);
              return (
                <li key={`${url}-${index}`}>
                  <strong>{url}</strong> · {classification}
                  {Number.isFinite(probability)
                    ? ` · ${(probability * 100).toFixed(1)} %`
                    : ""}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {image && (
        <div className="af-extracted">
          <h3>Contenido extraído de la imagen</h3>
          <p>{image.texto_extraido || "Sin texto OCR."}</p>
          {image.qr_detectados.length > 0 && (
            <>
              <h3>Códigos QR detectados</h3>
              <ul>
                {image.qr_detectados.map((qr) => (
                  <li key={qr}>{qr}</li>
                ))}
              </ul>
            </>
          )}
          {image.advertencias.length > 0 && (
            <ul className="af-warnings">
              {image.advertencias.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

export default function LandingDemo() {
  const [role, setRole] = useState<Role>(readRole);
  const [requestedView, setView] = useState<View>(readView);
  // Si la URL pide una vista que no corresponde a la sesión, se muestra la de inicio de esa sesión.
  const view = roleViews[role].includes(requestedView)
    ? requestedView
    : homeView[role];
  const [conversations, setConversations] = useState<Conversation[]>(() => [
    newConversation(),
  ]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<AnalysisMode>("analysis");
  const [maxTokens, setMaxTokens] = useState(120);
  const [file, setFile] = useState<File | null>(null);
  const [health, setHealth] = useState<AnalysisHealth | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [copyError, setCopyError] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const active =
    conversations.find((item) => item.id === activeId) ?? conversations[0];

  useEffect(() => {
    const update = () => {
      setView(readView());
      setSidebarOpen(false);
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(() => {
    if (view === requestedView) return;
    window.history.replaceState(null, "", viewHash[view]);
    setView(view);
  }, [view, requestedView]);

  useEffect(() => {
    let activeRequest = true;
    analysisApi
      .health()
      .then((result) => {
        if (activeRequest) setHealth(result);
      })
      .catch(() => {
        if (activeRequest) setHealthError(true);
      });
    return () => {
      activeRequest = false;
    };
  }, []);

  function changeRole(next: Role) {
    if (next === role) return;
    setRole(next);
    try {
      localStorage.setItem(ROLE_KEY, next);
    } catch {
      // Sin almacenamiento disponible: la sesión elegida solo dura esta visita.
    }
    setSidebarOpen(false);
    window.location.hash = viewHash[homeView[next]];
  }

  useEffect(() => {
    if (active.turns.length) end.current?.scrollIntoView({ block: "end" });
  }, [active.id, active.turns.length, view]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const close = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        setSidebarOpen(false);
        document.getElementById("af-menu")?.focus();
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [sidebarOpen]);

  function startNew() {
    if (busy) return;
    if (active.turns.length) {
      const next = newConversation();
      setConversations((current) => [next, ...current]);
      setActiveId(next.id);
    }
    setDraft("");
    setFile(null);
    setCopyError("");
    setSidebarOpen(false);
    window.location.hash = "/chat";
    textarea.current?.focus();
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if ((!content && !file) || busy) return;
    const conversationId = active.id;
    const submittedFile = file;
    const submittedMode = mode;
    setDraft("");
    setFile(null);
    setBusy(true);
    let result: TurnResult;
    try {
      if (submittedFile)
        result = {
          kind: "prediction",
          data: await analysisApi.analyzeImage(submittedFile, content),
        };
      else if (submittedMode === "generation")
        result = {
          kind: "generation",
          data: await analysisApi.generate(content, maxTokens),
        };
      else if (submittedMode === "qr")
        result = { kind: "qr", data: await analysisApi.analyzeQr(content) };
      else
        result = {
          kind: "prediction",
          data: await analysisApi.analyzeText(content),
        };
    } catch (failure) {
      result = { kind: "error", message: (failure as Error).message };
    }
    const turn: Turn = {
      id: crypto.randomUUID(),
      content: content || `Imagen: ${submittedFile?.name}`,
      mode: submittedFile ? "analysis" : submittedMode,
      fileName: submittedFile?.name,
      result,
    };
    setConversations((current) =>
      current.map((item) =>
        item.id === conversationId
          ? {
              ...item,
              title: item.turns.length ? item.title : turn.content.slice(0, 42),
              turns: [...item.turns, turn],
            }
          : item,
      ),
    );
    setBusy(false);
    requestAnimationFrame(() => textarea.current?.focus());
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      send();
    }
  }

  async function copy(turn: Turn) {
    try {
      await navigator.clipboard.writeText(copyableResult(turn));
      setCopied(turn.id);
      setCopyError("");
    } catch {
      setCopyError(
        "No se pudo copiar. Puedes seleccionar el texto y copiarlo manualmente.",
      );
    }
  }

  return (
    <div
      className={`af-app ${view === "chat" && !active.turns.length ? "af-is-welcome" : ""}`}
    >
      <RadialGlowBackground />
      <a
        className="af-skip"
        href="#af-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("af-content")?.focus();
        }}
      >
        Saltar al contenido
      </a>
      <header className="af-topbar">
        <a
          className="af-brand"
          href={viewHash[homeView[role]]}
          aria-label="Alerta Bosa, inicio"
        >
          <span className="af-brand-icon">
            <ShieldCheck size={23} aria-hidden="true" />
          </span>
          <span>
            Alerta Bosa <small>PREVENCIÓN DIGITAL</small>
          </span>
        </a>
        <div className="af-header-actions">
          {view === "chat" && (
            <button
              id="af-menu"
              className="af-history-toggle"
              type="button"
              aria-label="Consultas"
              aria-expanded={sidebarOpen}
              aria-controls="af-sidebar"
              onClick={() => setSidebarOpen((current) => !current)}
            >
              <History size={16} aria-hidden="true" />
              <span>Consultas</span>
            </button>
          )}
          <nav className="af-navigation" aria-label="Navegación principal">
            {role === "ciudadano" ? (
              <>
                <a
                  href="#/chat"
                  aria-current={view === "chat" ? "page" : undefined}
                >
                  <MessageSquare size={16} aria-hidden="true" />
                  <span>Chat</span>
                </a>
                <a
                  href="#/proyecto"
                  aria-current={view === "about" ? "page" : undefined}
                >
                  <Info size={16} aria-hidden="true" />
                  <span>Proyecto</span>
                </a>
              </>
            ) : (
              <a
                href="#/informes"
                aria-current={view === "informes" ? "page" : undefined}
              >
                <BarChart3 size={16} aria-hidden="true" />
                <span>Informes</span>
              </a>
            )}
          </nav>
          <div
            className="af-role-switch"
            role="group"
            aria-label="Tipo de sesión"
            title="Modo demostración: aún no hay inicio de sesión"
          >
            <button
              type="button"
              aria-pressed={role === "ciudadano"}
              onClick={() => changeRole("ciudadano")}
            >
              <User size={14} aria-hidden="true" />
              <span>Ciudadano</span>
            </button>
            <button
              type="button"
              aria-pressed={role === "admin"}
              onClick={() => changeRole("admin")}
            >
              <Lock size={14} aria-hidden="true" />
              <span>Admin</span>
            </button>
          </div>
          <span
            className={`af-demo-pill ${healthError ? "is-offline" : ""}`}
            title={
              health
                ? `Safe Browsing: ${health.safe_browsing_configured ? "configurado" : "no configurado"}`
                : undefined
            }
          >
            <span />{" "}
            {healthError
              ? "API no disponible"
              : health?.status === "degraded"
                ? "API degradada"
                : health
                  ? "API conectada"
                  : "Conectando API"}
          </span>
        </div>
      </header>

      <div
        className={`af-layout ${view === "informes" ? "af-layout-reports" : ""}`}
      >
        {view === "chat" && (
          <>
            {sidebarOpen && (
              <button
                className="af-backdrop"
                type="button"
                aria-label="Cerrar panel de consultas"
                onClick={() => setSidebarOpen(false)}
              />
            )}
            <aside
              id="af-sidebar"
              className={`af-sidebar ${sidebarOpen ? "is-open" : ""}`}
            >
              <div className="af-sidebar-top">
                <span className="af-eyebrow">TU ESPACIO</span>
                <button
                  className="af-close-sidebar af-icon-button"
                  type="button"
                  aria-label="Cerrar panel"
                  onClick={() => setSidebarOpen(false)}
                >
                  <X size={18} />
                </button>
              </div>
              <button
                className="af-new-chat"
                type="button"
                onClick={startNew}
                disabled={busy}
              >
                <Plus size={18} aria-hidden="true" /> Nueva consulta{" "}
                <span>+</span>
              </button>
              <div className="af-history">
                <p className="af-eyebrow">EN ESTA SESIÓN</p>
                {conversations.map((conversation) => (
                  <button
                    key={conversation.id}
                    type="button"
                    disabled={busy}
                    aria-pressed={conversation.id === active.id}
                    onClick={() => {
                      setActiveId(conversation.id);
                      setDraft("");
                      setSidebarOpen(false);
                      setCopyError("");
                    }}
                  >
                    <MessageSquare size={15} aria-hidden="true" />
                    <span>{conversation.title}</span>
                  </button>
                ))}
              </div>
              <div className="af-sidebar-bottom">
                <ShieldCheck size={22} aria-hidden="true" />
                <h2>Una pausa puede protegerte.</h2>
                <p>
                  Verifica antes de compartir datos personales, códigos o
                  contraseñas.
                </p>
                <a href="#/proyecto">
                  Conoce el proyecto <ArrowRight size={14} aria-hidden="true" />
                </a>
                <small>
                  Las consultas son temporales y se borran al recargar.
                </small>
              </div>
            </aside>
          </>
        )}

        {view === "informes" ? (
          <main id="af-content" tabIndex={-1} className="af-reports">
            <div className="af-reports-intro">
              <span className="af-eyebrow">INFORMES</span>
              <p>Consulta los reportes del proyecto y su trazabilidad.</p>
              <span className="af-reports-note">
                Vista de administración · demo sin inicio de sesión
              </span>
            </div>
            <Suspense
              fallback={
                <p className="af-loading" role="status">
                  <LoaderCircle className="af-spin" size={18} /> Cargando
                  informes…
                </p>
              }
            >
              <Dashboard />
            </Suspense>
          </main>
        ) : view === "about" ? (
          <AboutPage />
        ) : (
          <main id="af-content" tabIndex={-1} className="af-chat">
            <div className="af-chat-heading">
              <span>
                <span className="af-status-dot" /> Asistente de prevención
              </span>
              <span>Enlaces y mensajes</span>
            </div>
            <div
              className={`af-chat-body ${active.turns.length ? "has-messages" : ""}`}
            >
              {!active.turns.length ? (
                <section className="af-welcome">
                  <h1>
                    Tu tranquilidad empieza
                    <br />
                    con una <em>buena pregunta.</em>
                  </h1>
                  <p className="af-welcome-description">
                    Hecho para los comercios y familias de Bosa: revisa
                    mensajes, enlaces y llamadas sospechosas antes de pagar,
                    responder o compartir un código.
                  </p>
                </section>
              ) : (
                <section className="af-conversation" aria-label="Conversación">
                  {active.turns.map((turn) => (
                    <div className="af-turn" key={turn.id}>
                      <div className="af-user-message">
                        <span className="af-message-label">Tú</span>
                        <p>{turn.content}</p>
                      </div>
                      <article className="af-assistant-message">
                        <div className="af-assistant-avatar">
                          <ShieldCheck size={19} aria-hidden="true" />
                        </div>
                        <div className="af-answer">
                          <span className="af-message-label">
                            Asistente · resultado del servicio
                          </span>
                          <TurnResultView turn={turn} />
                          <button
                            className="af-copy"
                            type="button"
                            onClick={() => void copy(turn)}
                          >
                            {copied === turn.id ? (
                              <Check size={14} />
                            ) : (
                              <Copy size={14} />
                            )}
                            {copied === turn.id
                              ? "Copiado"
                              : "Copiar respuesta"}
                          </button>
                        </div>
                      </article>
                    </div>
                  ))}
                </section>
              )}
              {busy && (
                <p className="af-loading" role="status">
                  <LoaderCircle className="af-spin" size={17} /> Analizando la
                  consulta…
                </p>
              )}
              <div ref={end} />
            </div>
            <div className="af-composer-area">
              <MoonChatComposer
                value={draft}
                busy={busy}
                file={file}
                inputRef={textarea}
                onChange={setDraft}
                onFileChange={setFile}
                onSubmit={send}
                onKeyDown={onKeyDown}
              />
              <div className="af-quick-actions" aria-label="Acciones rápidas">
                {suggestions.map(
                  ({
                    icon: Icon,
                    title,
                    description,
                    prompt,
                    mode: nextMode,
                  }) => (
                    <button
                      key={title}
                      type="button"
                      disabled={busy}
                      title={description}
                      onClick={() => {
                        setMode(nextMode);
                        setDraft(prompt);
                        textarea.current?.focus();
                      }}
                    >
                      <Icon size={16} aria-hidden="true" />
                      <span>{title}</span>
                    </button>
                  ),
                )}
                <a href="#/proyecto">
                  <Info size={16} aria-hidden="true" /> ¿Qué es Alerta Bosa?
                </a>
                <button type="button" disabled={busy} onClick={startNew}>
                  <Plus size={16} aria-hidden="true" /> Nueva consulta
                </button>
              </div>
              <div className="af-demo-controls">
                <label htmlFor="af-mode">Tipo de consulta:</label>
                <select
                  id="af-mode"
                  value={mode}
                  disabled={busy || Boolean(file)}
                  onChange={(event) =>
                    setMode(event.target.value as AnalysisMode)
                  }
                >
                  <option value="analysis">Analizar mensaje y enlaces</option>
                  <option value="qr">Analizar URL / QR</option>
                  <option value="generation">Generar orientación</option>
                </select>
                {file && <span>La imagen usará OCR y detección QR.</span>}
                {mode === "generation" && !file && (
                  <label className="af-token-control">
                    Longitud
                    <select
                      aria-label="Longitud máxima de la respuesta"
                      value={maxTokens}
                      disabled={busy}
                      onChange={(event) =>
                        setMaxTokens(Number(event.target.value))
                      }
                    >
                      <option value={60}>Breve · 60 tokens</option>
                      <option value={120}>Media · 120 tokens</option>
                      <option value={256}>Amplia · 256 tokens</option>
                      <option value={512}>Máxima · 512 tokens</option>
                    </select>
                  </label>
                )}
              </div>
              <p className="af-disclaimer">
                Análisis automatizado: confirma decisiones importantes por un
                canal oficial · Enter para enviar · Shift + Enter para un salto
                de línea.
              </p>
              <div className="af-sr-only" role="status">
                {busy
                  ? "Analizando consulta"
                  : active.turns.length
                    ? "Resultado disponible"
                    : ""}
              </div>
              {copyError && (
                <p className="af-copy-error" role="alert">
                  {copyError}
                </p>
              )}
            </div>
          </main>
        )}
      </div>
    </div>
  );
}
