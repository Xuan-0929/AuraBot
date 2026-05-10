import { onMounted, onUnmounted, ref } from "vue";

export interface FocusAt {
  x: number;
  y: number;
}

export function useMouseTracking() {
  const focusAt = ref<FocusAt>({ x: 0, y: 0 });
  let unsubscribe: (() => void) | undefined;

  onMounted(() => {
    unsubscribe = window.aurabot.onPetCursorPosition((position) => {
      focusAt.value = {
        x: position.localX,
        y: position.localY
      };
    });
  });

  onUnmounted(() => {
    unsubscribe?.();
    unsubscribe = undefined;
  });

  return { focusAt };
}
