import { createRoot } from "react-dom/client";
import { App } from "./App";
import { mountPetVueApp } from "./features/pet-vue/mount";
import "./styles.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Root element not found");
}

const currentHash = window.location.hash.toLowerCase();
const isReactRoute = currentHash === "#console" || currentHash === "#settings";

if (isReactRoute) {
  createRoot(rootElement).render(<App />);
} else {
  mountPetVueApp(rootElement);
}
