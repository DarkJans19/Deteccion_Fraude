import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type RefObject } from "react";
import { ArrowUp, LoaderCircle, Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type MoonChatComposerProps = {
  value: string;
  busy: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
};

// Conserva las medidas, el editor y los controles de la referencia original.
// El título, el fondo y las acciones rápidas se componen en LandingDemo.
export default function MoonChatComposer({
  value, busy, inputRef, onChange, onSubmit, onKeyDown,
}: MoonChatComposerProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  useEffect(() => {
    function resize() {
      const field = inputRef.current;
      if (!field) return;
      field.style.height = "48px";
      field.style.height = `${Math.min(150, Math.max(48, field.scrollHeight))}px`;
      field.style.overflowY = field.scrollHeight > 150 ? "auto" : "hidden";
    }
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [value, inputRef]);

  return (
    <form className="af-composer" onSubmit={onSubmit}>
      <label className="af-sr-only" htmlFor="af-message">Escribe un mensaje o pega un enlace</label>
      <Textarea
        id="af-message" ref={inputRef} value={value} maxLength={12000} disabled={busy}
        onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown}
        placeholder="Pega un enlace o cuéntame qué recibiste…" rows={1}
      />
      <input ref={fileInput} type="file" className="af-sr-only" tabIndex={-1}
        aria-label="Archivo de prueba" accept="image/*,.pdf,.txt" disabled={busy}
        onChange={(event) => setFileName(event.target.files?.[0]?.name ?? "")} />
      {fileName && <div className="af-file-preview" role="status">
        <span>Archivo de prueba: {fileName}. Solo selección local; no se envía ni analiza.</span>
        <button type="button" aria-label="Quitar archivo" onClick={() => {
          setFileName("");
          if (fileInput.current) fileInput.current.value = "";
        }}><X size={14} aria-hidden="true" /></button>
      </div>}
      <div className="af-composer-bottom">
        <Button className="af-attach" type="button" variant="ghost" size="icon"
          disabled={busy} aria-label="Seleccionar archivo de prueba"
          onClick={() => fileInput.current?.click()}>
          <Paperclip aria-hidden="true" />
        </Button>
        <Button className="af-send" type="submit" size="icon"
          disabled={busy || !value.trim()} aria-label="Enviar consulta de prueba">
          {busy ? <LoaderCircle className="af-spin" aria-hidden="true" /> : <ArrowUp aria-hidden="true" />}
        </Button>
      </div>
    </form>
  );
}
