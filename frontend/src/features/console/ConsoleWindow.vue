<template>
  <main
    :class="[
      'console-window',
      {
        'console-window-task-sidebar-collapsed': isTaskSidebarInRailMode,
        'console-window-task-sidebar-rail-mode': isTaskSidebarInRailMode,
        'console-window-task-sidebar-hover-preview': isTaskSidebarPreviewVisible,
        'console-window-task-sidebar-closing': isTaskSidebarClosing,
        'console-window-task-sidebar-layout-transitioning': isTaskSidebarLayoutTransitioning,
        'console-window-task-sidebar-resizing': isTaskSidebarResizing
      }
    ]"
    :style="taskSidebarStyle"
    :data-task-sidebar-mode="taskSidebarMode"
    :aria-label="t(store.displayLanguage, 'consoleTitle')"
  >
    <div ref="consoleBodyRef" class="console-body" @transitionend="handleTaskSidebarLayoutTransitionEnd">
      <section class="console-chat" :aria-label="t(store.displayLanguage, 'mainConversation')">
        <MessageList :messages="store.messages" :display-language="store.displayLanguage" />
        <ConsoleGlassPanel variant="control" class="console-composer-glass">
          <Composer
            :display-language="store.displayLanguage"
            :agent-status="store.agentStatus"
            :on-send="(content, attachments) => store.sendMessage(content, 'console', attachments)"
          />
        </ConsoleGlassPanel>
      </section>

      <div
        v-show="!isTaskSidebarInRailMode"
        ref="taskSidebarResizeHandleRef"
        class="console-task-sidebar-resize-handle"
        role="separator"
        aria-orientation="vertical"
        tabindex="0"
        :aria-label="t(store.displayLanguage, 'resizeTaskStickyNotes')"
        :aria-valuemin="TASK_SIDEBAR_MIN_WIDTH"
        :aria-valuemax="TASK_SIDEBAR_MAX_WIDTH"
        :aria-valuenow="taskSidebarWidth"
        @pointerdown="handleTaskSidebarResizeStart"
        @pointermove="handleTaskSidebarResizeMove"
        @pointerup="finishTaskSidebarResize"
        @pointercancel="finishTaskSidebarResize"
        @lostpointercapture="finishTaskSidebarResize"
        @keydown="handleTaskSidebarResizeKeydown"
      >
        <span aria-hidden="true" />
      </div>

      <aside
        :class="['console-task-sidebar', { 'console-task-sidebar-collapsed': isTaskSidebarInRailMode }]"
        :aria-label="t(store.displayLanguage, 'consoleTaskViewportTitle')"
        @pointerenter="handleTaskSidebarPointerEnter"
        @pointerleave="handleTaskSidebarPointerLeave"
        @focusin="handleTaskSidebarFocusIn"
        @focusout="handleTaskSidebarFocusOut"
      >
        <div class="console-task-sidebar-rail">
          <button
            ref="taskSidebarToggleRef"
            type="button"
            class="console-task-sidebar-toggle"
            :aria-expanded="isTaskSidebarContentVisible"
            aria-controls="console-task-sidebar-content"
            :aria-label="t(store.displayLanguage, isTaskSidebarContentVisible ? 'collapseTaskStickyNotes' : 'expandTaskStickyNotes')"
            :title="t(store.displayLanguage, isTaskSidebarContentVisible ? 'collapseTaskStickyNotes' : 'expandTaskStickyNotes')"
            @click="toggleTaskSidebar"
          >
            <Icon :icon="isTaskSidebarContentVisible ? 'solar:alt-arrow-right-linear' : 'solar:alt-arrow-left-linear'" aria-hidden="true" />
          </button>
          <span v-if="isTaskSidebarInRailMode && consoleTasks.length > 0" class="console-task-sidebar-count">{{ consoleTasks.length }}</span>
        </div>

        <Transition name="task-sidebar-preview" @after-leave="handleTaskSidebarContentAfterLeave">
          <div v-show="isTaskSidebarContentVisible" id="console-task-sidebar-content" class="console-task-sidebar-content">
            <header class="console-task-sidebar-header">
              <strong>{{ t(store.displayLanguage, 'consoleTaskViewportTitle') }}</strong>
              <button
                type="button"
                class="console-task-settings-button"
                :aria-label="t(store.displayLanguage, 'openAuraBotSettings')"
                :title="t(store.displayLanguage, 'settings')"
                @click="void store.openSettingsWindow()"
              >
                <Icon icon="solar:settings-linear" aria-hidden="true" />
              </button>
            </header>

            <TaskDetailPanel
              v-if="store.selectedTaskDetail"
              :detail="store.selectedTaskDetail"
              :display-language="store.displayLanguage"
              :on-close="() => store.closeTaskDetail()"
            />
            <div v-else class="console-task-sidebar-list">
              <TaskStickyNotes
                v-if="consoleTasks.length > 0"
                :tasks="consoleTasks"
                variant="console"
                layout="vertical"
                :show-header="false"
                :aria-label="false"
                :display-language="store.displayLanguage"
                :on-cancel-task="(taskId) => void store.cancelTask(taskId)"
                :on-delete-task="(taskId) => void store.deleteTask(taskId)"
                :on-view-task-detail="openTaskDetail"
              />
              <p v-else class="console-task-sidebar-empty">{{ t(store.displayLanguage, 'taskSidebarEmpty') }}</p>
            </div>
          </div>
        </Transition>
      </aside>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Icon, addCollection } from "@iconify/vue";
import solarIcons from "@iconify-json/solar/icons.json";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import Composer from "../chat/Composer.vue";
import MessageList from "../chat/MessageList.vue";
import TaskDetailPanel from "../tasks/TaskDetailPanel.vue";
import TaskStickyNotes from "../tasks/TaskStickyNotes.vue";
import { pickConsoleStickyTasks } from "../tasks/taskStickyNotes";
import ConsoleGlassPanel from "./ConsoleGlassPanel.vue";
import {
  AUTO_COLLAPSE_CHAT_MIN_WIDTH,
  TASK_SIDEBAR_MAX_WIDTH,
  TASK_SIDEBAR_MIN_WIDTH,
  calculateTaskSidebarTargetWidth,
  calculateTaskSidebarWidth,
  readStoredTaskSidebarWidth,
  shouldAutoCollapseTaskSidebar,
  shouldCollapseTaskSidebarFromDrag,
  writeStoredTaskSidebarWidth
} from "./taskSidebarLayout";

addCollection(solarIcons);

const store = useAuraBotStore();
const consoleTasks = computed(() => pickConsoleStickyTasks(store.tasks));
const consoleBodyRef = ref<HTMLElement | null>(null);
const taskSidebarResizeHandleRef = ref<HTMLElement | null>(null);
const taskSidebarToggleRef = ref<HTMLButtonElement | null>(null);
const taskSidebarWidth = ref(readStoredTaskSidebarWidth(window.localStorage));
const consoleBodyWidth = ref(0);
const isTaskSidebarManuallyCollapsed = ref(false);
const isTaskSidebarPreviewVisible = ref(false);
const isTaskSidebarPreviewPinned = ref(false);
const isTaskSidebarClosing = ref(false);
const isTaskSidebarLayoutTransitioning = ref(false);
const isTaskSidebarContentReady = ref(true);
const isTaskSidebarFocused = ref(false);
const isTaskSidebarResizing = ref(false);
const canHoverPreview = ref(false);

let taskSidebarResizeObserver: ResizeObserver | undefined;
let hoverPreviewMediaQuery: MediaQueryList | undefined;
let hoverPreviewEnterTimer: ReturnType<typeof window.setTimeout> | undefined;
let hoverPreviewLeaveTimer: ReturnType<typeof window.setTimeout> | undefined;
let taskSidebarResizeAnimationFrame: number | undefined;
let taskSidebarContentRevealFrame: number | undefined;
let taskSidebarLayoutTransitionFrame: number | undefined;
let taskSidebarLayoutTransitionTimer: ReturnType<typeof window.setTimeout> | undefined;
let taskSidebarResizeState:
  | {
      pointerId: number;
      startClientX: number;
      startWidth: number;
      currentClientX: number;
      handle: HTMLElement;
    }
  | undefined;

const isTaskSidebarAutoCollapsed = computed(() =>
  shouldAutoCollapseTaskSidebar(consoleBodyWidth.value, taskSidebarWidth.value)
);
const isTaskSidebarInRailMode = computed(
  () => !isTaskSidebarResizing.value && (isTaskSidebarManuallyCollapsed.value || isTaskSidebarAutoCollapsed.value)
);
const isTaskSidebarPreviewActive = computed(
  () => isTaskSidebarPreviewVisible.value || isTaskSidebarPreviewPinned.value
);
const isTaskSidebarContentVisible = computed(
  () =>
    !isTaskSidebarClosing.value &&
    isTaskSidebarContentReady.value &&
    (!isTaskSidebarInRailMode.value || isTaskSidebarPreviewActive.value)
);
const taskSidebarMode = computed(() => {
  if (isTaskSidebarResizing.value) {
    return "resizing";
  }
  if (isTaskSidebarManuallyCollapsed.value) {
    return "user-collapsed";
  }
  if (isTaskSidebarClosing.value) {
    return "closing";
  }
  if (isTaskSidebarAutoCollapsed.value) {
    return isTaskSidebarPreviewActive.value ? "hover-preview" : "auto-collapsed";
  }
  return "expanded";
});
const taskSidebarStyle = computed(() => ({ "--task-sidebar-width": `${taskSidebarWidth.value}px` }));

onMounted(() => {
  const consoleBody = consoleBodyRef.value;
  if (consoleBody) {
    syncConsoleBodyWidth();
    taskSidebarResizeObserver = new ResizeObserver((entries) => {
      consoleBodyWidth.value = entries[0]?.contentRect.width ?? consoleBody.clientWidth;
    });
    taskSidebarResizeObserver.observe(consoleBody);
  }

  hoverPreviewMediaQuery = window.matchMedia("(hover: hover) and (pointer: fine)");
  updateHoverPreviewAvailability(hoverPreviewMediaQuery);
  hoverPreviewMediaQuery.addEventListener("change", updateHoverPreviewAvailability);
  window.addEventListener("resize", syncConsoleBodyWidth);
});

onBeforeUnmount(() => {
  taskSidebarResizeObserver?.disconnect();
  hoverPreviewMediaQuery?.removeEventListener("change", updateHoverPreviewAvailability);
  window.removeEventListener("resize", syncConsoleBodyWidth);
  clearHoverPreviewTimers();
  if (taskSidebarResizeAnimationFrame !== undefined) {
    window.cancelAnimationFrame(taskSidebarResizeAnimationFrame);
  }
  if (taskSidebarContentRevealFrame !== undefined) {
    window.cancelAnimationFrame(taskSidebarContentRevealFrame);
  }
  clearTaskSidebarLayoutTransition();
  finishTaskSidebarResize();
});

function openTaskDetail(taskId: string) {
  isTaskSidebarClosing.value = false;
  isTaskSidebarContentReady.value = true;
  isTaskSidebarManuallyCollapsed.value = false;
  if (isTaskSidebarAutoCollapsed.value) {
    isTaskSidebarPreviewPinned.value = true;
  }
  void store.openTaskDetail(taskId);
}

function updateHoverPreviewAvailability(event: MediaQueryListEvent | MediaQueryList) {
  canHoverPreview.value = event.matches;
  if (!event.matches) {
    isTaskSidebarPreviewVisible.value = false;
  }
}

function syncConsoleBodyWidth() {
  consoleBodyWidth.value = consoleBodyRef.value?.getBoundingClientRect().width ?? 0;
}

function toggleTaskSidebar() {
  clearHoverPreviewTimers();
  if (isTaskSidebarClosing.value) {
    return;
  }
  if (isTaskSidebarContentVisible.value) {
    if (!isTaskSidebarInRailMode.value) {
      collapseTaskSidebarWithAnimation();
      return;
    }
    isTaskSidebarManuallyCollapsed.value = true;
    isTaskSidebarPreviewVisible.value = false;
    isTaskSidebarPreviewPinned.value = false;
    isTaskSidebarContentReady.value = false;
    taskSidebarToggleRef.value?.focus();
    return;
  }

  if (isTaskSidebarAutoCollapsed.value) {
    isTaskSidebarManuallyCollapsed.value = false;
    isTaskSidebarPreviewPinned.value = true;
    isTaskSidebarContentReady.value = true;
    return;
  }
  beginTaskSidebarLayoutTransition(() => {
    isTaskSidebarManuallyCollapsed.value = false;
    isTaskSidebarPreviewPinned.value = false;
    revealTaskSidebarContentAfterLayout();
  });
}

function collapseTaskSidebarWithAnimation() {
  clearHoverPreviewTimers();
  if (isTaskSidebarClosing.value || isTaskSidebarInRailMode.value) {
    return;
  }
  beginTaskSidebarLayoutTransition(() => {
    isTaskSidebarClosing.value = true;
    isTaskSidebarManuallyCollapsed.value = true;
  });
}

function handleTaskSidebarContentAfterLeave() {
  if (!isTaskSidebarClosing.value) {
    return;
  }
  isTaskSidebarClosing.value = false;
  isTaskSidebarContentReady.value = false;
  taskSidebarToggleRef.value?.focus();
}

function revealTaskSidebarContentAfterLayout() {
  isTaskSidebarContentReady.value = false;
  if (taskSidebarContentRevealFrame !== undefined) {
    window.cancelAnimationFrame(taskSidebarContentRevealFrame);
  }
  taskSidebarContentRevealFrame = window.requestAnimationFrame(() => {
    taskSidebarContentRevealFrame = undefined;
    isTaskSidebarContentReady.value = true;
  });
}

function beginTaskSidebarLayoutTransition(applyLayout: () => void) {
  clearTaskSidebarLayoutTransition();
  isTaskSidebarLayoutTransitioning.value = true;
  taskSidebarLayoutTransitionFrame = window.requestAnimationFrame(() => {
    taskSidebarLayoutTransitionFrame = undefined;
    applyLayout();
    taskSidebarLayoutTransitionTimer = window.setTimeout(() => {
      isTaskSidebarLayoutTransitioning.value = false;
      taskSidebarLayoutTransitionTimer = undefined;
    }, 360);
  });
}

function clearTaskSidebarLayoutTransition() {
  if (taskSidebarLayoutTransitionFrame !== undefined) {
    window.cancelAnimationFrame(taskSidebarLayoutTransitionFrame);
    taskSidebarLayoutTransitionFrame = undefined;
  }
  if (taskSidebarLayoutTransitionTimer !== undefined) {
    window.clearTimeout(taskSidebarLayoutTransitionTimer);
    taskSidebarLayoutTransitionTimer = undefined;
  }
  isTaskSidebarLayoutTransitioning.value = false;
}

function handleTaskSidebarLayoutTransitionEnd(event: TransitionEvent) {
  if (event.target !== consoleBodyRef.value || event.propertyName !== "grid-template-columns") {
    return;
  }
  clearTaskSidebarLayoutTransition();
}

function handleTaskSidebarPointerEnter() {
  if (!canHoverPreview.value || isTaskSidebarManuallyCollapsed.value || !isTaskSidebarAutoCollapsed.value) {
    return;
  }
  clearHoverPreviewLeaveTimer();
  hoverPreviewEnterTimer = window.setTimeout(() => {
    isTaskSidebarPreviewVisible.value = true;
  }, 120);
}

function handleTaskSidebarPointerLeave() {
  clearHoverPreviewEnterTimer();
  scheduleHoverPreviewClose();
}

function handleTaskSidebarFocusIn() {
  isTaskSidebarFocused.value = true;
  clearHoverPreviewLeaveTimer();
  if (!isTaskSidebarManuallyCollapsed.value && isTaskSidebarAutoCollapsed.value) {
    isTaskSidebarPreviewVisible.value = true;
  }
}

function handleTaskSidebarFocusOut(event: FocusEvent) {
  const nextFocusedElement = event.relatedTarget as Node | null;
  if (nextFocusedElement && (event.currentTarget as HTMLElement).contains(nextFocusedElement)) {
    return;
  }
  isTaskSidebarFocused.value = false;
  scheduleHoverPreviewClose();
}

function scheduleHoverPreviewClose() {
  if (
    !isTaskSidebarPreviewVisible.value ||
    isTaskSidebarPreviewPinned.value ||
    isTaskSidebarFocused.value ||
    isTaskSidebarResizing.value ||
    isTaskSidebarManuallyCollapsed.value
  ) {
    return;
  }
  clearHoverPreviewLeaveTimer();
  hoverPreviewLeaveTimer = window.setTimeout(() => {
    if (!isTaskSidebarFocused.value && !isTaskSidebarResizing.value) {
      isTaskSidebarPreviewVisible.value = false;
    }
  }, 320);
}

function clearHoverPreviewTimers() {
  clearHoverPreviewEnterTimer();
  clearHoverPreviewLeaveTimer();
}

function clearHoverPreviewEnterTimer() {
  if (hoverPreviewEnterTimer !== undefined) {
    window.clearTimeout(hoverPreviewEnterTimer);
    hoverPreviewEnterTimer = undefined;
  }
}

function clearHoverPreviewLeaveTimer() {
  if (hoverPreviewLeaveTimer !== undefined) {
    window.clearTimeout(hoverPreviewLeaveTimer);
    hoverPreviewLeaveTimer = undefined;
  }
}

function handleTaskSidebarResizeStart(event: PointerEvent) {
  const handle = event.currentTarget as HTMLElement;
  event.preventDefault();
  clearHoverPreviewTimers();
  clearTaskSidebarLayoutTransition();
  isTaskSidebarClosing.value = false;
  isTaskSidebarContentReady.value = true;
  isTaskSidebarManuallyCollapsed.value = false;
  isTaskSidebarPreviewVisible.value = false;
  isTaskSidebarPreviewPinned.value = false;
  taskSidebarResizeState = {
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startWidth: taskSidebarWidth.value,
    currentClientX: event.clientX,
    handle
  };
  handle.setPointerCapture(event.pointerId);
  isTaskSidebarResizing.value = true;
  document.documentElement.classList.add("is-task-sidebar-resizing");
}

function handleTaskSidebarResizeMove(event: PointerEvent) {
  const state = taskSidebarResizeState;
  if (state?.pointerId !== event.pointerId) {
    return;
  }
  state.currentClientX = event.clientX;
  const targetWidth = calculateTaskSidebarTargetWidth({
    startWidth: state.startWidth,
    startClientX: state.startClientX,
    clientX: state.currentClientX
  });
  if (shouldCollapseTaskSidebarFromDrag(targetWidth)) {
    finishTaskSidebarResize(event);
    return;
  }
  if (taskSidebarResizeAnimationFrame !== undefined) {
    return;
  }
  taskSidebarResizeAnimationFrame = window.requestAnimationFrame(() => {
    taskSidebarResizeAnimationFrame = undefined;
    if (!taskSidebarResizeState) {
      return;
    }
    taskSidebarWidth.value = calculateTaskSidebarWidth({
      startWidth: taskSidebarResizeState.startWidth,
      startClientX: taskSidebarResizeState.startClientX,
      clientX: taskSidebarResizeState.currentClientX
    });
  });
}

function finishTaskSidebarResize(event?: PointerEvent) {
  if (event && taskSidebarResizeState?.pointerId !== event.pointerId) {
    return;
  }
  const state = taskSidebarResizeState;
  if (!state) {
    return;
  }
  if (taskSidebarResizeAnimationFrame !== undefined) {
    window.cancelAnimationFrame(taskSidebarResizeAnimationFrame);
    taskSidebarResizeAnimationFrame = undefined;
  }
  if (event) {
    state.currentClientX = event.clientX;
  }
  const targetWidth = calculateTaskSidebarTargetWidth({
    startWidth: state.startWidth,
    startClientX: state.startClientX,
    clientX: state.currentClientX
  });
  if (state.handle.hasPointerCapture(state.pointerId)) {
    state.handle.releasePointerCapture(state.pointerId);
  }
  taskSidebarResizeState = undefined;
  isTaskSidebarResizing.value = false;
  document.documentElement.classList.remove("is-task-sidebar-resizing");

  taskSidebarWidth.value = calculateTaskSidebarWidth({
    startWidth: state.startWidth,
    startClientX: state.startClientX,
    clientX: state.currentClientX
  });
  writeStoredTaskSidebarWidth(window.localStorage, taskSidebarWidth.value);
  if (shouldCollapseTaskSidebarFromDrag(targetWidth)) {
    collapseTaskSidebarWithAnimation();
  }
}

function handleTaskSidebarResizeKeydown(event: KeyboardEvent) {
  const keyDeltas: Record<string, number> = {
    ArrowLeft: 16,
    ArrowRight: -16
  };
  if (!(event.key in keyDeltas) && event.key !== "Home" && event.key !== "End") {
    return;
  }
  event.preventDefault();
  if (event.key === "Home") {
    taskSidebarWidth.value = TASK_SIDEBAR_MIN_WIDTH;
  } else if (event.key === "End") {
    taskSidebarWidth.value = TASK_SIDEBAR_MAX_WIDTH;
  } else {
    taskSidebarWidth.value = calculateTaskSidebarWidth({
      startWidth: taskSidebarWidth.value,
      startClientX: 0,
      clientX: -keyDeltas[event.key]
    });
  }
  writeStoredTaskSidebarWidth(window.localStorage, taskSidebarWidth.value);
}
</script>
