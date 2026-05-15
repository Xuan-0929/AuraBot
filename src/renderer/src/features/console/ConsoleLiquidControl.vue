<template>
  <div class="console-liquid-control">
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
      <div class="console-liquid-control-content">
        <slot />
      </div>
    </LiquidGlass>
  </div>
</template>

<script setup lang="ts">
import { LiquidGlass } from "@wxperia/liquid-glass-vue";

withDefaults(
  defineProps<{
    radius?: number;
    padding?: string;
    blurAmount?: number;
    displacementScale?: number;
    saturation?: number;
    aberrationIntensity?: number;
    elasticity?: number;
  }>(),
  {
    radius: 18,
    padding: "0",
    blurAmount: 0.045,
    displacementScale: 10,
    saturation: 118,
    aberrationIntensity: 0,
    elasticity: 0.04
  }
);

const glassStyle = {
  position: "relative" as const,
  top: "50%",
  left: "50%",
  zIndex: 1
};
</script>
