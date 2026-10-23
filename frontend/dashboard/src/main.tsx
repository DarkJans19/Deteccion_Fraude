import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import LandingDemo from "./LandingDemo";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LandingDemo />
  </StrictMode>,
);
