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
} from "lucide-react";
import MoonChatComposer from "@/components/ui/ruixen-moon-chat";
import "./LandingDemo.css";

// Reutiliza el dashboard actual y lo carga al abrir Informes.
const Dashboard = lazy(() => import("./App"));

type View = "chat" | "informes";
type Scenario = "revision" | "alto" | "bajo";
type Turn = {
  id: string;
  content: string;
  scenario: Scenario;
};
type Conversation = { id: string; title: string; turns: Turn[] };

const scenarios: Record<Scenario, { title: string; text: string; advice: string }> = {
  revision: {
    title: "Requiere revisión",
    text: "Ejemplo de un resultado con información insuficiente para concluir.",
    advice: "Verifica el remitente y consulta a la entidad a través de un canal que ya conozcas.",
  },
  alto: {
    title: "Riesgo alto",
    text: "Ejemplo de una alerta que el detector podría mostrar ante señales de suplantación.",
    advice: "Evita abrir el enlace o compartir códigos. Confirma la solicitud por un canal oficial.",
  },
  bajo: {
    title: "Riesgo bajo",
    text: "Ejemplo de una respuesta sin señales relevantes identificadas. No garantiza seguridad.",
    advice: "Confirma que la comunicación corresponda a una operación que reconoces.",
  },
};

const suggestions = [
  {
    icon: Link2,
    title: "Revisar un enlace",
    description: "Comprende qué revisar antes de abrirlo.",
    prompt: "Quiero revisar este enlace: https://ejemplo.invalid/verificar",
  },
  {
    icon: MessageSquare,
    title: "Consultar un mensaje",
    description: "Pega un SMS, correo o mensaje de WhatsApp.",
    prompt: "Recibí este mensaje y quiero revisarlo: ",
  },
];

function readView(): View {
  return window.location.hash === "#/informes" ? "informes" : "chat";
}

function newConversation(): Conversation {
  return { id: crypto.randomUUID(), title: "Nueva consulta", turns: [] };
}

export default function LandingDemo() {
  const [view, setView] = useState<View>(readView);
  const [conversations, setConversations] = useState<Conversation[]>(() => [newConversation()]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [scenario, setScenario] = useState<Scenario>("revision");
  const [busy, setBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [copyError, setCopyError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const active = conversations.find((item) => item.id === activeId) ?? conversations[0];

  useEffect(() => {
    const update = () => {
      setView(readView());
      setSidebarOpen(false);
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

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
    setCopyError("");
    setSidebarOpen(false);
    window.location.hash = "/chat";
    textarea.current?.focus();
  }

  function send(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || busy) return;
    const conversationId = active.id;
    const turn: Turn = { id: crypto.randomUUID(), content, scenario };
    setDraft("");
    setBusy(true);
    // Simula solamente la espera de la interfaz. No consulta un detector.
    timer.current = setTimeout(() => {
      setConversations((current) => current.map((item) => item.id === conversationId
        ? { ...item, title: item.turns.length ? item.title : content.slice(0, 42), turns: [...item.turns, turn] }
        : item));
      setBusy(false);
      timer.current = null;
      textarea.current?.focus();
    }, 450);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  }

  async function copy(turn: Turn) {
    try {
      await navigator.clipboard.writeText(
        `RESULTADO SIMULADO: ${scenarios[turn.scenario].title}\n${scenarios[turn.scenario].text}\n${scenarios[turn.scenario].advice}\nEl contenido no fue evaluado por un detector.`,
      );
      setCopied(turn.id);
      setCopyError("");
    } catch {
      setCopyError("No se pudo copiar. Puedes seleccionar el texto y copiarlo manualmente.");
    }
  }

  return (
    <div className={`af-app ${view === "chat" && !active.turns.length ? "af-is-welcome" : ""}`}
      style={view === "chat" ? {
        backgroundImage: "url('https://cdn.21st.dev/assets/mirror/c3/c333918af688a4a8a3d004652e6c0ee219457a9d84d380eeb31f513d4b59a09f.png')",
        backgroundAttachment: "fixed",
        backgroundSize: "cover",
        backgroundPosition: "center",
      } : undefined}>
      <a className="af-skip" href="#af-content" onClick={(event) => { event.preventDefault(); document.getElementById("af-content")?.focus(); }}>Saltar al contenido</a>
      <header className="af-topbar">
        <a className="af-brand" href="#/chat" aria-label="Antifraudi-inador Bosa, inicio">
          <span className="af-brand-icon"><ShieldCheck size={23} aria-hidden="true" /></span>
          <span>Antifraudi-inador <small>BOSA · PREVENCIÓN DIGITAL</small></span>
        </a>
        <div className="af-header-actions">
          {view === "chat" && <button id="af-menu" className="af-history-toggle" type="button"
            aria-label="Consultas" aria-expanded={sidebarOpen} aria-controls="af-sidebar"
            onClick={() => setSidebarOpen((current) => !current)}>
            <History size={16} aria-hidden="true" /><span>Consultas</span>
          </button>}
        <nav className="af-navigation" aria-label="Navegación principal">
          <a href="#/chat" aria-current={view === "chat" ? "page" : undefined}>
            <MessageSquare size={16} aria-hidden="true" /><span>Chat</span>
          </a>
          <a href="#/informes" aria-current={view === "informes" ? "page" : undefined}>
            <BarChart3 size={16} aria-hidden="true" /><span>Informes</span>
          </a>
        </nav>
        <span className="af-demo-pill"><span /> Prototipo</span>
        </div>
      </header>

      <div className={`af-layout ${view === "informes" ? "af-layout-reports" : ""}`}>
        {view === "chat" && (
          <>
            {sidebarOpen && <button className="af-backdrop" type="button"
              aria-label="Cerrar panel de consultas" onClick={() => setSidebarOpen(false)} />}
            <aside id="af-sidebar" className={`af-sidebar ${sidebarOpen ? "is-open" : ""}`}>
              <div className="af-sidebar-top">
                <span className="af-eyebrow">TU ESPACIO</span>
                <button className="af-close-sidebar af-icon-button" type="button"
                  aria-label="Cerrar panel" onClick={() => setSidebarOpen(false)}><X size={18} /></button>
              </div>
              <button className="af-new-chat" type="button" onClick={startNew} disabled={busy}>
                <Plus size={18} aria-hidden="true" /> Nueva consulta <span>+</span>
              </button>
              <div className="af-history">
                <p className="af-eyebrow">EN ESTA SESIÓN</p>
                {conversations.map((conversation) => (
                  <button key={conversation.id} type="button" disabled={busy}
                    aria-pressed={conversation.id === active.id}
                    onClick={() => {
                      setActiveId(conversation.id);
                      setDraft("");
                      setSidebarOpen(false);
                      setCopyError("");
                    }}>
                    <MessageSquare size={15} aria-hidden="true" /><span>{conversation.title}</span>
                  </button>
                ))}
              </div>
              <div className="af-sidebar-bottom">
                <ShieldCheck size={22} aria-hidden="true" />
                <h2>Una pausa puede protegerte.</h2>
                <p>Verifica antes de compartir datos personales, códigos o contraseñas.</p>
                <a href="#/informes">Explorar informes <ArrowRight size={14} aria-hidden="true" /></a>
                <small>Las consultas son temporales y se borran al recargar.</small>
              </div>
            </aside>
          </>
        )}

        {view === "informes" ? (
          <main id="af-content" tabIndex={-1} className="af-reports">
            <div className="af-reports-intro"><span className="af-eyebrow">INFORMES</span>
              <p>Consulta los reportes del proyecto y su trazabilidad.</p>
              <a href="#/chat">Volver al chat <ArrowRight size={15} aria-hidden="true" /></a>
            </div>
            <Suspense fallback={<p className="af-loading" role="status"><LoaderCircle className="af-spin" size={18} /> Cargando informes…</p>}>
              <Dashboard />
            </Suspense>
          </main>
        ) : (
          <main id="af-content" tabIndex={-1} className="af-chat">
            <div className="af-chat-heading"><span><span className="af-status-dot" /> Asistente de prevención</span>
              <span>Enlaces y mensajes</span>
            </div>
            <div className={`af-chat-body ${active.turns.length ? "has-messages" : ""}`}>
              {!active.turns.length ? (
                <section className="af-welcome">
                  <h1>Tu tranquilidad empieza<br />con una <em>buena pregunta.</em></h1>
                  <p className="af-welcome-description">Un espacio para revisar lo que recibes y tomar decisiones con más información.</p>
                </section>
              ) : (
                <section className="af-conversation" aria-label="Conversación">
                  {active.turns.map((turn) => (
                    <div className="af-turn" key={turn.id}>
                      <div className="af-user-message"><span className="af-message-label">Tú</span><p>{turn.content}</p></div>
                      <article className="af-assistant-message">
                        <div className="af-assistant-avatar"><ShieldCheck size={19} aria-hidden="true" /></div>
                        <div className="af-answer"><span className="af-message-label">Asistente · respuesta simulada</span>
                          <div className={`af-result af-result-${turn.scenario}`}><span className="af-eyebrow">ESCENARIO DE PRUEBA</span>
                            <h2>{scenarios[turn.scenario].title}</h2><p>{scenarios[turn.scenario].text}</p>
                          </div>
                          <h3>Qué puedes hacer</h3><p>{scenarios[turn.scenario].advice}</p>
                          <p className="af-result-note">Este resultado depende del escenario seleccionado. El contenido aún no se ha evaluado con un detector.</p>
                          <button className="af-copy" type="button" onClick={() => void copy(turn)}>
                            {copied === turn.id ? <Check size={14} /> : <Copy size={14} />}
                            {copied === turn.id ? "Copiado" : "Copiar respuesta"}
                          </button>
                        </div>
                      </article>
                    </div>
                  ))}
                </section>
              )}
              {busy && <p className="af-loading" role="status"><LoaderCircle className="af-spin" size={17} /> Preparando respuesta de prueba…</p>}
              <div ref={end} />
            </div>
            <div className="af-composer-area">
              <MoonChatComposer value={draft} busy={busy} inputRef={textarea}
                onChange={setDraft} onSubmit={send} onKeyDown={onKeyDown} />
              <div className="af-quick-actions" aria-label="Acciones rápidas">
                {suggestions.map(({ icon: Icon, title, description, prompt }) => (
                  <button key={title} type="button" disabled={busy} title={description}
                    onClick={() => { setDraft(prompt); textarea.current?.focus(); }}>
                    <Icon size={16} aria-hidden="true" /><span>{title}</span>
                  </button>
                ))}
                <a href="#/informes"><BarChart3 size={16} aria-hidden="true" /> Ver informes</a>
                <button type="button" disabled={busy} onClick={startNew}>
                  <Plus size={16} aria-hidden="true" /> Nueva consulta
                </button>
              </div>
              <div className="af-demo-controls"><label htmlFor="af-scenario">Demo · probar resultado:</label>
                <select id="af-scenario" value={scenario} disabled={busy} onChange={(event) => setScenario(event.target.value as Scenario)}>
                  <option value="revision">Requiere revisión</option><option value="alto">Riesgo alto</option><option value="bajo">Riesgo bajo</option>
                </select>
              </div>
              <p className="af-disclaimer">Respuestas simuladas · Enter para enviar · Shift + Enter para un salto de línea.</p>
              <div className="af-sr-only" role="status">{busy ? "Preparando respuesta" : active.turns.length ? "Respuesta simulada disponible" : ""}</div>
              {copyError && <p className="af-copy-error" role="alert">{copyError}</p>}
            </div>
          </main>
        )}
      </div>
    </div>
  );
}
