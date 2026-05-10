import { createApp } from "vue";
import { createPinia } from "pinia";
import PetApp from "./PetApp.vue";

export function mountPetVueApp(rootElement: HTMLElement): void {
  rootElement.replaceChildren();
  const app = createApp(PetApp);
  app.use(createPinia());
  app.mount(rootElement);
}
