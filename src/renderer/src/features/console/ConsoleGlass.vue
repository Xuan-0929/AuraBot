<template>
  <div :class="['console-glass', `console-glass-${variant}`]">
    <LiquidGlass
      :padding="padding"
      :corner-radius="radius"
      :blur-amount="blurAmount"
      :displacement-scale="displacementScale"
      :saturation="saturation"
      :aberration-intensity="aberrationIntensity"
      :elasticity="elasticity"
      :mode="'standard'"
      :style="glassStyle"
    >
      <div class="console-glass-content">
        <slot />
      </div>
    </LiquidGlass>
  </div>
</template>

<script setup lang="ts">
import { LiquidGlass } from "@wxperia/liquid-glass-vue";

withDefaults(
  defineProps<{
    variant?: "panel" | "pill" | "control";
    radius?: number;
    padding?: string;
    blurAmount?: number;
    displacementScale?: number;
    saturation?: number;
    aberrationIntensity?: number;
    elasticity?: number;
  }>(),
  {
    variant: "panel",
    radius: 24,
    padding: "0",
    blurAmount: 0.07,
    displacementScale: 34,
    saturation: 126,
    aberrationIntensity: 0.8,
    elasticity: 0.06
  }
);

const glassStyle = {
  position: "relative" as const,
  top: "50%",
  left: "50%",
  zIndex: 1
};
</script>
