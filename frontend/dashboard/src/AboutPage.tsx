import { ArrowDown } from "lucide-react";
import TextBlockAnimation from "@/components/ui/text-block-animation";
import TerritorialMap from "@/components/territorial-map";
import "./AboutPage.css";

// Paleta azul noche + naranja, la misma del fondo del chat.
const ORANGE = "#f97316";
const BLUE = "#3b82f6";
const BLUE_DEEP = "#1d4ed8";
const BLUE_SOFT = "#93c5fd";

const steps = [
  {
    title: "Consulta sin complicaciones",
    text: "Pega el mensaje, el enlace o el código QR que recibiste en el chat web o por Telegram. No hay que instalar nada nuevo.",
  },
  {
    title: "Análisis con IA",
    text: "Un modelo de lenguaje ligero, afinado para reconocer mensajes de fraude, y un detector de enlaces estiman el nivel de riesgo.",
  },
  {
    title: "Verificación con fuentes abiertas",
    text: "Los enlaces se contrastan con listas públicas de sitios peligrosos, como Google Safe Browsing.",
  },
  {
    title: "Certificado de reporte",
    text: "Cada caso queda guardado de forma anónima con una huella SHA-256: si alguien altera el reporte, la huella deja de coincidir.",
  },
  {
    title: "Mapa territorial",
    text: "Los reportes se agrupan de forma anónima por localidad para ver dónde se concentran los casos, empezando por Bosa.",
  },
];

export default function AboutPage() {
  return (
    <main id="af-content" tabIndex={-1} className="af-about">
      {/* 1. Portada */}
      <section className="af-about-hero">
        <div className="af-about-hero-inner">
          <span className="af-about-eyebrow">LOCALIDAD 7 · BOSA · BOGOTÁ D. C.</span>
          <TextBlockAnimation blockColor={ORANGE} animateOnScroll={false} delay={0.2} duration={0.8}>
            <h1>
              Bosa no se deja
              <br />
              <span className="af-about-highlight">tumbar.</span>
            </h1>
          </TextBlockAnimation>
          <TextBlockAnimation blockColor={BLUE} animateOnScroll={false} delay={0.7} stagger={0.05}>
            <p className="af-about-lead">
              Alerta Bosa revisa los mensajes, enlaces y códigos QR sospechosos antes de que
              pagues, respondas o compartas un código.
            </p>
          </TextBlockAnimation>
        </div>
        <div className="af-about-scroll" aria-hidden="true">
          <span>Desliza para conocer el proyecto</span>
          <ArrowDown size={20} />
        </div>
      </section>

      {/* 2. El problema */}
      <section className="af-about-section">
        <div className="af-about-section-inner">
          <TextBlockAnimation blockColor={BLUE} duration={0.7}>
            <h2>¿Por qué Bosa?</h2>
          </TextBlockAnimation>
          <TextBlockAnimation blockColor={ORANGE} stagger={0.03}>
            <p>
              Bosa es una de las localidades más jóvenes y densamente pobladas del suroccidente de
              Bogotá, con una economía <strong>mayoritariamente informal</strong> y herencia del
              pueblo Muisca. También concentra uno de los mayores índices de fraude digital contra
              comercios y familias de la ciudad:{" "}
              <strong>phishing, SIM swapping, estafas por WhatsApp y suplantación de entidades financieras</strong>.
            </p>
          </TextBlockAnimation>
          <div className="af-about-quote">
            <TextBlockAnimation blockColor={BLUE_SOFT} duration={0.6}>
              <p>
                “Las víctimas carecen de un canal ágil y confiable para detectar, reportar y dar
                seguimiento a estos casos.”
              </p>
            </TextBlockAnimation>
            <span className="af-about-quote-source">Reto Hackathon Colombia 5.0 · Bosa</span>
          </div>
        </div>
      </section>

      {/* 3. Cómo funciona */}
      <section className="af-about-section af-about-section-alt">
        <div className="af-about-section-inner">
          <TextBlockAnimation blockColor={ORANGE} duration={0.7}>
            <h2>Cómo funciona</h2>
          </TextBlockAnimation>
          <ol className="af-about-steps">
            {steps.map((step, index) => (
              <li key={step.title} className="af-about-step">
                <span className="af-about-step-number" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="af-about-step-text">
                  <TextBlockAnimation blockColor={BLUE_DEEP} stagger={0.05}>
                    <h3>{step.title}</h3>
                    <p>{step.text}</p>
                  </TextBlockAnimation>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

            {/* 4. Mapa territorial (solo totales por localidad) */}
      <section className="af-about-section" aria-labelledby="af-about-map-title">
        <div className="af-about-section-inner af-about-section-wide">
          <TextBlockAnimation blockColor={BLUE} duration={0.7}>
            <h2 id="af-about-map-title">Alertas en el territorio</h2>
          </TextBlockAnimation>
          <TextBlockAnimation blockColor={ORANGE} stagger={0.03}>
            <p>
              Cada consulta reportada suma al mapa de su localidad. Así la comunidad, las
              organizaciones y las autoridades pueden ver dónde se concentran los intentos de
              fraude, <strong>sin exponer datos de nadie</strong>.
            </p>
          </TextBlockAnimation>
          <TerritorialMap />
        </div>
      </section>

      {/* 5. Llamado a la acción */}
      <footer className="af-about-footer">
        <TextBlockAnimation blockColor={ORANGE} duration={0.8}>
          <a href="#/chat" className="af-about-cta">Haz tu consulta.</a>
        </TextBlockAnimation>
        <p className="af-about-footnote">
          Prototipo desarrollado para la Hackathon Colombia 5.0 · Localidad de Bosa, 2026.
        </p>
      </footer>
    </main>
  );
}
